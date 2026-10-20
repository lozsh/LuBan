"""LLM-as-judge：分数边界（3/4/5，pass_score=4）、代码围栏容忍、解析失败的中立 error 语义。"""

import pytest

from lubanagent.core import ErrorCode, LubanError
from lubanagent.model import ScriptedFakeChatModel
from lubanagent.observability.evals import EvalCase, JudgeSpec, LlmJudge, check_judge

CASE = EvalCase(
    case="c",
    input="我的订单怎么还没到",
    judge=JudgeSpec(criteria="先安抚情绪，再给解决方案", pass_score=4),
)


def _judge(script: list) -> LlmJudge:
    return LlmJudge(ScriptedFakeChatModel(script=script))


async def test_score_parses_json() -> None:
    judge = _judge(['{"score": 5, "reason": "先安抚再给方案"}'])
    score, reason = await judge.score(criteria="标准", input="问", output="答")
    assert score == 5
    assert reason == "先安抚再给方案"


async def test_score_tolerates_code_fence() -> None:
    judge = _judge(['```json\n{"score": 4, "reason": "合格"}\n```'])
    score, _ = await judge.score(criteria="标准", input="问", output="答")
    assert score == 4


async def test_score_parse_failure_raises_validation_error() -> None:
    judge = _judge(["这不是 JSON"])
    with pytest.raises(LubanError) as exc_info:
        await judge.score(criteria="标准", input="问", output="答")
    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR


async def test_score_out_of_range_raises_validation_error() -> None:
    judge = _judge(['{"score": 9, "reason": "超界"}'])
    with pytest.raises(LubanError) as exc_info:
        await judge.score(criteria="标准", input="问", output="答")
    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR


async def test_check_judge_unconfigured_returns_none() -> None:
    case = EvalCase(case="c", input="i")
    assert await check_judge(case, _judge([]), "输出") is None


@pytest.mark.parametrize(
    ("score", "expected"),
    [(3, False), (4, True), (5, True)],
)
async def test_check_judge_score_boundary(score: int, expected: bool) -> None:
    judge = _judge([f'{{"score": {score}, "reason": "r"}}'])
    verdict = await check_judge(CASE, judge, "安抚您，给您替代方案")
    assert verdict is not None and verdict.kind == "judge"
    assert verdict.passed is expected
    assert verdict.error is None


async def test_check_judge_parse_failure_is_neutral_error() -> None:
    verdict = await check_judge(CASE, _judge(["无法解析"]), "输出")
    assert verdict is not None
    assert verdict.error is not None  # 中立 error：不计 pass/fail
    assert not verdict.passed


async def test_check_judge_without_judge_instance_fails() -> None:
    verdict = await check_judge(CASE, None, "输出")
    assert verdict is not None and not verdict.passed
    assert verdict.error is None
