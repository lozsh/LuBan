"""supervisor 路由：每轮决策 = supervisor Agent 子 Run + 结构化输出解析（契约决策 1/5）。

选型说明：fake 模型下 with_structured_output 未必可用（依赖模型厂商实现），
故采用「prompt 要求输出 JSON + 截取首个 {...} 块 json.loads」的容错解析；
解析失败 / 字段缺失 / 委派未知成员一律降级为 answer（不炸编排）。
委派轮次上限 max_delegations 代码强制（防 ping-pong）：触顶直接强制 answer，不再调模型。

扇出并行（fanout，默认关）：启用后 supervisor 可一次决策委派多个成员
（{"action": "delegate", "agents": [...], "input": ...}），解析为 agents 列表；
列表含未知成员 / 空列表 → 与单成员未知同路，降级 answer（reason=unknown_agent）。
未启用时 agents 列表不进解析分支（prompt 也不提供该语法）→ 老团队行为零变化。
"""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel

from lubanagent.core import (
    BudgetExceededError,
    EndReason,
    ErrorCode,
    LubanError,
    Run,
    RunCancelledError,
    RunContext,
    RunStatus,
    RunTimeoutError,
)
from lubanagent.runtime import AgentRuntime, BudgetGuard, RunRequest

from .state import TeamState, delegation_output

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class SupervisorDecision(BaseModel):
    """一轮 supervisor 决策；reason 在强制收尾/兜底时注明（如 max_delegations）。

    单成员委派写 agent；扇出并行（fanout 启用）写 agents 列表（≥2 个成员，去重保序）。
    """

    action: Literal["delegate", "answer"]
    agent: str | None = None
    agents: list[str] | None = None
    input: str = ""
    answer: str | None = None
    reason: str | None = None


def ensure_child_ok(child: Run) -> str:
    """子 Run 治理终态映射回图内异常（与 D3-4 agent 节点同一套映射），成功返回输出文本。"""
    if child.status is RunStatus.COMPLETED:
        return child.output or ""
    details = {"agent_key": child.agent_key, "child_run_id": child.id}
    if child.end_reason is EndReason.BUDGET_EXCEEDED:
        raise BudgetExceededError(f"子 Run 预算超限: {child.agent_key}", details=details)
    if child.end_reason is EndReason.TIMEOUT:
        raise RunTimeoutError(f"子 Run 超时: {child.agent_key}", details=details)
    if child.end_reason is EndReason.CANCELLED:
        raise RunCancelledError(f"子 Run 被取消: {child.agent_key}", details=details)
    raise LubanError(
        ErrorCode.INTERNAL_ERROR,
        f"子 Run 未成功: {child.agent_key} → {child.status.value}",
        details=details,
    )


