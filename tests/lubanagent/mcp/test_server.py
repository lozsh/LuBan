"""反向暴露: Registry 本地 Tool -> FastMCP Server -> 官方 client 内存对端到端调用。"""

import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from lubanagent.core import RiskLevel, ToolContract, ToolDescriptor
from lubanagent.extension import Registry, tool
from lubanagent.mcp import create_mcp_server, run_server
from lubanagent.tool import MockToolHandler, ToolExecutor


def _build_registry() -> Registry:
    reg = Registry()

    @tool(registry=reg)
    def add(a: int, b: int) -> int:
        return a + b

    @tool(registry=reg)
    def stats(seed: int) -> dict[str, int]:
        return {"seed": seed, "double": seed * 2}

    # mcp: 前缀的远端封装工具不应被反向暴露
    reg.register_tool(
        ToolDescriptor(key="mcp:ext:whatever", name="whatever"), MockToolHandler(result=None)
    )
    return reg


async def test_local_tools_listed_and_remote_prefixed_filtered() -> None:
    server = create_mcp_server(
        registry=_build_registry(), name="expose", export_tools={"add", "stats"}
    )
    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()
    names = {t.name for t in listed.tools}
    assert names == {"add", "stats"}


async def test_call_tool_results_match_direct_invocation() -> None:
    reg = _build_registry()
    server = create_mcp_server(registry=reg, export_tools={"add", "stats"})
    async with create_connected_server_and_client_session(server) as session:
        add_result = await session.call_tool("add", {"a": 2, "b": 3})
        stats_result = await session.call_tool("stats", {"seed": 4})

    assert not add_result.isError
    assert add_result.content[0].text == str(reg.get_tool("add").handler(2, 3))

    assert not stats_result.isError
    assert json.loads(stats_result.content[0].text) == reg.get_tool("stats").handler(4)


async def test_default_empty_export_and_unlisted_tool_never_executes() -> None:
    registry = _build_registry()
    counting = MockToolHandler(result="called")
    registry.register_tool(ToolDescriptor(key="hidden", name="hidden"), counting)
    for exports in (None, {"add"}):
        server = create_mcp_server(registry=registry, export_tools=exports)
        async with create_connected_server_and_client_session(server) as session:
            listed = await session.list_tools()
            assert {t.name for t in listed.tools} == (set() if exports is None else {"add"})
            rejected = await session.call_tool("hidden", {})
            assert rejected.isError
        assert counting.calls == []


@pytest.mark.parametrize("high,approval_required", [(True, False), (False, True)])
@pytest.mark.parametrize("decision", [None, False, True])
async def test_governed_approval_paths(high, approval_required, decision) -> None:
    registry = Registry()
    handler = MockToolHandler(result="ok")
    descriptor = ToolDescriptor(
        key="sensitive", name="sensitive", risk=RiskLevel.HIGH if high else RiskLevel.LOW,
        contract=ToolContract(approval_required=approval_required),
    )
    registry.register_tool(descriptor, handler)
    checked = []

    async def approve(tool, args):
        checked.append((tool.key, args))
        return decision

    executor = ToolExecutor(approval_handler=approve) if decision is not None else ToolExecutor()
    server = create_mcp_server(registry=registry, export_tools={"sensitive"}, executor=executor)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("sensitive", {})
    assert result.isError is (decision is not True)
    assert len(handler.calls) == int(decision is True)
    assert checked == ([] if decision is None else [("sensitive", {})])


async def test_mcp_permissions_and_arguments_are_checked() -> None:
    class DenyAll:
        async def check(self, ctx, tool, args):
            from lubanagent.core import ErrorCode, LubanError
            raise LubanError(ErrorCode.TOOL_PERMISSION_DENIED, "denied")

    registry = _build_registry()
    server = create_mcp_server(
        registry=registry, export_tools={"add"}, executor=ToolExecutor(permission_policy=DenyAll())
    )
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("add", {"a": 2, "b": 3})
    assert result.isError
    assert "denied" in result.content[0].text


def test_http_without_explicit_key_is_rejected_before_run(monkeypatch) -> None:
    from mcp.server.fastmcp import FastMCP

    monkeypatch.setattr(FastMCP, "run", lambda *args, **kwargs: pytest.fail("must not start"))
    from lubanagent.core import ErrorCode, LubanError

    with pytest.raises(LubanError) as exc:
        run_server(transport="http", registry=_build_registry(), export_tools={"add"})
    assert exc.value.code == ErrorCode.CONFIG_ERROR


@pytest.mark.parametrize("key", [None, "synthetic-mcp-only-key"])
async def test_http_requires_independent_bearer_before_handler(key) -> None:
    registry = Registry()
    counting = MockToolHandler(result="ok")
    registry.register_tool(ToolDescriptor(key="safe", name="safe"), counting)
    server = create_mcp_server(
        registry=registry, export_tools={"safe"}, http_api_key=key
    )
    app = server.streamable_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for headers in ({}, {"Authorization": "Bearer incorrect"}):
            response = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "safe", "arguments": {}}}, headers=headers,
            )
            assert response.status_code == 401
    assert counting.calls == []


async def test_http_authenticated_client_can_call_exported_tool(isolated_test_process) -> None:
    registry = Registry()
    counting = MockToolHandler(result="ok")
    registry.register_tool(ToolDescriptor(key="safe", name="safe"), counting)
    server = create_mcp_server(
        registry=registry, export_tools={"safe"}, http_api_key="synthetic-mcp-only-key"
    )
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    isolated_test_process[0].add(8100)  # ASGI 内存传输，不绑定真实端口
    headers = {
        "Authorization": "Bearer synthetic-mcp-only-key",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    async with server.session_manager.run():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8100"
        ) as client:
            initialized = await client.post(
                "/mcp", headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                }},
            )
            assert initialized.status_code == 200, initialized.text
            assert "synthetic-mcp-only-key" not in initialized.text
            session_id = initialized.headers.get("mcp-session-id")
            if session_id:
                headers["mcp-session-id"] = session_id
            response = await client.post(
                "/mcp", headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "safe", "arguments": {},
                }},
            )
    assert response.status_code == 200, response.text
    assert len(counting.calls) == 1
