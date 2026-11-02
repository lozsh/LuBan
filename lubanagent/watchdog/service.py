"""Watchdog 服务：周期巡检 RUNNING Run 的进展信号，异常时告警并可插拔处置。

判据只用现成仓储接口（RunRepository.list_runs / TraceRepository.list_events）：
- stale_events：RUNNING Run 的末条 trace 事件距今超阈值（空事件流按 started_at 计）
- long_running：RUNNING Run 的 started_at 距今超 max_run_seconds
不用 boundary.timeout_seconds——那是 BudgetGuard 的主循环内职责；watchdog 抓的是
主循环自己死了（进程重启僵尸 RUNNING、事件流断流、死锁），这些场景 guard 已不存在。

确定性：clock 可注入（测试给固定时钟，不起真实 sleep）；
check_once 可脱离巡检循环直接调用。防抖：同 run 同 reason 只告警一次，
发现该 Run 已非 RUNNING 即清记录（之后再次 stale 可重新告警）。
"""

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from lubanagent.config.settings import Settings
from lubanagent.core import RunStatus, TraceEvent, TraceEventType
from lubanagent.observability.trace.writer import TraceWriter
from lubanagent.persistence import Database
from lubanagent.persistence.repositories.run import RunRepository
from lubanagent.persistence.repositories.trace import TraceRepository

logger = logging.getLogger(__name__)

_ALERT_DEQUE_MAX = 100


@dataclass(frozen=True)
class WatchdogAlert:
    """一次告警；alert_id 确定性场景下可注入 clock 后仍唯一（uuid）。"""

    alert_id: str
    run_id: str
    reason: Literal["stale_events", "long_running"]
    last_event_at: datetime | None
    age_seconds: float
    created_at: datetime


class WatchdogActionHandler(Protocol):
    """告警处置协议（默认 AlertOnlyHandler 只留痕；可插拔自动处置如 cancel）。"""

    async def handle(self, alert: WatchdogAlert) -> str:
        """执行处置并返回动作名（留痕进 WATCHDOG_ALERT 事件 payload）。"""
        ...


class AlertOnlyHandler:
    """默认处置：只告警不动 Run。"""

    async def handle(self, alert: WatchdogAlert) -> str:
        return "alert"


class CancelHandler:
    """内置可选：对告警 Run 执行取消（cancel_fn 由装配根绑定 runtime.cancel）。

    经注入回调而非 import runtime——watchdog 包不依赖运行时（importlinter 契约）。
    """

    def __init__(self, cancel_fn: Callable[[str], Awaitable[bool]]) -> None:
        self._cancel_fn = cancel_fn

    async def handle(self, alert: WatchdogAlert) -> str:
        try:
            await self._cancel_fn(alert.run_id)
        except Exception:
            logger.exception("Watchdog 取消 Run %s 失败", alert.run_id)
            return "cancel_failed"
        return "cancel"


class WatchdogService:
    """定时巡检服务；lifespan 起 run_forever task，测试直接调 check_once。"""

    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        action_handler: WatchdogActionHandler | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._handler = action_handler or AlertOnlyHandler()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._writer = TraceWriter(db)
        self._recent: deque[WatchdogAlert] = deque(maxlen=_ALERT_DEQUE_MAX)
        # 防抖：run_id -> 已告警 reason 集合
        self._alerted: dict[str, set[str]] = {}
        self._closed = False

    async def check_once(self) -> list[WatchdogAlert]:
        """单次巡检：返回本轮新产生的告警（同时落 WATCHDOG_ALERT 事件 + 内存留痕）。"""
        now = self._clock()
        grace_seconds = self._settings.watchdog_interval_seconds * 2
        alerts: list[WatchdogAlert] = []
        async with self._db.session() as session:
            runs = await RunRepository(session).list_runs(limit=200)
            repo = TraceRepository(session)
            for run in runs:
                if run.status != RunStatus.RUNNING:
                    self._alerted.pop(run.id, None)  # 非 RUNNING 清防抖记录
                    continue
                if run.started_at is None:
                    continue
                started_at = _aware(run.started_at)
                run_age = (now - started_at).total_seconds()
                if run_age < grace_seconds:
                    continue  # 新 Run 宽限期内不告警
                reasons: list[Literal["stale_events", "long_running"]] = []
                last_event_at: datetime | None = None
                event_age: float | None = None
                events = await repo.list_events(run.id)
                if events:
                    last_event_at = _aware(events[-1].created_at)
                    event_age = (now - last_event_at).total_seconds()
                    if event_age > self._settings.watchdog_stale_after_seconds:
                        reasons.append("stale_events")
                else:
                    if run_age > self._settings.watchdog_stale_after_seconds:
                        reasons.append("stale_events")
                if run_age > self._settings.watchdog_max_run_seconds:
                    reasons.append("long_running")
                for reason in reasons:
                    if reason in self._alerted.get(run.id, set()):
                        continue  # 同 run 同 reason 只告警一次
                    alert = WatchdogAlert(
                        alert_id=uuid.uuid4().hex,
                        run_id=run.id,
                        reason=reason,
                        last_event_at=last_event_at,
                        age_seconds=event_age if reason == "stale_events" else run_age,
                        created_at=now,
                    )
                    self._alerted.setdefault(run.id, set()).add(reason)
                    await self._emit_alert(alert)
                    alerts.append(alert)
        return alerts

    async def run_forever(self) -> None:
        """巡检循环：异常只记日志不退出（外部 Control Loop 自己不能先死）。"""
        interval = self._settings.watchdog_interval_seconds
        while not self._closed:
            try:
                await self.check_once()
            except Exception:
                logger.exception("Watchdog 巡检失败（下一轮重试）")
            await asyncio.sleep(interval)

    async def aclose(self) -> None:
        self._closed = True

    def recent_alerts(self) -> list[WatchdogAlert]:
        """内存留痕（进程内），供 GET /v1/watchdog/alerts 查询。"""
        return list(self._recent)

    async def _emit_alert(self, alert: WatchdogAlert) -> None:
        try:
            action = await self._handler.handle(alert)
        except Exception:
            logger.exception("Watchdog 处置器执行失败（Run %s）", alert.run_id)
            action = "handler_error"
        self._recent.append(alert)
        event = TraceEvent(
            id=alert.alert_id,
            run_id=alert.run_id,
            type=TraceEventType.WATCHDOG_ALERT,
            payload={
                "reason": alert.reason,
                "age_seconds": alert.age_seconds,
                "last_event_at": alert.last_event_at.isoformat()
                if alert.last_event_at
                else None,
                "action": action,
                "alert_id": alert.alert_id,
            },
            created_at=alert.created_at,
        )
        try:
            await self._writer.emit(event)
        except Exception:
            # 事件落库失败不吞告警：内存留痕已记，日志兜底（僵尸 trace 场景）
            logger.exception("Watchdog 告警事件落库失败（Run %s）", alert.run_id)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


__all__ = [
    "AlertOnlyHandler",
    "CancelHandler",
    "WatchdogActionHandler",
    "WatchdogAlert",
    "WatchdogService",
]
