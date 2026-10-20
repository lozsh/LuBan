"""LangSmith 适配器：桩 RunTree 断言翻译序列 + SDK 缺失/key 缺失降级为 noop。"""

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
from lubanagent.observability import LangSmithBackend
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


class FakeRunTree:
    """桩 RunTree：记录 init/create_child/post/end/patch 调用序列。"""

    def __init__(self, calls: list | None = None, label: str = "root", **kwargs: Any) -> None:
        self._calls = calls if calls is not None else []
        self._label = label
        self._calls.append((label, "init", kwargs))

    def create_child(self, **kwargs: Any) -> "FakeRunTree":
        self._calls.append((self._label, "create_child", kwargs))
        return FakeRunTree(self._calls, label=f"child:{kwargs.get('name')}", **kwargs)

    def post(self) -> None:
        self._calls.append((self._label, "post", {}))

    def end(self, **kwargs: Any) -> None:
        self._calls.append((self._label, "end", kwargs))

    def patch(self) -> None:
        self._calls.append((self._label, "patch", {}))


def install_stub(monkeypatch) -> list:
    calls: list = []

    def _factory(**kwargs: Any) -> FakeRunTree:
        return FakeRunTree(calls, **kwargs)

    monkeypatch.setitem(sys.modules, "langsmith", SimpleNamespace(RunTree=_factory))
    return calls


def make_backend() -> LangSmithBackend:
    return LangSmithBackend(api_key="ls-key", project="proj-1")


def test_translation_sequence(monkeypatch) -> None:
    calls = install_stub(monkeypatch)
    backend = make_backend()

    handle = backend.start_trace(make_run())
    backend.emit(
        handle,
        make_event(
            TraceEventType.MODEL_CALL_STARTED,
            step_index=0,
            payload={"model_key": "deepseek-chat", "input": "prompt"},
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
        {"end_reason": "final_answer", "cost": 0.01},
    )

    # start_trace → 根 RunTree(chain) + post
    root_init = calls[0]
    assert root_init[0] == "root"
    assert root_init[1] == "init"
    assert root_init[2]["name"] == "cs-bot"
    assert root_init[2]["run_type"] == "chain"
    assert root_init[2]["inputs"] == {"question": "hi"}
    assert root_init[2]["project_name"] == "proj-1"
    assert root_init[2]["extra"]["metadata"] == {
        "agent_version": 2,
        "run_id": "run-1",
        "session_id": "sess-1",
    }
    assert ("root", "post", {}) in calls

    # MODEL_CALL_STARTED → create_child(llm) + post；COMPLETED → end(usage) + patch
    llm_child = next(
        c for c in calls if c[1] == "create_child" and c[2].get("run_type") == "llm"
    )
    assert llm_child[2]["name"] == "deepseek-chat"
    llm_ends = [c for c in calls if c[0] == "child:deepseek-chat" and c[1] == "end"]
    assert llm_ends[0][2]["outputs"] == {"output": "answer"}
    assert llm_ends[0][2]["extra"]["usage"] == {"input_tokens": 10, "output_tokens": 5}
    # usage 未知 → 不传 usage，metadata 标注 usage_unknown
    assert llm_ends[1][2]["outputs"] == {"output": "续答"}
    assert "usage" not in llm_ends[1][2]["extra"]
    assert llm_ends[1][2]["extra"]["metadata"] == {"usage_unknown": True}
    assert ("child:deepseek-chat", "patch", {}) in calls

    # TOOL_CALL_STARTED → create_child(tool)；COMPLETED → end(outputs)；FAILED → end(error)
    tool_children = [
        c for c in calls if c[1] == "create_child" and c[2].get("run_type") == "tool"
    ]
    assert len(tool_children) == 2
    assert tool_children[0][2]["inputs"] == {"input": {"order_id": "A1"}}
    tool_ends = [c for c in calls if c[0] == "child:refund.query" and c[1] == "end"]
    assert tool_ends[0][2] == {"outputs": {"output": {"ok": True}}}
    assert tool_ends[1][2] == {"error": "timeout"}

    # 其余事件 → chain child 立即 end + post
    other = next(
        c for c in calls if c[1] == "create_child" and c[2].get("name") == "budget_warning"
    )
    assert other[2]["run_type"] == "chain"
    assert other[2]["inputs"] == {"ratio": 0.8}
    assert ("child:budget_warning", "end", {}) in calls
    assert ("child:budget_warning", "post", {}) in calls

    # end_trace → root.end(outputs 含 status + summary) + patch
    assert calls[-2] == (
        "root",
        "end",
        {"outputs": {"status": "completed", "end_reason": "final_answer", "cost": 0.01}},
    )
    assert calls[-1] == ("root", "patch", {})


def test_end_trace_failed_uses_error(monkeypatch) -> None:
    calls = install_stub(monkeypatch)
    backend = make_backend()
    handle = backend.start_trace(make_run())
    backend.end_trace(handle, RunStatus.FAILED, {"error": "boom"})
    root_end = next(c for c in calls if c[0] == "root" and c[1] == "end")
    assert root_end[2] == {"error": "boom"}


def test_sdk_missing_degrades_to_noop(monkeypatch, caplog) -> None:
    monkeypatch.setitem(sys.modules, "langsmith", None)  # import 必 ImportError
    backend = make_backend()
    with caplog.at_level(logging.WARNING):
        handle = backend.start_trace(make_run())  # 不抛
        backend.emit(handle, make_event(TraceEventType.BUDGET_WARNING))  # 静默
        backend.end_trace(handle, RunStatus.COMPLETED)  # 静默
    assert isinstance(handle, NoneHandle)
    assert any("langsmith SDK 未安装" in r.message for r in caplog.records)


def test_missing_key_degrades_to_noop(caplog) -> None:
    backend = LangSmithBackend(api_key=None)
    with caplog.at_level(logging.WARNING):
        handle = backend.start_trace(make_run())
    assert isinstance(handle, NoneHandle)
    assert any("langsmith api key 缺失" in r.message for r in caplog.records)


def test_backend_name() -> None:
    assert make_backend().name == "langsmith"


def test_replay_sends_standalone_run(monkeypatch) -> None:
    """回放：无原 RunTree 语境下按独立子 run 重发，run_id/replayed 进 metadata。"""
    calls = install_stub(monkeypatch)
    backend = make_backend()
    backend.replay("run-9", "model_call_completed", {"input_tokens": 1})
    assert ("root", "init", {
        "name": "model_call_completed",
        "run_type": "chain",
        "inputs": {"input_tokens": 1},
        "project_name": "proj-1",
        "extra": {"metadata": {"run_id": "run-9", "replayed": True}},
    }) in calls
    assert ("root", "end", {}) in calls
    assert ("root", "post", {}) in calls
