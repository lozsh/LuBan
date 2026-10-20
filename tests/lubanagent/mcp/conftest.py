"""mcp 测试公共件: 内存对远端 Server 与 session_factory, 零网络零子进程。"""

import asyncio
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session


def make_remote_server() -> FastMCP:
    """远端 MCP Server: add(加法) / info(返回 dict) / slow(慢) / boom(内部抛错)。"""
    server = FastMCP("remote")

    @server.tool()
    def add(a: int, b: int = 0) -> int:
        return a + b

    @server.tool()
    def info(seed: int) -> dict[str, Any]:
        return {"seed": seed, "double": seed * 2}

    @server.tool()
    async def slow() -> str:
        await asyncio.sleep(5)
        return "done"

    @server.tool()
    def boom() -> str:
        raise RuntimeError("remote exploded")

    return server


def memory_session_factory(server: FastMCP) -> Callable[[], Any]:
    """产出已初始化 ClientSession 的 async CM 工厂, 注入 McpClient(session_factory=...)。"""
    return lambda: create_connected_server_and_client_session(server)


def counting_session_factory(server: FastMCP, opens: list[int]) -> Callable[[], Any]:
    """包一层计数: 每次真正建会话往 opens 追加一条, 用于断言长连复用/重建次数。"""
    inner = memory_session_factory(server)

    def factory() -> Any:
        opens.append(1)
        return inner()

    return factory
