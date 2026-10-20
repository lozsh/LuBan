"""Langfuse 适配器：桩 SDK 断言翻译序列 + SDK 缺失/key 缺失降级为 noop。"""

import logging
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from lubanagent.core import (
    BrainProfile,
    Run,
    RunStatus,
    TraceEvent,
    TraceEventType,
)
from lubanagent.observability import LangfuseBackend
from lubanagent.observability.adapters.noop import NoneHandle

T0 = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def make_run() -> Run:
    return Run(
        id="run-1",
        agent_key="cs-bot",
        agent_version=2,
        session_id="sess-1",
        input={"question": "hi"},
        brain_profile=BrainProfile.SMART,
        created_at=T0,
    )


def make_event(
    event_type: TraceEventType, *, step_index: int | None = None, payload: dict | None = None
) -> TraceEvent:
    return TraceEvent(
        id=f"e-{event_type.value}",
        run_id="run-1",
        type=event_type,
        step_index=step_index,
        payload=payload or {},
        created_at=T0,
    )


class _Recorder:
    def __init__(self, calls: list, label: str) -> None:
        self._calls = calls
        self._label = label

    def _record(self, action: str, kwargs: dict) -> None:
        self._calls.append((self._label, action, kwargs))


class FakeGeneration(_Recorder):
    def end(self, **kwargs: Any) -> None:
        self._record("end", kwargs)


class FakeSpan(_Recorder):
    def end(self, **kwargs: Any) -> None:
        self._record("end", kwargs)


class FakeTrace(_Recorder):
    def generation(self, **kwargs: Any) -> FakeGeneration:
        self._record("generation", kwargs)
        return FakeGeneration(self._calls, "generation")

    def span(self, **kwargs: Any) -> FakeSpan:
        self._record("span", kwargs)
        return FakeSpan(self._calls, "span")

    def update(self, **kwargs: Any) -> None:
        self._record("update", kwargs)


class FakeLangfuseClient:
    def __init__(self, calls: list, **kwargs: Any) -> None:
        self._calls = calls
        calls.append(("client", "init", kwargs))

    def trace(self, **kwargs: Any) -> FakeTrace:
        self._calls.append(("client", "trace", kwargs))
        return FakeTrace(self._calls, "trace")

    def event(self, **kwargs: Any) -> None:
        self._calls.append(("client", "event", kwargs))

    def flush(self) -> None:
        self._calls.append(("client", "flush", {}))


def install_stub(monkeypatch) -> list:
    calls: list = []

    def _factory(**kwargs: Any) -> FakeLangfuseClient:
        return FakeLangfuseClient(calls, **kwargs)

    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=_factory))
    return calls


def make_backend() -> LangfuseBackend:
    return LangfuseBackend(host="http://lf:3000", public_key="pk", secret_key="sk")


