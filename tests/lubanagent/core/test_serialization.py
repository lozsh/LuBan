"""JSON 序列化往返无损: 契约「验收标准」第 3 条。"""

import json
from datetime import UTC, datetime

from lubanagent.core import (
    AgentDefinition,
    BrainConfig,
    BrainProfile,
    Budget,
    ModelDefinition,
    Run,
    RunStatus,
    TraceEvent,
    TraceEventType,
)

NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def _round_trip(model_cls, obj):  # type: ignore[no-untyped-def]
    payload = obj.model_dump(mode="json")
    text = json.dumps(payload)  # 必须可 JSON 序列化
    restored = model_cls.model_validate(json.loads(text))
    assert restored.model_dump(mode="json") == payload
    return restored


def test_agent_definition_round_trip() -> None:
    definition = AgentDefinition(
        key="cs-agent",
        name="客服",
        instructions="你是客服",
        prompt_variables={"brand": "Luban"},
        brain=BrainConfig(profile=BrainProfile.FAST, allow_run_override=True),
        tools=["refund", "order_query"],
        tool_signatures={"refund": "abc123"},
        boundary={"max_steps": 8, "budget": Budget(max_tokens=1000, max_cost=0.5)},
        io={"input_type": "json", "output_schema": {"type": "object"}},
        version=3,
    )
    restored = _round_trip(AgentDefinition, definition)
    assert restored.brain.profile is BrainProfile.FAST
    assert restored.boundary.budget is not None
    assert restored.boundary.budget.max_tokens == 1000
    assert restored.tool_signatures == {"refund": "abc123"}


def test_agent_definition_defaults() -> None:
    definition = AgentDefinition(key="a", name="A")
    assert definition.brain.profile is BrainProfile.SMART
    assert definition.memory.window_size == 20
    assert definition.knowledge.top_k == 5
    assert definition.io.input_type == "text"
    assert definition.version == 1
    assert definition.status.value == "draft"


def test_run_round_trip() -> None:
    run = Run(
        id="run-1",
        agent_key="cs-agent",
        agent_version=3,
        session_id="s-1",
        parent_run_id="run-0",
        status=RunStatus.COMPLETED,
        input={"text": "你好"},
        output="您好",
        brain_profile=BrainProfile.SMART,
        model_key="deepseek-chat",
        end_reason="final_answer",
        token_usage={"input_tokens": 10, "output_tokens": 20, "model_calls": 2},
        cost=0.001,
        error=None,
        created_at=NOW,
        started_at=NOW,
        ended_at=NOW,
    )
    restored = _round_trip(Run, run)
    assert restored.status is RunStatus.COMPLETED
    assert restored.end_reason is not None and restored.end_reason.value == "final_answer"
    assert restored.token_usage.total == 30


def test_run_with_error_dict_round_trip() -> None:
    error = {
        "code": "BUDGET_EXCEEDED",
        "message": "超预算",
        "details": {"cost": 1.2},
        "retryable": False,
    }
    run = Run(
        id="run-2",
        agent_key="a",
        agent_version=1,
        brain_profile=BrainProfile.CHEAP,
        status=RunStatus.FAILED,
        error=error,
        created_at=NOW,
    )
    restored = _round_trip(Run, run)
    assert restored.error == error


def test_trace_event_round_trip() -> None:
    event = TraceEvent(
        id="ev-1",
        run_id="run-1",
        type=TraceEventType.TOOL_CALL_COMPLETED,
        step_index=2,
        payload={"tool": "refund", "ms": 12.5},
        created_at=NOW,
    )
    restored = _round_trip(TraceEvent, event)
    assert restored.type is TraceEventType.TOOL_CALL_COMPLETED
    assert restored.payload == {"tool": "refund", "ms": 12.5}


def test_model_definition_round_trip() -> None:
    model = ModelDefinition(
        key="deepseek-chat",
        provider="deepseek",
        model_name="deepseek-chat",
        factory="openai_compat",
        capability={"tool_calling": True, "structured_output": True, "context_window": 65536},
        params={"temperature": 0.2},
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
    )
    restored = _round_trip(ModelDefinition, model)
    assert restored.capability.tool_calling is True
    assert restored.capability.streaming is True  # 默认值保留
    assert restored.api_key_env == "DEEPSEEK_API_KEY"


def test_model_capability_defaults() -> None:
    model = ModelDefinition(key="k", provider="p", model_name="m", factory="fake")
    cap = model.capability
    assert (cap.chat, cap.streaming) == (True, True)
    assert (cap.tool_calling, cap.structured_output, cap.vision, cap.reasoning) == (
        False,
        False,
        False,
        False,
    )
    assert (cap.context_window, cap.max_output) == (8192, 4096)
    assert model.enabled is True
    assert model.base_url is None
    assert model.params == {}
