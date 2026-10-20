"""扩展注册表: 框架运行期只从这里取用户扩展实例, 重复注册即报错。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lubanagent.core import ErrorCode, LubanError, ToolDescriptor

from .protocols import (
    AuthProvider,
    GraphDescriptor,
    Hook,
    KnowledgeConnector,
    ObservabilityBackend,
)


@dataclass(frozen=True)
class RegisteredTool:
    descriptor: ToolDescriptor
    handler: Callable[..., Any]


class Registry:
    """用户扩展的运行期注册表; 框架不 import 用户模块, 只查此表。"""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._graphs: dict[str, GraphDescriptor] = {}
        self._auth: AuthProvider | None = None
        self._observability: ObservabilityBackend | None = None
        self._observability_backends: dict[str, ObservabilityBackend] = {}
        self._hooks: list[Hook] = []
        self._knowledge: dict[str, KnowledgeConnector] = {}

    # ---- tool ----
    def register_tool(self, descriptor: ToolDescriptor, handler: Callable[..., Any]) -> None:
        if descriptor.key in self._tools:
            raise LubanError(ErrorCode.CONFIG_ERROR, f"工具重复注册: {descriptor.key}")
        self._tools[descriptor.key] = RegisteredTool(descriptor=descriptor, handler=handler)

    def get_tool(self, key: str) -> RegisteredTool:
        try:
            return self._tools[key]
        except KeyError:
            raise LubanError(ErrorCode.TOOL_NOT_FOUND, f"工具未注册: {key}") from None

    def has_tool(self, key: str) -> bool:
        return key in self._tools

    def list_tools(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    # ---- graph ----
    def register_graph(self, descriptor: GraphDescriptor) -> None:
        if descriptor.name in self._graphs:
            raise LubanError(ErrorCode.CONFIG_ERROR, f"图重复注册: {descriptor.name}")
        self._graphs[descriptor.name] = descriptor

    def get_graph(self, name: str) -> GraphDescriptor:
        try:
            return self._graphs[name]
        except KeyError:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"图未注册: {name}") from None

    def list_graphs(self) -> list[GraphDescriptor]:
        return list(self._graphs.values())

    # ---- auth: 不注册为 None, 框架默认 API Key ----
    def register_auth(self, provider: AuthProvider) -> None:
        self._auth = provider

    def get_auth(self) -> AuthProvider | None:
        return self._auth

    # ---- observability: 不注册为 None, 由 settings 选择 ----
    def register_observability(self, backend: ObservabilityBackend) -> None:
        self._observability = backend

    def get_observability(self) -> ObservabilityBackend | None:
        return self._observability

    # ---- observability 命名后端: 多后端选址, 用户注册优先于内置 ----
    def register_observability_backend(self, name: str, backend: ObservabilityBackend) -> None:
        if name in self._observability_backends:
            raise LubanError(ErrorCode.CONFIG_ERROR, f"观测后端重复注册: {name}")
        self._observability_backends[name] = backend

    def get_observability_backend(self, name: str) -> ObservabilityBackend | None:
        return self._observability_backends.get(name)

    def list_observability_backends(self) -> list[str]:
        return list(self._observability_backends)

    # ---- hooks ----
    def register_hook(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def hooks(self) -> list[Hook]:
        return list(self._hooks)

    # ---- knowledge ----
    def register_knowledge(self, name: str, connector: KnowledgeConnector) -> None:
        if name in self._knowledge:
            raise LubanError(ErrorCode.CONFIG_ERROR, f"知识源重复注册: {name}")
        self._knowledge[name] = connector

    def get_knowledge(self, name: str) -> KnowledgeConnector:
        try:
            return self._knowledge[name]
        except KeyError:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"知识源未注册: {name}") from None

    def list_knowledge(self) -> list[str]:
        return list(self._knowledge)

    def unregister_knowledge(self, name: str) -> None:
        """删除知识源注册（幂等）；Agent YAML 仍引用该源时按既有设计 run 期报错。"""
        self._knowledge.pop(name, None)


default_registry = Registry()
