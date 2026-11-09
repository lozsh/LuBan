# 流程与团队参考产出（一）：app/graphs/my_library_flow.py

"""逾期处理流水线（确定性图编排）：查逾期 → 分级 → 严重逾期人工确认 → 处罚 → 通知。"""

from typing import Any

from lubanagent.extension import default_registry, graph
from lubanagent.graph import GraphBuilder

SEVERE_OVERDUE_DAYS = 30  # 严重逾期线：超过则冻结借阅权（需人工确认）


def _load_overdue(state: dict[str, Any]) -> dict[str, Any]:
    """按学号查借阅记录，算出逾期最重的一册（复用业务工具，不绕过治理）。"""
    tool = default_registry.get_tool("my_lib_lookup")
    records = tool.handler(student_id=state["student_id"])["records"]
    # 示例演示：以固定"今天"为基准算逾期天数
    from datetime import date
    today = date(2026, 9, 9)
    worst = max(records, key=lambda r: (today - date.fromisoformat(r["due"])).days)
    overdue_days = (today - date.fromisoformat(worst["due"])).days
    return {"worst_title": worst["title"], "overdue_days": overdue_days}


def _needs_confirm(state: dict[str, Any]) -> bool:
    return state["overdue_days"] > SEVERE_OVERDUE_DAYS


def _apply_penalty(state: dict[str, Any]) -> dict[str, Any]:
    # 拒绝或缺少人工确认时，不允许冻结借阅权限。
    days = state["overdue_days"]
    approved = state.get("confirm_freeze") is True
    return {"penalty": round(days * 0.1, 1),
            "frozen": days > SEVERE_OVERDUE_DAYS and approved}


def _notify(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("frozen"):
        return {"message": f"《{state['worst_title']}》逾期 {state['overdue_days']} 天，"
                            f"滞纳金 {state['penalty']} 元，借阅权限已冻结（缴清后恢复）。"}
    return {"message": f"《{state['worst_title']}》逾期 {state['overdue_days']} 天，"
                        f"滞纳金 {state['penalty']} 元，请尽快归还。"}


def _build() -> "GraphBuilder":
    builder = GraphBuilder(
        "my_library_flow", description="逾期处理：查逾期→分级→严重确认→处罚→通知"
    )
    builder.entry("load_overdue")
    builder.step("load_overdue", _load_overdue)
    builder.human(
        "confirm_freeze", prompt=f"逾期超过 {SEVERE_OVERDUE_DAYS} 天将冻结借阅权限，需人工确认"
    )
    builder.step("apply_penalty", _apply_penalty)
    builder.step("notify", _notify)
    # 注意：entry 节点只出条件边（when），同一节点不能同时有普通边与条件边
    builder.when("load_overdue", _needs_confirm, then="confirm_freeze", otherwise="apply_penalty")
    builder.edge("confirm_freeze", "apply_penalty")
    builder.edge("apply_penalty", "notify")
    return builder


@graph(name="my_library_flow", description="逾期处理：查逾期→分级→严重确认→处罚→通知")
def my_library_flow_builder() -> Any:
    return _build()


# ============================================================
# 流程与团队参考产出（二）：app/multiagents/my_library_team.py

# """图书馆咨询台团队：triage 调度 + 借阅事务/规章专家两成员。"""
#
# from lubanagent.multiagent import Team, TeamBuilder
#
#
# def build_my_library_team() -> Team:
#     builder = TeamBuilder(
#         "my-library-team", description="图书馆咨询台：triage 调度 + 事务/规章成员"
#     )
#     builder.supervisor("my-lib-triage")
#     builder.agent("my-lib-assistant", description="借阅记录查询、校园卡挂失、逾期处理")
#     builder.agent("my-lib-rules", description="借阅规章、续借规则、滞纳金、开放时间咨询")
#     builder.route(max_delegations=4)
#     return builder.build()

# ============================================================
# 流程与团队参考产出（三）：app/agents/my_lib_triage.yaml（supervisor，JSON 决策指令）
# 与 app/agents/my_lib_rules.yaml（规章专家，纯 RAG + 守门——参照知识检索指南的 knowledge 配置）
# 见 website/tutorial/reference/07-triage-and-rules.yaml
