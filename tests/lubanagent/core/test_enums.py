"""枚举值逐字核对: 契约「验收标准」第 4 条。"""

import pytest

from lubanagent.core import (
    AgentStatus,
    BrainProfile,
    EndReason,
    ErrorCode,
    RiskLevel,
    RunStatus,
    TraceEventType,
)


def test_brain_profile_values() -> None:
    assert [p.value for p in BrainProfile] == ["fast", "smart", "cheap", "vision"]


def test_brain_profile_supports_custom_identifiers() -> None:
    from lubanagent.core import AgentDefinition

    definition = AgentDefinition(
        key="custom",
        name="Custom",
        brain={"profile": "deep_reasoning", "fallback_profiles": ["small-model"]},
    )
    assert definition.brain.profile.value == "deep_reasoning"
    assert definition.brain.fallback_profiles[0].value == "small-model"
    assert definition.model_dump(mode="json")["brain"]["profile"] == "deep_reasoning"
    assert BrainProfile("smart") is BrainProfile.SMART
    assert "enum" not in AgentDefinition.model_json_schema()["$defs"]["BrainProfile"]


@pytest.mark.parametrize("value", ["", "Smart", "a b", "../slot", "a/b", "中文", "1name", "a" * 65])
def test_brain_profile_rejects_invalid_identifiers(value: str) -> None:
    with pytest.raises(ValueError):
        BrainProfile(value)


def test_error_code_all_32() -> None:
    expected = [
        "AGENT_NOT_FOUND",
        "AGENT_VERSION_NOT_FOUND",
        "AGENT_VERSION_UNRUNNABLE",
        "MODEL_NOT_FOUND",
        "BRAIN_NOT_MAPPED",
        "BRAIN_PROFILE_NOT_FOUND",
        "BRAIN_PROFILE_CONFLICT",
        "BRAIN_PROFILE_IN_USE",
        "CAPABILITY_MISMATCH",
        "NO_FALLBACK_AVAILABLE",
        "TOOL_NOT_FOUND",
        "TOOL_PERMISSION_DENIED",
        "TOOL_APPROVAL_REQUIRED",
        "TOOL_EXECUTION_FAILED",
        "RUN_NOT_FOUND",
        "BUDGET_EXCEEDED",
        "STEP_LIMIT_EXCEEDED",
        "RUN_TIMEOUT",
        "RUN_CANCELLED",
        "AUTH_FAILED",
        "PERMISSION_DENIED",
        "RATE_LIMITED",
        "VALIDATION_ERROR",
        "CONFIG_ERROR",
        "INTERNAL_ERROR",
        "PROMPT_NOT_FOUND",
        "PROMPT_VERSION_NOT_FOUND",
        "KNOWLEDGE_SOURCE_NOT_FOUND",
        "GRAPH_NOT_FOUND",
        "GRAPH_STRUCTURE_UNAVAILABLE",
        "AVATAR_NOT_FOUND",
        "EVAL_RESULT_NOT_FOUND",
    ]
    assert [c.value for c in ErrorCode] == expected
    assert all(c.name == c.value for c in ErrorCode)


def test_run_status_values() -> None:
    assert [s.value for s in RunStatus] == [
        "pending",
        "running",
        "waiting_approval",
        "completed",
        "failed",
        "cancelled",
    ]


def test_end_reason_values() -> None:
    assert [r.value for r in EndReason] == [
        "final_answer",
        "step_limit",
        "budget_exceeded",
        "timeout",
        "cancelled",
        "error",
        "human_handoff",
        "knowledge_no_hit",
    ]


def test_agent_status_values() -> None:
    assert [s.value for s in AgentStatus] == ["draft", "published", "deprecated"]


def test_risk_level_values() -> None:
    assert [r.value for r in RiskLevel] == ["low", "medium", "high"]


def test_trace_event_type_values() -> None:
    expected = [
        "run_started",
        "run_resumed",
        "run_completed",
        "run_failed",
        "run_cancelled",
        "step_started",
        "step_completed",
        "model_call_started",
        "model_token_delta",
        "model_call_completed",
        "tool_call_started",
        "tool_call_completed",
        "tool_call_failed",
        "tool_approval_requested",
        "tool_approval_resolved",
        "budget_warning",
        "budget_exceeded",
        "model_fallback",
        "knowledge_no_hit",
        "watchdog_alert",
        "tool_task_accepted",
        "tool_task_completed",
        "tool_task_failed",
        "tool_task_cancelled",
    ]
    assert [t.value for t in TraceEventType] == expected