def test_translation_sequence(monkeypatch) -> None:
    calls = install_stub(monkeypatch)
    backend = make_backend()

    handle = backend.start_trace(make_run())
    backend.emit(
        handle,
        make_event(
            TraceEventType.MODEL_CALL_STARTED,
            step_index=0,
            payload={
                "model_key": "deepseek-chat",
                "input": [{"role": "system", "content": "你是客服"}],
            },
        ),
    )
    backend.emit(
        handle,
        make_event(
            TraceEventType.MODEL_CALL_COMPLETED,
            step_index=0,
            payload={
                "model_key": "deepseek-chat",
                "text": "answer",
                "tool_calls": [],
                "input_tokens": 10,
                "output_tokens": 5,
                "usage_known": True,
            },
        ),
    )
    # usage 未知（usage_known=False）：不把未知报成 0
    backend.emit(
        handle,
        make_event(
            TraceEventType.MODEL_CALL_COMPLETED,
            step_index=3,
            payload={
                "model_key": "deepseek-chat",
                "text": "续答",
                "tool_calls": [],
                "input_tokens": None,
                "output_tokens": None,
                "usage_known": False,
            },
        ),
    )
    backend.emit(
        handle,
        make_event(
            TraceEventType.TOOL_CALL_STARTED,
            step_index=1,
            payload={"tool": "refund.query", "args": {"order_id": "A1"}},
        ),
    )
    backend.emit(
        handle,
        make_event(
            TraceEventType.TOOL_CALL_COMPLETED,
            step_index=1,
            payload={"tool": "refund.query", "result": {"ok": True}, "duration_ms": 12},
        ),
    )
    backend.emit(
        handle,
        make_event(
            TraceEventType.TOOL_CALL_FAILED,
            step_index=2,
            payload={"tool": "refund.query", "error": "timeout"},
        ),
    )
    backend.emit(handle, make_event(TraceEventType.BUDGET_WARNING, payload={"ratio": 0.8}))
    backend.end_trace(
        handle,
        RunStatus.COMPLETED,
        {"end_reason": "final_answer", "token_usage": {"total": 15}, "cost": 0.01},
    )

    # start_trace → client 构造 + trace(name=agent_key, metadata 三要素)
    assert ("client", "init", {
        "host": "http://lf:3000", "public_key": "pk", "secret_key": "sk"
    }) in calls
    assert ("client", "trace", {
        "name": "cs-bot",
        "metadata": {"agent_version": 2, "run_id": "run-1", "session_id": "sess-1"},
    }) in calls

    # MODEL_CALL_STARTED → generation；COMPLETED → generation.end(usage)（同约定读 payload 键）
    gen = next(c for c in calls if c[0] == "trace" and c[1] == "generation")
    assert gen[2]["model"] == "deepseek-chat"
    assert gen[2]["input"] == [{"role": "system", "content": "你是客服"}]
    gen_ends = [c for c in calls if c[0] == "generation" and c[1] == "end"]
    assert gen_ends[0][2]["usage"] == {"input": 10, "output": 5}
    assert gen_ends[0][2]["output"] == "answer"
    # usage 未知 → usage=None + metadata usage_unknown，不冒充 0
    unknown_end = gen_ends[1]
    assert unknown_end[2]["usage"] is None
    assert unknown_end[2]["metadata"]["usage_unknown"] is True
    assert unknown_end[2]["output"] == "续答"

    # TOOL_CALL_STARTED → span；COMPLETED → span.end；FAILED → span.end(level=ERROR)
    span = next(c for c in calls if c[0] == "trace" and c[1] == "span")
    assert span[2]["name"] == "refund.query"
    assert span[2]["input"] == {"order_id": "A1"}
    span_ends = [c for c in calls if c[0] == "span" and c[1] == "end"]
    assert span_ends[0][2] == {"output": {"ok": True}}
    assert span_ends[1][2] == {"output": "timeout", "level": "ERROR"}

    # 其余事件 → client.event(name=event.type.value, payload)
    evt = next(c for c in calls if c[0] == "client" and c[1] == "event")
    assert evt[2]["name"] == "budget_warning"
    assert evt[2]["input"] == {"ratio": 0.8}

    # end_trace → trace.update(status/end_reason/usage/cost) + flush
    update = next(c for c in calls if c[0] == "trace" and c[1] == "update")
    assert update[2] == {
        "status": "completed",
        "end_reason": "final_answer",
        "usage": {"total": 15},
        "cost": 0.01,
    }
    assert calls[-1] == ("client", "flush", {})


def test_sdk_missing_degrades_to_noop(monkeypatch, caplog) -> None:
    monkeypatch.setitem(sys.modules, "langfuse", None)  # import 必 ImportError
    backend = make_backend()
    with caplog.at_level(logging.WARNING):
        handle = backend.start_trace(make_run())  # 不抛
        backend.emit(handle, make_event(TraceEventType.BUDGET_WARNING))  # 静默
        backend.end_trace(handle, RunStatus.COMPLETED)  # 静默
    assert isinstance(handle, NoneHandle)
    assert any("langfuse SDK 未安装" in r.message for r in caplog.records)


def test_missing_keys_degrades_to_noop(caplog) -> None:
    backend = LangfuseBackend(host="http://lf:3000", public_key=None, secret_key=None)
    with caplog.at_level(logging.WARNING):
        handle = backend.start_trace(make_run())
    assert isinstance(handle, NoneHandle)
    assert any("langfuse key 缺失" in r.message for r in caplog.records)


def test_backend_name() -> None:
    assert make_backend().name == "langfuse"


def test_replay_sends_generic_event(monkeypatch) -> None:
    """回放：无原句柄语境下按通用 event 重发，run_id/replayed 进 metadata。"""
    calls = install_stub(monkeypatch)
    backend = make_backend()
    backend.replay("run-9", "model_call_completed", {"input_tokens": 1})
    assert ("client", "event", {
        "name": "model_call_completed",
        "input": {"input_tokens": 1},
        "metadata": {"run_id": "run-9", "replayed": True},
    }) in calls


def test_replay_degraded_raises(monkeypatch) -> None:
    """已降级 noop（SDK 缺失）时回放抛错：留痕消费端计失败尝试而非静默丢失。"""
    monkeypatch.setitem(sys.modules, "langfuse", None)  # import 必 ImportError
    backend = make_backend()
    try:
        backend.replay("run-9", "model_call_started", {})
    except RuntimeError as exc:
        assert "降级" in str(exc)
    else:
        raise AssertionError("降级状态下 replay 应抛 RuntimeError")
