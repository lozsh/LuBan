"""tools 路由：清单列出 + 在线测试（debug_tool 直执行）。"""

from lubanagent.core import RiskLevel, ToolContract, ToolDescriptor
from lubanagent.tool import make_mock_registered


def _echo_tool(registry, key: str = "echo", **descriptor_overrides):
    descriptor = ToolDescriptor(
        key=key,
        name=descriptor_overrides.pop("name", "回声工具"),
        description=descriptor_overrides.pop("description", "原样返回输入"),
        args_schema=descriptor_overrides.pop(
            "args_schema",
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        **descriptor_overrides,
    )
    registry.register_tool(descriptor, make_mock_registered(descriptor).handler)
    return descriptor


async def test_list_tools_empty(client):
    response = await client.get("/v1/tools")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_tools(client, registry):
    _echo_tool(registry, key="echo")
    _echo_tool(
        registry,
        key="refund",
        name="退款",
        risk=RiskLevel.HIGH,
        contract=ToolContract(timeout_seconds=10.0, idempotent=True),
    )
    response = await client.get("/v1/tools")
    assert response.status_code == 200
    tools = {tool["key"]: tool for tool in response.json()}
    assert set(tools) == {"echo", "refund"}
    assert tools["refund"]["risk"] == "high"
    assert tools["refund"]["contract"]["idempotent"] is True
    assert tools["echo"]["args_schema"]["required"] == ["text"]


async def test_tool_test_success(client, registry):
    _echo_tool(registry)
    response = await client.post("/v1/tools/echo/test", json={"text": "你好"})
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["error"] is None
    assert result["attempts"] == 1
    assert result["duration_ms"] >= 0


async def test_tool_test_not_found(client):
    response = await client.post("/v1/tools/ghost/test", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TOOL_NOT_FOUND"


async def test_tool_test_invalid_args(client, registry):
    _echo_tool(registry)
    response = await client.post("/v1/tools/echo/test", json={"wrong": 1})
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert result["attempts"] == 0


async def test_tool_test_handler_error(client, registry):
    descriptor = ToolDescriptor(key="boom", name="爆炸工具")
    registry.register_tool(
        descriptor,
        make_mock_registered(descriptor, error=RuntimeError("炸了")).handler,
    )
    response = await client.post("/v1/tools/boom/test", json={})
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is False
    assert result["error"]["code"] == "TOOL_EXECUTION_FAILED"
