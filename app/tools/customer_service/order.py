"""客服示例工具：订单查询，经 HTTP 调 Mock 业务服务（BIZ_BASE_URL）。

领地隔离：只 import lubanagent/*，禁止 import demos/*（Tool 走 HTTP）。
"""

from contextvars import ContextVar

import httpx

from lubanagent.config import get_settings
from lubanagent.core import ErrorCode, LubanError, RiskLevel
from lubanagent.extension import tool

_DEFAULT_BIZ_URL = "http://localhost:9100"
_business_transport: ContextVar[httpx.BaseTransport | None] = ContextVar(
    "business_transport", default=None
)


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    """共享的 HTTP 请求入口；测试可注入 MockTransport，生产保留 HTTPX 环境语义。"""
    transport = _business_transport.get()
    with httpx.Client(transport=transport, trust_env=transport is None) as client:
        return client.request(method, url, **kwargs)


def _biz_url() -> str:
    # 统一走 Settings.biz_base_url(env: BIZ_BASE_URL), 不直接读环境变量
    return (get_settings().biz_base_url or _DEFAULT_BIZ_URL).rstrip("/")


@tool(
    key="query_order",
    description="查询订单信息（按订单号或用户标识）。risk=low、幂等、超时10秒。",
    risk=RiskLevel.LOW,
    timeout_seconds=10.0,
    idempotent=True,
)
def query_order(order_id: str) -> dict:
    """按订单号查订单详情。

    参数:
        order_id: 订单号（如 ORD-1001）

    返回:
        订单 JSON（order_id/status/amount/refundable 等）；不存在返回 error。
    """
    url = f"{_biz_url()}/orders/{order_id}"
    try:
        resp = _request("GET", url, timeout=10.0)
    except httpx.RequestError as exc:
        raise LubanError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            f"查询订单连接失败: {exc}",
            details={"url": url},
            retryable=True,
        ) from None
    if resp.status_code == 404:
        raise LubanError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            f"订单不存在: {order_id}",
            details={"order_id": order_id},
        )
    if resp.status_code != 200:
        raise LubanError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            f"查询订单失败: HTTP {resp.status_code}",
            details={"order_id": order_id, "status_code": resp.status_code},
        )
    return resp.json()
