"""工具独立调试与 Mock: 不起 Runtime 即可执行与验证单个工具。"""

from typing import Any

from lubanagent.core import ToolDescriptor
from lubanagent.extension import RegisteredTool

from .executor import ToolExecutor, ToolResult


class MockToolHandler:
    """同签名词典驱动的假 handler: 记录调用、返回罐头结果或抛罐头错误。"""

    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def make_mock_registered(
    descriptor: ToolDescriptor, *, result: Any = None, error: Exception | None = None
) -> RegisteredTool:
    """快速构造可注册/可执行的假 Tool (descriptor + MockToolHandler)。"""
    return RegisteredTool(
        descriptor=descriptor, handler=MockToolHandler(result=result, error=error)
    )


async def debug_tool(registered: RegisteredTool, args: dict[str, Any]) -> ToolResult:
    """一行调试入口: 等价 ToolExecutor 默认配置 execute (无权限策略、无审批)。"""
    return await ToolExecutor().execute(registered, args)
