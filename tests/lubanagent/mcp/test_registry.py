"""McpRegistry: DB 记录登记、工具发现注册、client 长连复用与 aclose 清理。"""

import pytest

from lubanagent.core import ErrorCode, LubanError, McpServer
from lubanagent.extension import Registry
from lubanagent.mcp import McpClient, McpRegistry, McpServerConfig
from lubanagent.tool import ToolExecutor

from .conftest import counting_session_factory, make_remote_server, memory_session_factory


class StubStore:
    """McpServerStore 的内存 stub: 返回 McpServer 领域对象 (与 persistence 仓储同形)。"""

    def __init__(self, records: list[McpServer]) -> None:
        self._records = records

    async def list(self) -> list[McpServer]:
        return list(self._records)


def _records() -> list[McpServer]:
    return [
        McpServer(
            key="calc",
            name="Calc",
            transport="stdio",
            config={"command": "python", "args": ["-m", "calc_server"]},
            enabled=True,
        ),
        McpServer(
            key="disabled_one",
            name="Off",
            transport="http",
            config={"url": "http://127.0.0.1:9/mcp"},
            enabled=False,
        ),
    ]


async def test_load_from_db_registers_enabled_records_only() -> None:
    mcp_reg = McpRegistry(repository=StubStore(_records()))
    count = await mcp_reg.load_from_db()
    assert count == 1
    servers = mcp_reg.list_servers()
    assert [s.key for s in servers] == ["calc"]
    assert servers[0].command == "python"
    assert servers[0].args == ["-m", "calc_server"]


async def test_load_from_db_without_repository_returns_zero() -> None:
    assert await McpRegistry().load_from_db() == 0


async def test_discover_tools_registers_remote_tools_into_registry() -> None:
    server = make_remote_server()
    mcp_reg = McpRegistry(
        repository=StubStore(_records()),
        client_factory=lambda cfg: McpClient(cfg, session_factory=memory_session_factory(server)),
    )
    await mcp_reg.load_from_db()

    reg = Registry()
    descriptors = await mcp_reg.discover_tools("calc", into=reg)
    assert {d.key for d in descriptors} == {
        "mcp:calc:add",
        "mcp:calc:info",
        "mcp:calc:slow",
        "mcp:calc:boom",
    }
    result = await ToolExecutor().execute_by_key("mcp:calc:add", {"a": 1, "b": 2}, registry=reg)
    assert result.success
    assert result.value == "3"


async def test_duplicate_register_raises_config_error() -> None:
    mcp_reg = McpRegistry()
    mcp_reg.register(McpServerConfig(key="calc"))
    with pytest.raises(LubanError) as exc_info:
        mcp_reg.register(McpServerConfig(key="calc"))
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert exc_info.value.details["mcp_server"] == "calc"


async def test_discover_unknown_key_raises_config_error() -> None:
    with pytest.raises(LubanError) as exc_info:
        await McpRegistry().discover_tools("nope", into=Registry())
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert exc_info.value.details["mcp_server"] == "nope"


async def test_unreachable_server_raises_config_error_not_silent() -> None:
    mcp_reg = McpRegistry()
    mcp_reg.register(
        McpServerConfig(key="bad", command="luban-definitely-missing-binary-zzz")
    )
    with pytest.raises(LubanError) as exc_info:
        await mcp_reg.discover_tools("bad", into=Registry())
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert exc_info.value.details["mcp_server"] == "bad"


async def test_discover_reuses_cached_client_across_calls() -> None:
    """同一 server 多次 discover 只建一个 client, 长连跨 discover 复用。"""
    server = make_remote_server()
    opens: list[int] = []
    built: list[str] = []

    def client_factory(cfg: McpServerConfig) -> McpClient:
        built.append(cfg.key)
        return McpClient(cfg, session_factory=counting_session_factory(server, opens))

    mcp_reg = McpRegistry(repository=StubStore(_records()), client_factory=client_factory)
    await mcp_reg.load_from_db()

    first = await mcp_reg.discover_tools("calc", into=Registry())
    second = await mcp_reg.discover_tools("calc", into=Registry())
    assert built == ["calc"]  # client 只建一次
    assert [d.key for d in first] == [d.key for d in second]
    assert len(opens) == 1  # 会话也只建一次 (长连复用)


async def test_aclose_closes_cached_clients_and_next_discover_rebuilds() -> None:
    """aclose 释放全部缓存 client 的长连; 之后的 discover 重建。"""
    server = make_remote_server()
    opens: list[int] = []
    built: list[str] = []

    def client_factory(cfg: McpServerConfig) -> McpClient:
        built.append(cfg.key)
        return McpClient(cfg, session_factory=counting_session_factory(server, opens))

    mcp_reg = McpRegistry(repository=StubStore(_records()), client_factory=client_factory)
    await mcp_reg.load_from_db()
    await mcp_reg.discover_tools("calc", into=Registry())

    await mcp_reg.aclose()
    await mcp_reg.aclose()  # 幂等
    assert built == ["calc"]

    await mcp_reg.discover_tools("calc", into=Registry())
    assert built == ["calc", "calc"]  # aclose 后重建
    assert len(opens) == 2
