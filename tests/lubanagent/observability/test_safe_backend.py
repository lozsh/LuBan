"""SafeBackend：失败隔离（外发异常不阻断 Run）、sink 留痕、限流与 drain。"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from lubanagent.core import BrainProfile, Run, RunStatus, TraceEvent, TraceEventType
from lubanagent.observability.adapters.base import SafeBackend
from lubanagent.observability.adapters.noop import NoneHandle

T0 = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def make_run() -> Run:
    return Run(
        id="run-1",
        agent_key="demo",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        created_at=T0,
    )


def make_event(event_id: str = "e1") -> TraceEvent:
    return TraceEvent(
        id=event_id,
        run_id="run-1",
        type=TraceEventType.MODEL_CALL_STARTED,
        payload={"model_key": "m"},
        created_at=T0,
    )


class ExplodingBackend:
    """emit/end_trace 必炸的桩后端。"""

    def __init__(self) -> None:
        self.started = 0

    @property
    def name(self) -> str:
        return "exploding"

    def start_trace(self, run: Run) -> object:
        self.started += 1
        return object()

    def emit(self, handle: Any, event: TraceEvent) -> None:
        raise RuntimeError("boom")

    def end_trace(
        self, handle: Any, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        raise RuntimeError("boom-end")


class ExplodingStartBackend(ExplodingBackend):
    def start_trace(self, run: Run) -> object:
        raise RuntimeError("boom-start")


class RecordingBackend:
    """记录收到的 event/summary，供脱敏边界断言。"""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.summaries: list[dict[str, Any] | None] = []

    @property
    def name(self) -> str:
        return "recording"

    def start_trace(self, run: Run) -> object:
        return object()

    def emit(self, handle: Any, event: TraceEvent) -> None:
        self.events.append(event)

    def end_trace(
        self, handle: Any, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        self.summaries.append(summary)


async def test_safe_backend_redacts_payload_before_inner_and_source_untouched() -> None:
    inner = RecordingBackend()
    backend = SafeBackend(inner)
    handle = backend.start_trace(make_run())
    event = make_event("e-redact")
    event.payload["args"] = {"api_key": "sk-synthetic-secret", "order_id": "A-1"}
    backend.emit(handle, event)
    await backend.drain()
    assert len(inner.events) == 1
    # inner 收到脱敏副本
    assert inner.events[0].payload["args"]["api_key"] == "[REDACTED]"
    assert inner.events[0].payload["args"]["order_id"] == "A-1"
    # 调用方原事件不被修改
    assert event.payload["args"]["api_key"] == "sk-synthetic-secret"


async def test_safe_backend_redacts_summary_before_inner() -> None:
    inner = RecordingBackend()
    backend = SafeBackend(inner)
    handle = backend.start_trace(make_run())
    backend.end_trace(handle, RunStatus.COMPLETED, {"output": "ok", "token": "tok-synthetic"})
    await backend.drain()
    assert inner.summaries == [{"output": "ok", "token": "[REDACTED]"}]


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str, dict[str, Any], str]] = []

    async def __call__(
        self, backend: str, run_id: str, event_type: str, payload: dict[str, Any], error: str
    ) -> None:
        self.records.append((backend, run_id, event_type, payload, error))


async def test_emit_failure_isolated_and_sink_notified() -> None:
    sink = RecordingSink()
    backend = SafeBackend(ExplodingBackend(), sink=sink)
    handle = backend.start_trace(make_run())

    backend.emit(handle, make_event())  # 不抛
    await backend.drain()

    assert len(sink.records) == 1
    name, run_id, event_type, payload, error = sink.records[0]
    assert name == "exploding"
    assert run_id == "run-1"
    assert event_type == "model_call_started"
    assert payload == {"model_key": "m"}
    assert "boom" in error


async def test_end_trace_failure_goes_to_sink() -> None:
    sink = RecordingSink()
    backend = SafeBackend(ExplodingBackend(), sink=sink)
    handle = backend.start_trace(make_run())

    backend.end_trace(handle, RunStatus.COMPLETED, {"cost": 0.1})  # 不抛
    await backend.drain()

    assert len(sink.records) == 1
    assert sink.records[0][0] == "exploding"
    assert sink.records[0][2] == "end_trace"
    assert "boom-end" in sink.records[0][4]


async def test_start_trace_failure_returns_none_handle(caplog) -> None:
    backend = SafeBackend(ExplodingStartBackend())
    with caplog.at_level(logging.WARNING):
        handle = backend.start_trace(make_run())
    assert isinstance(handle, NoneHandle)


async def test_emit_without_sink_only_logs(caplog) -> None:
    backend = SafeBackend(ExplodingBackend())
    handle = backend.start_trace(make_run())
    with caplog.at_level(logging.WARNING):
        backend.emit(handle, make_event())
        await backend.drain()
    assert any("emit 失败" in r.message for r in caplog.records)


def test_emit_in_sync_context_falls_back_inline(caplog) -> None:
    """无运行 loop 的同步上下文：直接委托，异常不传播；失败入缓冲，下次异步机会落 sink（R3）。"""
    sink = RecordingSink()
    backend = SafeBackend(ExplodingBackend(), sink=sink)
    handle = backend.start_trace(make_run())
    with caplog.at_level(logging.WARNING):
        backend.emit(handle, make_event())  # 不抛
    assert sink.records == []  # 此刻无 loop 无法 await sink，先入内存缓冲
    assert any("同步上下文" in r.message for r in caplog.records)
    asyncio.run(backend.drain())  # 下次异步机会（drain）把缓冲落进 sink
    assert len(sink.records) == 1
    name, run_id, event_type, payload, error = sink.records[0]
    assert name == "exploding"
    assert run_id == "run-1"
    assert event_type == "model_call_started"
    assert payload == {"model_key": "m"}
    assert "boom" in error
    assert backend._inline_failures == []  # 缓冲已清空


class ConcurrencyProbeBackend:
    """记录 SafeBackend 并发峰值（读包装器的 inflight 计数）。"""

    def __init__(self) -> None:
        self.wrapper: SafeBackend | None = None
        self.peaks: list[int] = []
        self.calls = 0

    @property
    def name(self) -> str:
        return "probe"

    def start_trace(self, run: Run) -> object:
        return object()

    def emit(self, handle: Any, event: TraceEvent) -> None:
        assert self.wrapper is not None
        self.peaks.append(self.wrapper._inflight)
        self.calls += 1

    def end_trace(
        self, handle: Any, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        return None


async def test_emit_concurrency_limited_by_semaphore() -> None:
    inner = ConcurrencyProbeBackend()
    backend = SafeBackend(inner, max_concurrency=3)
    inner.wrapper = backend
    handle = backend.start_trace(make_run())

    for i in range(20):
        backend.emit(handle, make_event(f"e{i}"))
    await backend.drain()

    assert inner.calls == 20
    assert max(inner.peaks) == 3  # 限流信号量生效：峰值恰为 max_concurrency


async def test_drain_waits_for_in_flight() -> None:
    inner = ConcurrencyProbeBackend()
    backend = SafeBackend(inner, max_concurrency=2)
    inner.wrapper = backend
    handle = backend.start_trace(make_run())
    for i in range(5):
        backend.emit(handle, make_event(f"e{i}"))
    await backend.drain()
    assert inner.calls == 5
    assert backend._tasks == set()
