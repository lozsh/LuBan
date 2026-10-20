"""MCP Server: 把 Registry 里的本地 Tool 经 FastMCP 反向暴露给外部 MCP Client。"""

import hmac
import inspect
from collections.abc import Callable, Collection
from typing import Any, Literal

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from lubanagent.core import ErrorCode, LubanError, RunContext
from lubanagent.extension import RegisteredTool, Registry, default_registry
from lubanagent.tool import ToolExecutor

_JSON_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class _StaticTokenVerifier:
    """仅供独立 MCP HTTP 入口使用，不借用 REST 的鉴权配置。"""

    def __init__(self, key: str | None) -> None:
        self._key = key

    async def verify_token(self, token: str) -> AccessToken | None:
        if self._key is None or not hmac.compare_digest(token, self._key):
            return None
        return AccessToken(token=token, client_id="mcp-key-holder", scopes=[])


def create_mcp_server(
    *,
    registry: Registry | None = None,
    name: str = "lubanagent",
    export_tools: Collection[str] | None = None,
    executor: ToolExecutor | None = None,
    ctx: RunContext | None = None,
    http_api_key: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """只暴露显式列出的本地工具；执行统一进入 ToolExecutor。"""
    reg = registry if registry is not None else default_registry
    if http_api_key is not None and not http_api_key.strip():
        raise LubanError(ErrorCode.CONFIG_ERROR, "MCP HTTP Key 不能为空")
    # 即使调用者直接把 streamable_http_app() 挂到 ASGI，未配置 Key 也一律拒绝。
    auth = AuthSettings(
        issuer_url=f"http://{host}:{port}/",
        resource_server_url=f"http://{host}:{port}/mcp",
    )
    server = FastMCP(
        name, host=host, port=port, auth=auth,
        token_verifier=_StaticTokenVerifier(http_api_key),
    )
    allowed = set(export_tools or ())
    tool_executor = executor if executor is not None else ToolExecutor()
    for registered in reg.list_tools():
        if registered.descriptor.key not in allowed or registered.descriptor.key.startswith("mcp:"):
            continue
        descriptor = registered.descriptor
        server.add_tool(
            _build_tool_fn(registered, tool_executor, ctx),
            name=descriptor.key,
            description=descriptor.description or None,
            structured_output=False,  # 返回值统一序列化为文本 content, 行为确定
        )
    return server


def run_server(*, transport: Literal["stdio", "http"] = "stdio", **kw: Any) -> None:
    """stdio 为受信本地入口；HTTP 未明确配置独立 Key 则拒绝启动。"""
    if transport == "http" and not kw.get("http_api_key"):
        raise LubanError(ErrorCode.CONFIG_ERROR, "MCP HTTP 必须配置独立 http_api_key")
    server = create_mcp_server(**kw)
    if transport == "stdio":
        server.run()
    elif transport == "http":
        server.run(transport="streamable-http")
    else:
        raise LubanError(
            ErrorCode.CONFIG_ERROR,
            f"未知 MCP 传输方式: {transport}",
            details={"transport": transport},
        )


def _build_tool_fn(
    registered: RegisteredTool, executor: ToolExecutor, ctx: RunContext | None
) -> Callable[..., Any]:
    descriptor = registered.descriptor

    async def fn(**kwargs: Any) -> Any:
        result = await executor.execute(registered, kwargs, ctx=ctx)
        if not result.success:
            error = result.error or {}
            raise ToolError(
                f"{error.get('code', 'TOOL_EXECUTION_FAILED')}: "
                f"{error.get('message', '工具执行失败')}"
            )
        return result.value

    fn.__signature__ = _signature_from_schema(descriptor.args_schema)  # type: ignore[attr-defined]
    fn.__name__ = descriptor.name
    fn.__doc__ = descriptor.description or None
    return fn


def _signature_from_schema(schema: dict[str, Any]) -> inspect.Signature:
    """按 JSON Schema properties/required/default 动态生成带注解的关键字参数签名。"""
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    params: list[inspect.Parameter] = []
    for prop, spec in properties.items():
        spec = spec or {}
        annotation: Any = _JSON_TYPES.get(spec.get("type", ""), Any)
        if prop in required:
            default = inspect.Parameter.empty
        elif "default" in spec:
            default = spec["default"]
        else:
            annotation = annotation | None
            default = None
        params.append(
            inspect.Parameter(
                prop, inspect.Parameter.KEYWORD_ONLY, annotation=annotation, default=default
            )
        )
    return inspect.Signature(params, return_annotation=Any)
