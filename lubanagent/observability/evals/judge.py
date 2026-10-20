"""LLM-as-judge：smart 槽位模型按 criteria 给输出打分（1-5）。

judge 模型经 BrainResolver 解析（契约 D2-1 决策 3）；提示词属框架内部实现，不进用户领地。
模型输出解析失败 → LubanError(VALIDATION_ERROR)；check_judge 捕获后记中立 error  verdict
（不计 pass/fail）。
"""

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from lubanagent.core import ErrorCode, LubanError
from lubanagent.observability.evals.assertions import AssertionVerdict
from lubanagent.observability.evals.dataset import EvalCase

_SYSTEM_PROMPT = (
    "你是严格的评测裁判。根据评分标准给 AI 助手的回复打分。"
    "只输出 JSON，形如 {\"score\": 1-5 的整数, \"reason\": \"简短理由\"}，不要输出其他内容。"
)


def _extract_json(text: str) -> dict[str, Any]:
    """从模型输出提取 JSON 对象（容忍 ```json 代码围栏）；失败 → VALIDATION_ERROR。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            "judge 输出不是合法 JSON",
            details={"judge_output": text},
        ) from exc
    if not isinstance(data, dict):
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            "judge 输出不是 JSON 对象",
            details={"judge_output": text},
        )
    return data


class LlmJudge:
    """包装一个 chat model：score() 返回 (1-5 分, 理由)。"""

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    async def score(self, *, criteria: str, input: str, output: str) -> tuple[int, str]:
        prompt = (
            f"评分标准：{criteria}\n\n"
            f"用户输入：{input}\n\n"
            f"助手回复：{output}\n\n"
            "请按评分标准输出 JSON。"
        )
        response = await self._model.ainvoke(
            [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
        content = response.content
        text = content if isinstance(content, str) else str(content)
        data = _extract_json(text)
        score = data.get("score")
        reason = data.get("reason", "")
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                "judge 输出缺少 1-5 的整数 score",
                details={"judge_output": text},
            )
        return score, str(reason)


async def check_judge(
    case: EvalCase, judge: LlmJudge | None, output: str | None
) -> AssertionVerdict | None:
    """case 未配置 judge → None；解析失败 → 中立 error verdict；否则按 pass_score 判定。"""
    if case.judge is None:
        return None
    if judge is None:
        return AssertionVerdict(
            kind="judge", passed=False, detail="用例配置了 judge 但运行器未提供 LlmJudge"
        )
    try:
        score, reason = await judge.score(
            criteria=case.judge.criteria, input=case.input, output=output or ""
        )
    except LubanError as exc:
        return AssertionVerdict(
            kind="judge",
            passed=False,
            detail=f"judge 判定失败（不计 pass/fail）: {exc.message}",
            error=exc.message,
        )
    passed = score >= case.judge.pass_score
    return AssertionVerdict(
        kind="judge",
        passed=passed,
        detail=f"judge 评分 {score}/{case.judge.pass_score}: {reason}",
    )
