"""Registry 行为: 注册-取回一轮、重复报错、未注册报错、mock ToolProvider 入默认表。"""

from typing import Any

import pytest

from lubanagent.core import ErrorCode, LubanError, ToolDescriptor
from lubanagent.extension import (
    AuthProvider,
    AuthRequest,
    GraphDescriptor,
    KnowledgeConnector,
    ObservabilityBackend,
    Principal,
    Registry,
    Snippet,
    default_registry,
)


class MockToolProvider:
    def tools(self) -> list[ToolDescriptor]:
        return [ToolDescriptor(key="mock_provider.echo", name="echo")]


class MockAuth:
    async def verify(self, request: AuthRequest) -> Principal:
        return Principal(subject="tester")


class MockObservability:
    @property
    def name(self) -> str:
        return "mock-obs"

    def start_trace(self, run: Any) -> object:
        return object()

    def emit(self, handle: Any, event: Any) -> None:
        return None

    def end_trace(self, handle: Any, status: Any, summary: dict[str, Any] | None = None) -> None:
        return None


class MockKnowledge:
    async def retrieve(self, query: str, top_k: int) -> list[Snippet]:
        return [Snippet(content=query, source="mock")]


def _echo_handler(text: str) -> str:
    return text


def test_mock_tool_provider_registered_into_default_registry() -> None:
    provider = MockToolProvider()
    for descriptor in provider.tools():
        if not default_registry.has_tool(descriptor.key):
            default_registry.register_tool(descriptor, _echo_handler)
    registered = default_registry.get_tool("mock_provider.echo")
    assert registered.descriptor.name == "echo"
    assert registered.handler("hi") == "hi"


def test_register_tool_duplicate_raises_config_error() -> None:
    reg = Registry()
    reg.register_tool(ToolDescriptor(key="t1", name="t1"), _echo_handler)
    with pytest.raises(LubanError) as exc_info:
        reg.register_tool(ToolDescriptor(key="t1", name="t1b"), _echo_handler)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_get_tool_missing_raises_tool_not_found() -> None:
    reg = Registry()
    assert not reg.has_tool("nope")
    with pytest.raises(LubanError) as exc_info:
        reg.get_tool("nope")
    assert exc_info.value.code == ErrorCode.TOOL_NOT_FOUND


def test_graph_register_get_list() -> None:
    reg = Registry()
    descriptor = GraphDescriptor(name="g1", description="图", builder=lambda: None)
    reg.register_graph(descriptor)
    assert reg.get_graph("g1") is descriptor
    assert reg.list_graphs() == [descriptor]
    with pytest.raises(LubanError) as exc_info:
        reg.register_graph(GraphDescriptor(name="g1", builder=lambda: None))
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    with pytest.raises(LubanError) as exc_info:
        reg.get_graph("nope")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_auth_register_get_default_none() -> None:
    reg = Registry()
    assert reg.get_auth() is None
    provider = MockAuth()
    reg.register_auth(provider)
    assert reg.get_auth() is provider
    assert isinstance(provider, AuthProvider)


def test_observability_register_get_default_none() -> None:
    reg = Registry()
    assert reg.get_observability() is None
    backend = MockObservability()
    reg.register_observability(backend)
    assert reg.get_observability() is backend
    assert isinstance(backend, ObservabilityBackend)


def test_observability_named_backends() -> None:
    reg = Registry()
    assert reg.get_observability_backend("mycorp") is None
    assert reg.list_observability_backends() == []
    backend = MockObservability()
    reg.register_observability_backend("mycorp", backend)
    assert reg.get_observability_backend("mycorp") is backend
    assert reg.list_observability_backends() == ["mycorp"]
    with pytest.raises(LubanError) as exc_info:
        reg.register_observability_backend("mycorp", MockObservability())
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_knowledge_register_get_list() -> None:
    reg = Registry()
    connector = MockKnowledge()
    reg.register_knowledge("faq", connector)
    assert reg.get_knowledge("faq") is connector
    assert reg.list_knowledge() == ["faq"]
    assert isinstance(connector, KnowledgeConnector)
    with pytest.raises(LubanError) as exc_info:
        reg.register_knowledge("faq", connector)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    with pytest.raises(LubanError) as exc_info:
        reg.get_knowledge("nope")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_knowledge_unregister_idempotent() -> None:
    reg = Registry()
    connector = MockKnowledge()
    reg.register_knowledge("faq", connector)
    reg.unregister_knowledge("faq")
    assert "faq" not in reg.list_knowledge()
    with pytest.raises(LubanError) as exc_info:
        reg.get_knowledge("faq")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
    # 幂等：未注册的名字不报错
    reg.unregister_knowledge("faq")
    reg.unregister_knowledge("never-registered")
