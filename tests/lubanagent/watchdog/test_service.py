"""Watchdog 服务验收：假时钟确定性巡检 / 防抖 / 处置器 / API 集成 / 默认关。"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from lubanagent.core import RunStatus, TraceEventType
from lubanagent.persistence import RunRepository, TraceRepository
from lubanagent.watchdog import CancelHandler, WatchdogService

from .conftest import NOW, make_clock, minutes, seed_agent, seed_run


def make_service(db, settings, *, clock=None, action_handler=None) -> WatchdogService:
    return WatchdogService(
        db=db,
        settings=settings,
        action_handler=action_handler,
        clock=clock or make_clock(NOW),
    )


async def test_stale_events_alert(db, settings):
    """RUNNING Run 末条事件距今超阈值 → stale_events 告警落库。"""
    await seed_agent(db)
    await seed_run(
        db, "run-1", started_at=minutes(10), last_event_at=minutes(9)
    )  # 末条事件距今 9 分钟 > 300s
    service = make_service(db, settings)
    alerts = await service.check_once()
    assert [a.reason for a in alerts] == ["stale_events"]
    assert alerts[0].run_id == "run-1"
    assert alerts[0].age_seconds == pytest.approx(9 * 60)
    # WATCHDOG_ALERT 事件落目标 Run 的 trace
    async with db.session() as session:
        events = await TraceRepository(session).list_events("run-1")
    watchdog_events = [e for e in events if e.type is TraceEventType.WATCHDOG_ALERT]
    assert len(watchdog_events) == 1
    payload = watchdog_events[0].payload
    assert payload["action"] == "alert"
    assert payload["alert_id"] == alerts[0].alert_id
    assert payload["last_event_at"] is not None


async def test_fresh_run_no_alert(db, settings):
    """事件新鲜（2 分钟前 < 300s）→ 无告警。"""
    await seed_agent(db)
    await seed_run(db, "run-1", started_at=minutes(1), last_event_at=minutes(1))
    service = make_service(db, settings)
    assert await service.check_once() == []


async def test_waiting_approval_exempt(db, settings):
    """WAITING_APPROVAL Run 即使事件陈旧也不告警（status 过滤）。"""
    await seed_agent(db)
    await seed_run(
        db,
        "run-1",
        status=RunStatus.WAITING_APPROVAL,
        started_at=minutes(30),
        last_event_at=minutes(29),
    )
    service = make_service(db, settings)
    assert await service.check_once() == []


async def test_long_running_alert(db, settings):
    """事件新鲜但运行超 max_run_seconds → long_running。"""
    await seed_agent(db)
    await seed_run(
        db, "run-1", started_at=minutes(90), last_event_at=minutes(1)
    )  # 事件新鲜，运行 90 分钟 > 3600s
    service = make_service(db, settings)
    alerts = await service.check_once()
    assert [a.reason for a in alerts] == ["long_running"]
    assert alerts[0].last_event_at is not None


async def test_no_events_counts_from_started_at(db, settings):
    """空事件流：stale 判据按 started_at 计。"""
    await seed_agent(db)
    await seed_run(db, "run-1", started_at=minutes(10), last_event_at=None)
    service = make_service(db, settings)
    alerts = await service.check_once()
    assert [a.reason for a in alerts] == ["stale_events"]
    assert alerts[0].last_event_at is None


async def test_debounce_and_reset(db, settings):
    """同 run 同 reason 只告警一次；Run 非 RUNNING 清记录、时钟推进后可再次告警。

    首轮告警的 WATCHDOG_ALERT 事件本身会成为 trace 末条事件（时间=告警时刻），
    故重置后需推进时钟超过 stale 阈值才构成二次 stale——这正是生产行为。
    """
    await seed_agent(db)
    await seed_run(db, "run-1", started_at=minutes(10), last_event_at=minutes(9))
    current = {"now": NOW}
    service = make_service(db, settings, clock=make_clock(current["now"]))
    # 可推进时钟：后面把 now 拨到未来
    service._clock = lambda: current["now"]
    first = await service.check_once()
    assert len(first) == 1
    assert await service.check_once() == []  # 防抖
    # Run 完成 → 清防抖记录
    async with db.session() as session:
        repo = RunRepository(session)
        run = await repo.get_run("run-1")
        run.status = RunStatus.COMPLETED
        await repo.update_run(run)
    assert await service.check_once() == []
    # 重新置 RUNNING + 时钟推进（首轮告警事件已变陈旧）→ 再次告警
    async with db.session() as session:
        repo = RunRepository(session)
        run = await repo.get_run("run-1")
        run.status = RunStatus.RUNNING
        await repo.update_run(run)
    current["now"] = NOW + timedelta(seconds=400)
    again = await service.check_once()
    assert [a.reason for a in again] == ["stale_events"]


async def test_cancel_handler_invokes_cancel_fn(db, settings):
    """CancelHandler 走注入回调，动作名进事件 payload。"""
    await seed_agent(db)
    await seed_run(db, "run-1", started_at=minutes(10), last_event_at=minutes(9))
    cancelled: list[str] = []

    async def cancel_fn(run_id: str) -> bool:
        cancelled.append(run_id)
        return True

    service = make_service(db, settings, action_handler=CancelHandler(cancel_fn))
    await service.check_once()
    assert cancelled == ["run-1"]
    async with db.session() as session:
        events = await TraceRepository(session).list_events("run-1")
    payload = next(
        e for e in events if e.type is TraceEventType.WATCHDOG_ALERT
    ).payload
    assert payload["action"] == "cancel"


async def test_recent_alerts_deque(db, settings):
    await seed_agent(db)
    await seed_run(db, "run-1", started_at=minutes(10), last_event_at=minutes(9))
    service = make_service(db, settings)
    await service.check_once()
    assert [a.run_id for a in service.recent_alerts()] == ["run-1"]


async def test_api_integration_alerts_endpoint(db, registry, tmp_path):
    """create_app 集成：watchdog 开启 + lifespan 起巡检 → GET /v1/watchdog/alerts 非空。"""
    from lubanagent.app import create_app, seed_defaults
    from lubanagent.config import Settings

    settings = Settings(
        auth_enabled=False,
        observability_backend="none",
        env="dev",
        watchdog_enabled=True,
        watchdog_interval_seconds=0.05,
        watchdog_stale_after_seconds=1.0,
    )
    await seed_agent(db)
    # 用真实时钟种旧 Run（工厂里的服务用真实时钟；conftest NOW 是测试用假时钟，勿混用）
    real_now = datetime.now(UTC)
    await seed_run(
        db,
        "run-zombie",
        started_at=real_now - timedelta(minutes=10),
        last_event_at=real_now - timedelta(minutes=9),
    )
    application = create_app(settings=settings, registry=registry, db=db)
    await seed_defaults(db)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            await asyncio.sleep(0.2)  # 等 2-3 轮巡检
            response = await client.get("/v1/watchdog/alerts")
            assert response.status_code == 200
            alerts = response.json()
            assert any(a["run_id"] == "run-zombie" for a in alerts)
            assert alerts[0]["reason"] == "stale_events"
    # lifespan 退出后巡检 task 收敛（无悬挂告警）
    await asyncio.sleep(0)


async def test_disabled_by_default_no_service(db, registry):
    """默认关 → app.state 无 watchdog，告警端点恒空。"""
    from lubanagent.app import create_app, seed_defaults
    from lubanagent.config import Settings

    settings = Settings(auth_enabled=False, observability_backend="none", env="dev")
    application = create_app(settings=settings, registry=registry, db=db)
    await seed_defaults(db)
    async with application.router.lifespan_context(application):
        assert not hasattr(application.state, "watchdog")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.get("/v1/watchdog/alerts")
            assert response.status_code == 200
            assert response.json() == []


async def test_run_forever_survives_exceptions(db, settings, monkeypatch):
    """巡检循环异常只记日志不退出（外部 Control Loop 自己不能先死）。"""
    await seed_agent(db)
    service = make_service(db, settings)
    calls = {"n": 0}

    async def flaky_check():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        raise asyncio.CancelledError  # 第三轮用取消退出循环

    monkeypatch.setattr(service, "check_once", flaky_check)
    settings.watchdog_interval_seconds = 0.01
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(service.run_forever(), timeout=2)
    assert calls["n"] == 3
