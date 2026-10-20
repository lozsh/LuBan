"""评测断言：工具调用 / 输出关键字 / 拒答三类确定性断言（judge 断言见 judge.py）。

断言只呈现事实：未配置的类别返回 None（不参与判定）；
判定误差的中立情形用 verdict.error 表达（不计 pass/fail，见契约 D2-1 决策 3）。
AssertionVerdict 领域模型已下沉 `lubanagent.core.evaluation`，本模块 re-export。
"""

from typing import Any

from lubanagent.core import AssertionVerdict, TraceEvent, TraceEventType
from lubanagent.observability.evals.dataset import EvalCase

__all__ = [
    "AssertionVerdict",
    "check_output",
    "check_refusal",
    "check_tool_calls",
]


def _args_contains(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """参数子集匹配：actual 逐键包含 expected 的键值。"""
    return all(actual.get(key) == value for key, value in expected.items())


def check_tool_calls(case: EvalCase, events: list[TraceEvent]) -> AssertionVerdict | None:
    """expect_tools/expect_tool_args 均未配置 → None；否则按 TOOL_CALL_STARTED 事件判定。"""
    if not case.expect_tools and not case.expect_tool_args:
        return None
    started = [event for event in events if event.type is TraceEventType.TOOL_CALL_STARTED]
    called_tools = {str(event.payload.get("tool")) for event in started}

    missing = [tool for tool in case.expect_tools if tool not in called_tools]
    arg_failures: list[str] = []
    for tool, expected_args in case.expect_tool_args.items():
        matched = any(
            event.payload.get("tool") == tool
            and _args_contains(dict(event.payload.get("args") or {}), expected_args)
            for event in started
        )
        if not matched:
            arg_failures.append(f"{tool} 参数未匹配期望子集 {expected_args}")
    if missing or arg_failures:
        parts: list[str] = []
        if missing:
            parts.append(f"期望工具未被调用: {missing}（实际调用: {sorted(called_tools)}）")
        parts.extend(arg_failures)
        return AssertionVerdict(kind="tool_call", passed=False, detail="；".join(parts))
    return AssertionVerdict(
        kind="tool_call",
        passed=True,
        detail=f"工具调用符合期望: {sorted(called_tools)}",
    )


def check_output(case: EvalCase, output: str | None) -> AssertionVerdict | None:
    """expect_output 未配置 → None；contains 全部命中（大小写不敏感）才 pass。"""
    if case.expect_output is None:
        return None
    text = (output or "").lower()
    missing = [word for word in case.expect_output.contains if word.lower() not in text]
    if missing:
        return AssertionVerdict(
            kind="output", passed=False, detail=f"输出缺少关键字: {missing}"
        )
    return AssertionVerdict(
        kind="output",
        passed=True,
        detail=f"输出包含全部关键字: {case.expect_output.contains}",
    )


def check_refusal(case: EvalCase, events: list[TraceEvent]) -> AssertionVerdict | None:
    """expect_refusal=False → None；True → 事件流须含 KNOWLEDGE_NO_HIT（未拒答=错答）。

    判定依据是事件不是文本（确定性，不依赖 judge）：防错答守门短路时 agent_loop
    必发 KNOWLEDGE_NO_HIT 事件；用例要求拒答而事件缺失即错答。
    """
    if not case.expect_refusal:
        return None
    no_hit = any(
        event.type is TraceEventType.KNOWLEDGE_NO_HIT for event in events
    )
    if no_hit:
        return AssertionVerdict(
            kind="refusal",
            passed=True,
            detail="按预期拒答（knowledge_no_hit 短路生效）",
        )
    return AssertionVerdict(
        kind="refusal",
        passed=False,
        detail="应拒答未拒答（错答）：事件流无 knowledge_no_hit，模型在低置信检索下作答",
    )
