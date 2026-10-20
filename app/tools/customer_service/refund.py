"""客服示例工具：退款申请，经 HTTP 调 Mock 业务服务（BIZ_BASE_URL）。

高风险工具：approval_required=True，超过退款审批阈值（单点配置
app/config/refund_policy.py 的 REFUND_APPROVAL_THRESHOLD）由框架审批闸门拦截。
"""

import httpx

from app.config.refund_policy import REFUND_APPROVAL_THRESHOLD
from app.tools.customer_service.order import _request
from lubanagent.config import get_settings
from lubanagent.core import ErrorCode, LubanError, RiskLevel
from lubanagent.extension import tool

_DEFAULT_BIZ_URL = "http://localhost:9100"


def _biz_url() -> str:
    # 统一走 Settings.biz_base_url(env: BIZ_BASE_URL), 不直接读环境变量
    return (get_settings().biz_base_url or _DEFAULT_BIZ_URL).rstrip("/")


@tool(
    key="refund_apply",
    # 描述中的审批口径从单点配置渲染（改常量即改描述，与图分支/Agent 指令同源）
    description=(
        f"提交退款申请（高风险：>{REFUND_APPROVAL_THRESHOLD:g}元触发人工审批）。"
        "risk=high、非幂等、超时15秒。"
    ),
    risk=RiskLevel.HIGH,
    timeout_seconds=15.0,
    idempotent=False,
    approval_required=True,
)
def refund_apply(order_id: str, amount: float, reason: str) -> dict:
    """提交退款申请。

    参数:
        order_id: 订单号
        amount: 退款金额（不得超过订单金额）
        reason: 退款原因

    返回:
        退款单 JSON（refund_id/order_id/amount/status）；不可退返回 error。
    """
    url = f"{_biz_url()}/refunds"
    payload = {"order_id": order_id, "amount": amount, "reason": reason}
    try:
        resp = _request("POST", url, json=payload, timeout=15.0)
    except httpx.RequestError as exc:
        raise LubanError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            f"退款申请连接失败: {exc}",
            details={"url": url},
            retryable=True,
        ) from None
    if resp.status_code not in (200, 201):
        raise LubanError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            f"退款申请失败: HTTP {resp.status_code}",
            details={
                "order_id": order_id,
                "status_code": resp.status_code,
                "response": resp.text,
            },
        )
    return resp.json()
