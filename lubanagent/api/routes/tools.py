"""tools 路由：工具清单与在线测试（debug_tool 直执行，不经 Runtime）。"""

from typing import Any

from fastapi import APIRouter

from lubanagent.api.deps import CtxDep
from lubanagent.core import ToolDescriptor
from lubanagent.tool import ToolResult, debug_tool

router = APIRouter(tags=["tools"])


@router.get("/tools", response_model=list[ToolDescriptor])
async def list_tools(ctx: CtxDep) -> list[ToolDescriptor]:
    return [registered.descriptor for registered in ctx.registry.list_tools()]


@router.post("/tools/{key}/test", response_model=ToolResult)
async def test_tool(key: str, ctx: CtxDep, body: dict[str, Any] | None = None) -> ToolResult:
    # 未注册 → LubanError(TOOL_NOT_FOUND) → 404（统一错误映射）
    registered = ctx.registry.get_tool(key)
    return await debug_tool(registered, body or {})
