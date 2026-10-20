"""export_failures 重试消费端（R4）：drain 时回放待重试留痕——
成功删记录 / 失败计尝试 / 超限放弃；以及同步上下文失败留痕落库（R3）。"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from lubanagent.core import BrainProfile, Run, RunStatus, TraceEvent, TraceEventType
from lubanagent.observability import SafeBackend, db_failure_sink
from lubanagent.persistence import Database, ExportFailureRepository

T0 = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def make_run() -> Run:
    return Run(
        id="run-9",
        agent_key="demo",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        created_at=T0,
    )


def make_event() -> TraceEvent:
    return TraceEvent(
        id="e1",
        run_id="run-9",
        type=TraceEventType.MODEL_CALL_COMPLETED,
        payload={"input_tokens": 1},
        created_at=T0,
    )


class FlakyBackend:
    """可开关的桩后端：failing=True 时 emit/replay 必炸，恢复后 replay 成功；记录回放参数。"""

    def __init__(self) -> None:
        self.failing = True
        self.replayed: list[tuple[str, str, dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return "flaky"

    def start_trace(self, run: Run) -> object:
        return object()

    def emit(self, handle: Any, event: TraceEvent) -> None:
        if self.failing:
            raise ConnectionError("flaky down")

    def end_trace(
        self, handle: Any, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        return None

    def replay(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.replayed.append((run_id, event_type, payload))
        if self.failing:
            raise ConnectionError("flaky down")


class ExplodingNoReplayBackend:
    """必炸且不实现 replay 的自定义后端：留痕保留、不计数、不自动重试。"""

    def __init__(self) -> None:
        self.replayed: list[tuple[str, str, dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return "no-replay"

    def start_trace(self, run: Run) -> object:
        return object()

    def emit(self, handle: Any, event: TraceEvent) -> None:
        raise ConnectionError("boom")

    def end_trace(
        self, handle: Any, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        return None


def wire(db: Database, inner: FlakyBackend, **kwargs: Any) -> SafeBackend:
    backend = SafeBackend(inner, sink=db_failure_sink(db), **kwargs)
    backend.set_retry_db(db)
    return backend


async def test_retry_success_deletes_record(db: Database) -> None:
    inner = FlakyBackend()
    backend = wire(db, inner)
    handle = backend.start_trace(make_run())
    backend.emit(handle, make_event())  # 外发失败 → 落留痕
    await backend.drain()  # 同一轮 drain：落库 + 第一次重试（仍失败，计 1 次）

    async with db.session() as session:
        pending = await ExportFailureRepository(session).list_pending()
    assert len(pending) == 1
    assert pending[0]["retry_count"] == 1
    assert pending[0]["retried_at"] is not None

    inner.failing = False  # 后端恢复
    await backend.drain()  # 下一轮 drain 重试成功 → 删记录

    async with db.session() as session:
        repo = ExportFailureRepository(session)
        assert await repo.list_pending() == []
        assert await repo.list_given_up() == []
    expected = ("run-9", "model_call_completed", {"input_tokens": 1})
    assert inner.replayed == [expected, expected]  # 第一次失败尝试 + 恢复后成功回放


async def test_retry_gives_up_after_max_attempts(db: Database) -> None:
    inner = FlakyBackend()  # 持续失败
    backend = wire(db, inner, max_attempts=3)
    handle = backend.start_trace(make_run())
    backend.emit(handle, make_event())
    for _ in range(4):  # 多 drain 一轮验证放弃后不再尝试
        await backend.drain()

    async with db.session() as session:
        repo = ExportFailureRepository(session)
        assert await repo.list_pending() == []  # 达上限，不再待重试
        given_up = await repo.list_given_up()
    assert len(given_up) == 1  # 行保留供运维查询（可观测途径）
    assert given_up[0]["retry_count"] == 3
    assert given_up[0]["retried_at"] is not None
    assert given_up[0]["error"]  # 原始失败原因可查
    assert len(inner.replayed) == 3  # 原始外发失败不计，恰重试 3 次


async def test_backend_without_replay_keeps_record_unattempted(db: Database) -> None:
    inner = ExplodingNoReplayBackend()
    backend = wire(db, inner)
    handle = backend.start_trace(make_run())
    backend.emit(handle, make_event())
    await backend.drain()
    await backend.drain()  # 再来一轮：无 replay 能力，不计数不删除

    async with db.session() as session:
        pending = await ExportFailureRepository(session).list_pending()
    assert len(pending) == 1
    assert pending[0]["retry_count"] == 0  # 未计数（能力缺失 ≠ 尝试失败）
    assert inner.replayed == []


async def test_sync_context_failure_persisted_via_buffer(db: Database) -> None:
    """R3：无运行 loop 的同步上下文失败先入缓冲，drain 时落 export_failures。"""
    inner = FlakyBackend()
    backend = SafeBackend(inner, sink=db_failure_sink(db))  # 不挂重试，聚焦留痕落库
    handle = backend.start_trace(make_run())
    await asyncio.to_thread(backend.emit, handle, make_event())  # 工作线程内无运行 loop

    await backend.drain()  # 下次异步机会：缓冲落库

    async with db.session() as session:
        pending = await ExportFailureRepository(session).list_pending()
    assert len(pending) == 1
    assert pending[0]["backend"] == "flaky"
    assert pending[0]["run_id"] == "run-9"
    assert pending[0]["event_type"] == "model_call_completed"
    assert pending[0]["payload"] == {"input_tokens": 1}
    assert "flaky down" in pending[0]["error"]
