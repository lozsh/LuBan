"""确定性断言：工具调用（集合包含 + 参数子集）与输出关键字，正负用例与未配置语义。"""

from datetime import UTC, datetime
from typing import Any

from lubanagent.core import TraceEvent, TraceEventType
from lubanagent.observability.evals import (
    EvalCase,
    ExpectOutput,
    check_output,
    check_refusal,
    check_tool_calls,
)


def _tool_started(tool: str, args: dict[str, Any] | None = None) -> TraceEvent:
    return TraceEvent(
        id=f"evt-{tool}",
        run_id="run-1",
        type=TraceEventType.TOOL_CALL_STARTED,
        payload={"tool": tool, "args": args or {}},
        created_at=datetime.now(UTC),
    )


def test_tool_calls_unconfigured_returns_none() -> None:
    case = EvalCase(case="c", input="i")
    assert check_tool_calls(case, []) is None


def test_tool_calls_expected_tool_called() -> None:
    case = EvalCase(case="c", input="i", expect_tools=["query_order"])
    verdict = check_tool_calls(case, [_tool_started("query_order")])
    assert verdict is not None and verdict.kind == "tool_call"
    assert verdict.passed


def test_tool_calls_expected_tool_not_called_fails() -> None:
    case = EvalCase(case="c", input="i", expect_tools=["query_order"])
    verdict = check_tool_calls(case, [_tool_started("other_tool")])
    assert verdict is not None and not verdict.passed
    assert "query_order" in verdict.detail


def test_tool_args_subset_match() -> None:
    case = EvalCase(
        case="c",
        input="i",
        expect_tool_args={"query_order": {"order_id": "12345"}},
    )
    events = [_tool_started("query_order", {"order_id": "12345", "extra": "忽略"})]
    verdict = check_tool_calls(case, events)
    assert verdict is not None and verdict.passed


def test_tool_args_mismatch_fails() -> None:
    case = EvalCase(
        case="c",
        input="i",
        expect_tool_args={"query_order": {"order_id": "12345"}},
    )
    events = [_tool_started("query_order", {"order_id": "99999"})]
    verdict = check_tool_calls(case, events)
    assert verdict is not None and not verdict.passed


def test_tool_args_tool_never_called_fails() -> None:
    case = EvalCase(case="c", input="i", expect_tool_args={"query_order": {"order_id": "1"}})
    verdict = check_tool_calls(case, [])
    assert verdict is not None and not verdict.passed


def test_output_unconfigured_returns_none() -> None:
    case = EvalCase(case="c", input="i")
    assert check_output(case, "任意输出") is None


def test_output_contains_all_keywords_case_insensitive() -> None:
    case = EvalCase(
        case="c", input="i", expect_output=ExpectOutput(contains=["安抚", "REFUND"])
    )
    verdict = check_output(case, "先安抚您，refund 已提交")
    assert verdict is not None and verdict.kind == "output"
    assert verdict.passed


def test_output_missing_keyword_fails() -> None:
    case = EvalCase(case="c", input="i", expect_output=ExpectOutput(contains=["安抚", "替代"]))
    verdict = check_output(case, "先安抚您")
    assert verdict is not None and not verdict.passed
    assert "替代" in verdict.detail


def test_output_none_output_fails_when_configured() -> None:
    case = EvalCase(case="c", input="i", expect_output=ExpectOutput(contains=["安抚"]))
    verdict = check_output(case, None)
    assert verdict is not None and not verdict.passed


# ------------------------------------------------------------ 拒答断言（防错答硬指标）


def _no_hit_event() -> TraceEvent:
    return TraceEvent(
        id="evt-no-hit",
        run_id="run-1",
        type=TraceEventType.KNOWLEDGE_NO_HIT,
        payload={"sources": ["faq"], "score_threshold": 0.2},
        created_at=datetime.now(UTC),
    )


def test_refusal_unconfigured_returns_none() -> None:
    case = EvalCase(case="c", input="i")
    assert check_refusal(case, []) is None


def test_refusal_expected_and_short_circuited_passes() -> None:
    case = EvalCase(case="c", input="i", expect_refusal=True)
    verdict = check_refusal(case, [_no_hit_event()])
    assert verdict is not None and verdict.kind == "refusal"
    assert verdict.passed


def test_refusal_expected_but_model_answered_fails() -> None:
    """应拒答未拒答 = 错答（事件流无 knowledge_no_hit）。"""
    case = EvalCase(case="c", input="i", expect_refusal=True)
    verdict = check_refusal(case, [_tool_started("query_order")])
    assert verdict is not None and verdict.kind == "refusal"
    assert not verdict.passed
    assert "错答" in verdict.detail