class SupervisorRouter:
    """supervisor Agent + 结构化输出解析 → 每轮决策；fanout 启用时支持多成员扇出。"""

    def __init__(
        self,
        *,
        agent_key: str,
        members: list[str],
        max_delegations: int = 8,
        fanout: bool = False,
    ) -> None:
        self._agent_key = agent_key
        self._members = list(members)
        self._max_delegations = max_delegations
        self._fanout = fanout

    @property
    def agent_key(self) -> str:
        return self._agent_key

    @property
    def members(self) -> list[str]:
        return list(self._members)

    @property
    def max_delegations(self) -> int:
        return self._max_delegations

    async def decide(
        self, state: TeamState, *, runtime: AgentRuntime, parent_ctx: RunContext
    ) -> SupervisorDecision:
        """构造路由 prompt → supervisor 子 Run（parent_run_id 透传）→ 解析结构化输出。

        delegations 数达 max_delegations → 强制 answer（payload 注明 reason，不再调模型）。
        """
        delegations = list(state.get("delegations") or [])
        if len(delegations) >= self._max_delegations:
            return self._forced_answer(delegations)
        guard = parent_ctx.metadata.get("budget_guard")
        child = await runtime.run(
            RunRequest(
                agent_key=self._agent_key,
                user_input=self._routing_prompt(state),
                session_id=state.get("thread_id"),
                user_id=parent_ctx.user_id,
                parent_run_id=parent_ctx.run_id,
                budget_guard=guard if isinstance(guard, BudgetGuard) else None,
            )
        )
        raw = ensure_child_ok(child)
        return self._parse(raw)

    # ------------------------------------------------------------ 内部

    def _forced_answer(self, delegations: list[Any]) -> SupervisorDecision:
        answer = f"已达最大委派轮次（{self._max_delegations}），停止继续委派。"
        if delegations:
            last_output = delegation_output(delegations[-1])
            if last_output:
                answer = f"{answer}最近一次成员结果：{last_output}"
        return SupervisorDecision(action="answer", answer=answer, reason="max_delegations")

    def _parse(self, raw: str) -> SupervisorDecision:
        """截取首个 {...} 块 json.loads；失败/缺字段/未知成员 → 当 answer 处理。

        fanout 启用且 payload 带 agents 列表 → 走扇出分支（全为已知成员且非空才有效，
        否则与未知成员同路降级 answer）；未启用时 agents 列表不进分支（行为零变化）。
        """
        payload: dict[str, Any] = {}
        match = _JSON_BLOCK.search(raw)
        if match is not None:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                payload = data
        action = payload.get("action")
        if action == "delegate":
            if self._fanout:
                raw_agents = payload.get("agents")
                if isinstance(raw_agents, list):
                    agents = [str(item) for item in raw_agents]
                    if agents and all(item in self._members for item in agents):
                        # 去重保序：同一成员一轮扇出只执行一次
                        return SupervisorDecision(
                            action="delegate",
                            agents=list(dict.fromkeys(agents)),
                            input=str(payload.get("input") or ""),
                        )
                    return SupervisorDecision(action="answer", answer=raw, reason="unknown_agent")
            agent = payload.get("agent")
            if agent in self._members:
                return SupervisorDecision(
                    action="delegate",
                    agent=str(agent),
                    input=str(payload.get("input") or ""),
                )
            return SupervisorDecision(action="answer", answer=raw, reason="unknown_agent")
        if action == "answer":
            answer = payload.get("answer")
            return SupervisorDecision(action="answer", answer=str(answer) if answer else raw)
        return SupervisorDecision(action="answer", answer=raw, reason="parse_fallback")

    def _routing_prompt(self, state: TeamState) -> str:
        descriptions = state.get("member_descriptions") or {}  # type: ignore[typeddict-item]
        member_lines = [
            f"- {key}: {descriptions.get(key) or '（无描述）'}" for key in self._members
        ]
        trace_lines = [
            f"- {message.get('role')}: {message.get('content')}"
            for message in state.get("messages") or []
        ]
        action_lines = (
            '- 自己直接回答：{"action": "answer", "answer": "<最终答复>"}\n'
            '- 委派给成员：{"action": "delegate", "agent": "<成员 key>", '
            '"input": "<转述给成员的请求>"}\n'
        )
        if self._fanout:
            action_lines += (
                '- 并行委派给多个成员（结果一并汇总给你）：'
                '{"action": "delegate", "agents": ["<成员1>", "<成员2>"], '
                '"input": "<转述给成员的请求>"}\n'
            )
        return (
            f"你是团队调度 supervisor（agent: {self._agent_key}）。"
            "根据用户请求与对话轨迹，决定下一步动作，只输出一个 JSON 对象，不要输出其他内容：\n"
            + action_lines
            + "\n可选成员：\n" + "\n".join(member_lines) + "\n\n"
            f"用户请求：{state.get('input') or ''}\n\n"
            "对话轨迹：\n" + ("\n".join(trace_lines) if trace_lines else "（空）")
        )


__all__ = ["SupervisorDecision", "SupervisorRouter", "ensure_child_ok"]
