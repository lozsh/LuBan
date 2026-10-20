"""extension: 扩展点 Protocol、Registry 与注册装饰器, 框架与用户领地的桥梁。"""

from .decorators import discover, discover_packages, graph, hook, tool
from .protocols import (
    AuthProvider,
    AuthRequest,
    ChannelConnector,
    GraphDescriptor,
    GraphProvider,
    GuardVerdict,
    Hook,
    KnowledgeConnector,
    ObservabilityBackend,
    OutputGuard,
    Principal,
    SessionState,
    SessionStateStore,
    Snippet,
    ToolProvider,
    TraceHandle,
)
from .registry import RegisteredTool, Registry, default_registry

__all__ = [
    "AuthProvider",
    "AuthRequest",
    "ChannelConnector",
    "GraphDescriptor",
    "GraphProvider",
    "GuardVerdict",
    "Hook",
    "KnowledgeConnector",
    "ObservabilityBackend",
    "OutputGuard",
    "Principal",
    "RegisteredTool",
    "Registry",
    "SessionState",
    "SessionStateStore",
    "Snippet",
    "ToolProvider",
    "TraceHandle",
    "default_registry",
    "discover",
    "discover_packages",
    "graph",
    "hook",
    "tool",
]
