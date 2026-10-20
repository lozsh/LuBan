"""退款流水线：确定性 LangGraph 图（D3-4 GraphBuilder）。

步骤：资格校验 → 金额计算 → when(金额 > REFUND_APPROVAL_THRESHOLD → human 审批，
否则直退) → 执行退款 → 通知。
import 本模块即经 @graph 注册（app.graphs 是约定扫描入口）。
"""

from typing import Any

from app.config.refund_policy import REFUND_APPROVAL_THRESHOLD
from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import graph
from lubanagent.graph import GraphBuilder

# Mock biz 的退款端点经 Tool refund_apply 已封装；图中不直接调 HTTP，
# 而是在 step 里调用 refund_apply 的 handler（经 default_registry）。
# 但 step fn 不应是 Tool（Tool 有治理包装），这里直接调业务逻辑更清晰。


def _check_eligibility(state: dict[str, Any]) -> dict[str, Any]:
    """资格校验：订单存在且可退。"""
    order_id = str(state.get("order_id", ""))
    if not order_id:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "退款流水线缺 order_id")
    # 查单：经 Tool handler（业务侧 HTTP），不绕过 Tool 治理
    from lubanagent.extension import default_registry

    registered = default_registry.get_tool("query_order")
    result = registered.handler(order_id=order_id)
    # handler 是同步函数（@tool 装饰返回原函数），直接拿返回值
    order = result if isinstance(result, dict) else {}
    if not order.get("refundable", False):
        raise LubanError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            f"订单不可退: {order_id}",
            details={"order_id": order_id, "status": order.get("status")},
        )
    return {"order": order, "checked": True}


def _compute_amount(state: dict[str, Any]) -> dict[str, Any]:
    """金额计算：取订单金额作为退款额（示例简化），标记是否需审批。"""
    order = state.get("order") or {}
    amount = float(state.get("amount") or order.get("amount") or 0.0)
    needs_approval = amount > REFUND_APPROVAL_THRESHOLD  # 口径单点配置：>（非 >=）
    return {"amount": amount, "needs_approval": needs_approval}


def _needs_approval(state: dict[str, Any]) -> bool:
    return bool(state.get("needs_approval"))


def _execute_refund(state: dict[str, Any]) -> dict[str, Any]:
    """执行退款：经 Tool refund_apply（已含审批闸门，大额由 Tool 治理拦截）。"""
    from lubanagent.extension import default_registry

    registered = default_registry.get_tool("refund_apply")
    result = registered.handler(
        order_id=str(state.get("order_id", "")),
        amount=float(state.get("amount", 0.0)),
        reason=str(state.get("reason", "流水线自动退款")),
    )
    return {"refund_result": result, "refunded": True}


def _notify(state: dict[str, Any]) -> dict[str, Any]:
    """通知：构造用户可见的退款结果摘要。"""
    refund = state.get("refund_result") or {}
    order = state.get("order") or {}
    amount = state.get("amount", 0.0)
    summary = (
        f"退款已提交：订单 {order.get('order_id', '')}，"
        f"金额 {amount} 元，退款单号 {refund.get('refund_id', '')}。"
    )
    return {"notification": summary}


def _build_refund_flow() -> "GraphBuilder":
    """构建退款流水线图。"""
    builder = GraphBuilder("refund_flow", description="退款流水线：校验→计算→审批→退款→通知")
    builder.entry("check_eligibility")
    builder.step("check_eligibility", _check_eligibility)
    builder.step("compute_amount", _compute_amount)
    # 审批提示从单点配置渲染，与图分支判定同源（改常量即同步）
    builder.human(
        "approve",
        prompt=f"退款金额 >{REFUND_APPROVAL_THRESHOLD:g} 元，需要人工审批",
    )
    builder.step("execute_refund", _execute_refund)
    builder.step("notify", _notify)
    builder.edge("check_eligibility", "compute_amount")
    builder.when(
        "compute_amount",
        _needs_approval,
        then="approve",
        otherwise="execute_refund",
    )
    builder.edge("approve", "execute_refund")
    builder.edge("execute_refund", "notify")
    return builder


@graph(name="refund_flow", description="退款流水线：校验→计算→审批→退款→通知")
def refund_flow_builder() -> Any:  # GraphDescriptor.builder 语义：返回编译图
    return _build_refund_flow()
