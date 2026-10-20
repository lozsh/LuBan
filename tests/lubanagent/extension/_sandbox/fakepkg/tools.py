"""伪造用户包: 供 discover 测试自动发现注册。"""

from typing import Any

from lubanagent.core import RunContext, Step, ToolDescriptor
from lubanagent.extension import graph, hook, tool


@tool(key="fake.echo", description="回显", approval_required=True)
def echo(text: str, count: int = 1) -> str:
    return text * count


@graph(name="fake.flow", description="伪造图")
def build_flow() -> None:
    return None


@hook()
class FakeHook:
    async def pre_run(self, ctx: RunContext) -> None:
        return None

    async def pre_tool(self, ctx: RunContext, tool: ToolDescriptor, args: dict[str, Any]) -> None:
        return None

    async def post_step(self, ctx: RunContext, step: Step) -> None:
        return None
