"""多 Agent 团队共享状态（契约决策 3）：TeamState + DelegationRecord。"""

from typing import Any, TypedDict

from pydantic import BaseModel


class DelegationRecord(BaseModel):
    """一次委派的留痕：委派给哪个成员、转述输入、成员输出、成员子 Run id。"""

    agent_key: str
    input: str
    output: str
    run_id: str


class TeamState(TypedDict, total=False):
    """团队编排的共享 state（承载于图 state["data"]，纯 dict 合并语义）。

    messages 为简化对话轨迹（role=supervisor 或成员 agent_key），供 supervisor 决策上下文。
    运行期还会携带 thread_id / decision / member_descriptions 等内部键（不进本 schema）；
    decision 为 SupervisorDecision 的 dict 形态——扇出并行时带 agents 列表（多成员），
    delegations 按成员执行次数累积（入口成员执行、扇出的每个并行子各计一条）。
    """

    input: str
    messages: list[dict[str, str]]  # {"role": "supervisor"|agent_key, "content": str}
    delegations: list[DelegationRecord]
    final_output: str | None


def delegation_output(record: DelegationRecord | dict[str, Any]) -> str:
    """兼容对象/字典两态取成员输出（checkpoint 往返后可能是 dict）。"""
    if isinstance(record, DelegationRecord):
        return record.output
    return str(record.get("output") or "")


__all__ = ["DelegationRecord", "TeamState", "delegation_output"]
