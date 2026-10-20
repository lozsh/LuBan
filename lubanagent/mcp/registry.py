"""MCP Server 登记处: config 来自 DB (mcp_servers 表) 或直接注册, 发现工具进本地 Registry。"""

from collections.abc import Callable
from typing import Protocol

from lubanagent.core import ErrorCode, LubanError, McpServer, RiskLevel, ToolDescriptor
from lubanagent.extension import Registry, default_registry

from .client import McpClient, McpServerConfig


class McpServerStore(Protocol):
    """mcp_servers 表读取协议; persistence 的 DatabaseMcpServerStore 天然满足, 测试可 stub。"""

    async def list(self) -> list[McpServer]:
        """返回全部登记记录 (McpServer 领域对象)。"""
        ...


class McpRegistry:
    """MCP Server 登记处; 发现到的远端工具注册进 extension Registry 与本地 Tool 同治理。

    同一 server 的 McpClient 按 key 缓存, 长连跨多次 discover 复用;
    aclose() 关闭全部缓存 client, 供应用 shutdown 钩子调用。
    """

    def __init__(
        self,
        *,
        repository: McpServerStore | None = None,
        client_factory: Callable[[McpServerConfig], McpClient] | None = None,
    ) -> None:
        self._repository = repository
        self._client_factory = client_factory
        self._configs: dict[str, McpServerConfig] = {}
        self._clients: dict[str, McpClient] = {}

    def register(self, config: McpServerConfig) -> None:
        if config.key in self._configs:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"MCP Server 重复登记: {config.key}",
                details={"mcp_server": config.key},
            )
        self._configs[config.key] = config

    async def load_from_db(self) -> int:
        """从 mcp_servers 表读 enabled 记录并登记, 返回条数; repository 为 None -> 0。"""
        if self._repository is None:
            return 0
        count = 0
        for record in await self._repository.list():
            if not record.enabled:
                continue
            self.register(_config_from_record(record))
            count += 1
        return count

    async def discover_tools(
        self, key: str, *, into: Registry | None = None
    ) -> list[ToolDescriptor]:
        """枚举指定 server 的远端工具并注册进 Registry (缺省 default_registry)。"""
        config = self._configs.get(key)
        if config is None:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"MCP Server 未登记: {key}",
                details={"mcp_server": key},
            )
        reg = into if into is not None else default_registry
        client = self._client_for(config)
        try:
            descriptors = await client.list_tools()
        except LubanError:
            raise
        except Exception as exc:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"MCP Server {key} 不可达: {type(exc).__name__}",
                details={"mcp_server": key, "reason": str(exc)},
            ) from exc
        for descriptor in descriptors:
            if reg.has_tool(descriptor.key):
                # 幂等发现（H09）：同一 server 多轮 run / 多个 Runtime 共用时跳过已注册工具，
                # 不因重复注册抛 CONFIG_ERROR；服务端工具变更需重启进程重新发现。
                continue
            registered = client.as_registered_tool(descriptor)
            reg.register_tool(registered.descriptor, registered.handler)
        return descriptors

    def list_servers(self) -> list[McpServerConfig]:
        return list(self._configs.values())

    async def aclose(self) -> None:
        """关闭全部缓存 client 的长连; 幂等, 供应用 shutdown 钩子调用。"""
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.close()

    def _client_for(self, config: McpServerConfig) -> McpClient:
        """按 key 缓存 client: 长连跨多次 discover 复用, aclose 后下次 discover 重建。"""
        client = self._clients.get(config.key)
        if client is None:
            client = (
                self._client_factory(config)
                if self._client_factory is not None
                else McpClient(config)
            )
            self._clients[config.key] = client
        return client


def _config_from_record(record: McpServer) -> McpServerConfig:
    """McpServer 领域对象 -> McpServerConfig; 连接细节在 config 子字段。"""
    extra = dict(record.config or {})
    if "default_risk" in extra:
        extra["default_risk"] = RiskLevel(extra["default_risk"])
    return McpServerConfig(
        key=record.key,
        name=record.name,
        transport=record.transport,
        **extra,
    )
