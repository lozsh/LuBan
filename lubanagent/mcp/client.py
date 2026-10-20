"""MCP Client: 接外部 MCP Server, 枚举远端工具并封装为 RegisteredTool 与本地同治理。"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent

from lubanagent.core import ErrorCode, LubanError, RiskLevel, ToolContract, ToolDescriptor
from lubanagent.extension import RegisteredTool


@dataclass(frozen=True)
class McpServerConfig:
    """一个外部 MCP Server 的连接配置; default_risk 作用于其全部远端工具。"""

    key: str
    name: str = ""
    transport: Literal["stdio", "http"] = "stdio"
    command: str | None = None  # stdio: 启动命令 (如 "npx")
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    url: str | None = None  # http: Streamable HTTP 端点
    timeout_seconds: float = 30.0
    default_risk: RiskLevel = RiskLevel.MEDIUM  # 远端默认中风险, 可按 server 覆盖


# 测试/集成注入缝: 返回一个 async context manager, 产出已初始化的 ClientSession
# (如 mcp.shared.memory.create_connected_server_and_client_session)。
SessionFactory = Callable[[], Any]


class _Connection:
    """一条长连的载体: 由专属 keeper 任务建立/收尾, 调用方只发信号。

    MCP 传输 CM (stdio/streamable-http/memory) 基于 anyio, 取消作用域绑定宿主任务,
    跨任务退出或"捕获取消后再 await 清理"都会破坏取消协议; 因此连接的 enter/exit
    全部收在 keeper 任务内, 调用任务经 Event 请求建立/废弃。
    """

    def __init__(self) -> None:
        self.session: ClientSession | None = None  # 建立成功后有效
        self.build_error: BaseException | None = None  # 建立失败原因
        self.ready = asyncio.Event()  # 建立完成 (成功或失败) 置位
        self.closed = asyncio.Event()  # 请求废弃 (keeper 收到即收尾)
        self.done = asyncio.Event()  # keeper 收尾完成 (资源已释放)
        self.task: asyncio.Task[None] | None = None  # keeper 任务句柄


class McpClient:
    """接一个外部 MCP Server。

    长连复用 (D1-9 性能项落地): 首个调用触发建立会话 (stdio 子进程 + initialize
    握手, 或 streamable-http 建连) 并缓存, 后续调用复用; 调用抛非 LubanError 异常
    (连接断/超时/取消) 时废弃连接, 下次调用重建 (自愈, server 重启后不卡死坏连接);
    显式 close() 释放连接供应用 shutdown 钩子调用。

    连接生命周期由专属 keeper 后台任务持有 (anyio 取消作用域任务绑定, 见
    _Connection docstring), 调用任务可与建立任务不同 (FastAPI 每请求一个任务的
    场景下复用仍安全)。

    并发决策: 同一 client 的调用经 asyncio.Lock 串行化——stdio 是单管道、http 是
    单会话, 串行化实现最简且不引入请求交错/管道竞争问题; 需要真并行请为同一
    server 建多个 McpClient 实例 (实例间无共享锁)。
    """

    def __init__(
        self, config: McpServerConfig, *, session_factory: SessionFactory | None = None
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        self._conn: _Connection | None = None

    @property
    def config(self) -> McpServerConfig:
        return self._config

    async def list_tools(self) -> list[ToolDescriptor]:
        """枚举远端工具并映射为 ToolDescriptor; 不可达 -> CONFIG_ERROR 含 mcp_server key。"""
        try:
            result = await self._run(lambda session: session.list_tools())
        except LubanError:
            raise
        except Exception as exc:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"MCP Server {self._config.key} 不可达: {type(exc).__name__}",
                details={"mcp_server": self._config.key, "reason": str(exc)},
            ) from exc
        return [
            self._to_descriptor(tool.name, tool.description, tool.inputSchema)
            for tool in result.tools
        ]

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        """调远端工具; 任何失败 -> TOOL_EXECUTION_FAILED 含 mcp_server/tool, 不静默吞错。"""
        try:
            result = await self._run(lambda session: session.call_tool(tool_name, args))
        except LubanError:
            raise
        except Exception as exc:
            raise LubanError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                f"MCP 工具调用失败: {tool_name} ({type(exc).__name__})",
                details={
                    "mcp_server": self._config.key,
                    "tool": tool_name,
                    "reason": str(exc),
                },
            ) from exc
        if result.isError:
            raise LubanError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                f"MCP 工具远端报错: {tool_name}: {_content_text(result.content)}",
                details={"mcp_server": self._config.key, "tool": tool_name},
            )
        return _content_value(result.content)

    def as_registered_tool(self, descriptor: ToolDescriptor) -> RegisteredTool:
        """把远端工具 descriptor 封装为 RegisteredTool, handler 内部走 call_tool。"""
        prefix = f"mcp:{self._config.key}:"
        remote_name = (
            descriptor.key[len(prefix):] if descriptor.key.startswith(prefix) else descriptor.name
        )

        async def handler(**kwargs: Any) -> Any:
            return await self.call_tool(remote_name, kwargs)

        return RegisteredTool(descriptor=descriptor, handler=handler)

    async def close(self) -> None:
        """显式释放长连 (关闭子进程/HTTP 连接); 幂等, 供应用 shutdown 钩子调用。"""
        async with self._lock:
            await self._discard()

    async def _run(self, op: Callable[[ClientSession], Awaitable[Any]]) -> Any:
        """锁内取缓存会话执行 op; 非 LubanError 异常废弃连接 (下次重建) 后原样抛出。"""
        async with self._lock:
            session = await self._get_session()
            try:
                return await op(session)
            except LubanError:
                # 远端业务报错 (result.isError 已转 LubanError), 连接仍健康, 不废弃
                raise
            except asyncio.CancelledError:
                # 取消路径 (治理超时/上层取消): 过期的 anyio 取消作用域内任何 await
                # 都会立刻再被取消, 不能同步等待收尾; 只发废弃信号, keeper 后台收尾
                self._poison()
                raise
            except BaseException:
                # 连接断/超时等: 同步等 keeper 收尾完成后废弃, 下次调用重建自愈
                await self._discard()
                raise

    async def _get_session(self) -> ClientSession:
        """取活跃会话; 无则触发 keeper 建立并等待就绪 (建立失败原样抛, 下次重试)。"""
        while True:
            conn = self._conn
            if conn is None:
                conn = _Connection()
                self._conn = conn
                conn.task = asyncio.create_task(
                    self._keep(conn), name=f"mcp-conn:{self._config.key}"
                )
            if conn.session is not None:
                return conn.session
            try:
                await conn.ready.wait()
            except asyncio.CancelledError:
                # 等待建立期间被取消: 标记废弃并摘除缓存, keeper 建立完成即拆且
                # 下次调用不再命中这条半建连接（否则首调失败一次才自愈）
                conn.closed.set()
                if self._conn is conn:
                    self._conn = None
                raise
            if conn.build_error is not None:
                self._conn = None  # 建立失败: 丢弃这条连接, 下次调用重试
                raise conn.build_error
            if conn.session is not None:
                return conn.session
            # ready 置位但既无 session 也无 error: 连接已被并发废弃, 重取
            self._conn = None

    async def _keep(self, conn: _Connection) -> None:
        """keeper 任务: 在本任务内建立并持有连接, 收到废弃信号后在本任务内收尾。"""
        try:
            async with AsyncExitStack() as stack:
                try:
                    session = await self._enter_session(stack)
                except BaseException as exc:
                    conn.build_error = exc
                    return  # async with 退出时清理半开连接
                conn.session = session
                conn.ready.set()
                await conn.closed.wait()  # 等废弃信号; 正常路径经 close/出错/取消触发
        except BaseException as exc:  # 收尾异常兜底记录, 避免任务异常静默丢失
            if conn.build_error is None:
                conn.build_error = exc
        finally:
            conn.ready.set()  # 建立失败也要放行等待者
            conn.done.set()

    async def _discard(self) -> None:
        """废弃当前连接: 通知 keeper 收尾并等待资源释放完成。"""
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.closed.set()
            await conn.done.wait()

    def _poison(self) -> None:
        """取消路径的废弃: 只发信号不等收尾 (调用任务已处于过期取消作用域, 不能再 await)。"""
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.closed.set()

    async def _enter_session(self, stack: AsyncExitStack) -> ClientSession:
        """把会话的完整建立链挂进 exit stack; 返回已 initialize 的 ClientSession。"""
        if self._session_factory is not None:
            return await stack.enter_async_context(self._session_factory())
        if self._config.transport == "stdio":
            if not self._config.command:
                raise LubanError(
                    ErrorCode.CONFIG_ERROR,
                    f"MCP Server {self._config.key} 缺少 stdio command",
                    details={"mcp_server": self._config.key},
                )
            params = StdioServerParameters(
                command=self._config.command, args=self._config.args, env=self._config.env
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session
        if not self._config.url:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"MCP Server {self._config.key} 缺少 http url",
                details={"mcp_server": self._config.key},
            )
        read, write, _ = await stack.enter_async_context(streamablehttp_client(self._config.url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    def _to_descriptor(
        self, name: str, description: str | None, input_schema: dict[str, Any]
    ) -> ToolDescriptor:
        schema = dict(input_schema or {})
        return ToolDescriptor(
            key=f"mcp:{self._config.key}:{name}",
            name=name,
            description=description or "",
            args_schema=schema,
            risk=self._config.default_risk,
            contract=ToolContract(timeout_seconds=self._config.timeout_seconds),
            signature_hash=hashlib.sha256(
                json.dumps(schema, sort_keys=True, default=str).encode()
            ).hexdigest()[:16],
        )


def _content_text(content: list[Any]) -> str:
    return "\n".join(block.text for block in content if isinstance(block, TextContent))


def _content_value(content: list[Any]) -> Any:
    """文本 block 串联为字符串; 含非文本 block 时逐块返回 (text 或 model_dump dict)。"""
    if all(isinstance(block, TextContent) for block in content):
        return "\n".join(block.text for block in content)
    return [
        block.text if isinstance(block, TextContent) else block.model_dump() for block in content
    ]
