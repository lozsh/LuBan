"""Client 封装: 远端工具 -> ToolDescriptor -> RegisteredTool -> ToolExecutor 同治理 + 长连复用。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

from lubanagent.core import ErrorCode, LubanError, RiskLevel
from lubanagent.extension import Registry
from lubanagent.mcp import McpClient, McpServerConfig
from lubanagent.tool import ToolExecutor

from .conftest import counting_session_factory, make_remote_server, memory_session_factory


def _client(**overrides) -> McpClient:
    config = McpServerConfig(key="calc", **overrides)
    return McpClient(config, session_factory=memory_session_factory(make_remote_server()))


async def test_list_tools_maps_remote_tools_to_descriptors() -> None:
    server = make_remote_server()
    client = McpClient(
        McpServerConfig(key="calc", timeout_seconds=7.0),
        session_factory=memory_session_factory(server),
    )
    descriptors = {d.key: d for d in await client.list_tools()}

    assert set(descriptors) == {
        "mcp:calc:add",
        "mcp:calc:info",
        "mcp:calc:slow",
        "mcp:calc:boom",
    }
    add = descriptors["mcp:calc:add"]
    remote_schema = next(t for t in (await server.list_tools()) if t.name == "add").inputSchema
    assert add.args_schema == remote_schema
    assert add.risk == RiskLevel.MEDIUM  # 远端默认中风险
    assert add.contract.timeout_seconds == 7.0
    assert len(add.signature_hash) == 16


async def test_default_risk_overridable_per_server() -> None:
    client = _client(default_risk=RiskLevel.LOW)
    descriptors = await client.list_tools()
    assert all(d.risk == RiskLevel.LOW for d in descriptors)


async def test_registered_mcp_tool_executes_through_tool_executor() -> None:
    client = _client()
    reg = Registry()
    for descriptor in await client.list_tools():
        registered = client.as_registered_tool(descriptor)
        reg.register_tool(registered.descriptor, registered.handler)

    add_result = await ToolExecutor().execute_by_key("mcp:calc:add", {"a": 2, "b": 3}, registry=reg)
    assert add_result.success
    assert add_result.value == "5"  # 远端文本 content 串联

    info_result = await ToolExecutor().execute_by_key("mcp:calc:info", {"seed": 4}, registry=reg)
    assert info_result.success
    assert json.loads(info_result.value) == {"seed": 4, "double": 8}


async def test_governance_timeout_applies_to_remote_tool() -> None:
    client = _client(timeout_seconds=0.2)
    reg = Registry()
    for descriptor in await client.list_tools():
        registered = client.as_registered_tool(descriptor)
        reg.register_tool(registered.descriptor, registered.handler)

    result = await ToolExecutor().execute_by_key("mcp:calc:slow", {}, registry=reg)
    assert not result.success
    assert result.error["code"] == ErrorCode.RUN_TIMEOUT.value


async def test_governance_validation_failure_never_hits_remote() -> None:
    client = _client()
    reg = Registry()
    for descriptor in await client.list_tools():
        registered = client.as_registered_tool(descriptor)
        reg.register_tool(registered.descriptor, registered.handler)

    result = await ToolExecutor().execute_by_key("mcp:calc:add", {}, registry=reg)  # 缺 required a
    assert not result.success
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR.value
    assert result.attempts == 0  # 校验阶段即失败, 未进入执行


async def test_remote_error_is_explicit_luban_error() -> None:
    client = _client()
    with pytest.raises(LubanError) as exc_info:
        await client.call_tool("boom", {})
    assert exc_info.value.code == ErrorCode.TOOL_EXECUTION_FAILED
    assert exc_info.value.details["mcp_server"] == "calc"
    assert exc_info.value.details["tool"] == "boom"


async def test_remote_error_survives_executor_wrapping() -> None:
    client = _client()
    reg = Registry()
    for descriptor in await client.list_tools():
        registered = client.as_registered_tool(descriptor)
        reg.register_tool(registered.descriptor, registered.handler)

    result = await ToolExecutor().execute_by_key("mcp:calc:boom", {}, registry=reg)
    assert not result.success
    assert result.error["code"] == ErrorCode.TOOL_EXECUTION_FAILED.value
    assert result.error["details"]["mcp_server"] == "calc"
    assert result.error["details"]["tool"] == "boom"


async def test_missing_command_is_config_error() -> None:
    client = McpClient(McpServerConfig(key="broken", transport="stdio"))
    with pytest.raises(LubanError) as exc_info:
        await client.list_tools()
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert exc_info.value.details["mcp_server"] == "broken"


# ---- 长连复用 / 出错重连 / close 清理 (D1-9) ----


async def test_session_reused_across_calls() -> None:
    """首次调用建会话缓存, 后续调用复用, 不重复握手。"""
    server = make_remote_server()
    opens: list[int] = []
    client = McpClient(
        McpServerConfig(key="calc"), session_factory=counting_session_factory(server, opens)
    )
    await client.list_tools()
    assert await client.call_tool("add", {"a": 1, "b": 2}) == "3"
    assert await client.call_tool("add", {"a": 4}) == "4"
    await client.list_tools()
    assert len(opens) == 1  # 四次调用只建一次会话


async def test_broken_session_discarded_and_rebuilt() -> None:
    """连接级异常 (连接断/超时) 废弃坏会话并清理底层传输, 下次调用重建自愈。"""
    sessions: list[_FakeSession] = []
    cms: list[_FakeCM] = []

    def factory() -> _FakeCM:
        session = _FakeSession()
        cm = _FakeCM(session)
        sessions.append(session)
        cms.append(cm)
        return cm

    client = McpClient(McpServerConfig(key="fake"), session_factory=factory)
    await client.list_tools()  # 建会话 s1
    sessions[0].broken = True  # 模拟 server 进程死亡

    with pytest.raises(LubanError) as exc_info:
        await client.call_tool("t", {})
    assert exc_info.value.code == ErrorCode.TOOL_EXECUTION_FAILED
    assert cms[0].exited is True  # 坏会话的传输被清理

    await client.list_tools()  # 自愈: 重建 s2
    assert len(sessions) == 2
    assert sessions[1].calls == 1


async def test_remote_tool_error_keeps_session_alive() -> None:
    """远端业务报错 (isError) 不是连接故障, 会话继续复用不重建。"""
    opens: list[int] = []
    client = _fake_result_client(opens)
    with pytest.raises(LubanError) as first:
        await client.call_tool("boom", {})
    assert first.value.code == ErrorCode.TOOL_EXECUTION_FAILED
    assert await client.call_tool("ok", {}) == "ok:2"
    assert len(opens) == 1  # 会话未废弃


async def test_close_releases_session_and_is_idempotent() -> None:
    """close 显式释放长连; 幂等; 释放后下次调用重建。"""
    sessions: list[_FakeSession] = []
    cms: list[_FakeCM] = []

    def factory() -> _FakeCM:
        session = _FakeSession()
        cm = _FakeCM(session)
        sessions.append(session)
        cms.append(cm)
        return cm

    client = McpClient(McpServerConfig(key="fake"), session_factory=factory)
    await client.list_tools()
    await client.close()
    assert cms[0].exited is True

    await client.close()  # 幂等
    assert len(cms) == 1

    await client.list_tools()  # close 后重建
    assert len(cms) == 2


async def test_cancelled_call_discards_session_for_self_heal() -> None:
    """治理超时取消在途调用后旧会话废弃, 下次调用重建, 不卡死坏连接。"""
    server = make_remote_server()
    opens: list[int] = []
    client = McpClient(
        McpServerConfig(key="calc"), session_factory=counting_session_factory(server, opens)
    )
    assert await client.call_tool("add", {"a": 1}) == "1"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(client.call_tool("slow", {}), timeout=0.1)
    assert await client.call_tool("add", {"a": 2, "b": 3}) == "5"  # 自愈
    assert len(opens) == 2  # 取消后会话重建


async def test_concurrent_calls_serialized_on_one_session() -> None:
    """并发调用同一 client: 锁串行化, 全部成功且只建一次会话。"""
    server = make_remote_server()
    opens: list[int] = []
    client = McpClient(
        McpServerConfig(key="calc"), session_factory=counting_session_factory(server, opens)
    )
    results = await asyncio.gather(
        client.call_tool("add", {"a": 1, "b": 1}),
        client.call_tool("add", {"a": 2, "b": 2}),
        client.call_tool("add", {"a": 3, "b": 3}),
    )
    assert results == ["2", "4", "6"]
    assert len(opens) == 1


class _FakeSession:
    """纯内存假会话: 计调用次数, broken 后抛连接级异常模拟 server 死亡。"""

    def __init__(self) -> None:
        self.calls = 0
        self.broken = False

    async def list_tools(self) -> SimpleNamespace:
        self.calls += 1
        if self.broken:
            raise ConnectionError("server gone")
        return SimpleNamespace(tools=[])

    async def call_tool(self, name: str, args: dict) -> SimpleNamespace:
        self.calls += 1
        if self.broken:
            raise ConnectionError("server gone")
        text = "remote exploded" if name == "boom" else f"{name}:{self.calls}"
        return SimpleNamespace(isError=name == "boom", content=_text_blocks(text))


class _FakeCM:
    """包 _FakeSession 的 async CM, exited 标记底层是否被清理。"""

    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.exited = False

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *exc_info: object) -> bool:
        self.exited = True
        return False


def _fake_result_client(opens: list[int]) -> McpClient:
    """会话永远健康的 client (call_tool 走 ok/boom 假结果), 用于断言会话不被误废弃。"""

    def factory() -> _FakeCM:
        opens.append(1)
        return _FakeCM(_FakeSession())

    return McpClient(McpServerConfig(key="fake"), session_factory=factory)


def _text_blocks(text: str) -> list[object]:
    return [TextContent(type="text", text=text)]
