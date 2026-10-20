"""后端选址：settings 点名 → 用户注册优先 → 内置构造 → 未知名报错；统一 SafeBackend 包装。"""

from collections.abc import Callable

from lubanagent.config import Settings
from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import Registry
from lubanagent.extension.protocols import ObservabilityBackend
from lubanagent.observability.adapters.base import SafeBackend
from lubanagent.observability.adapters.langfuse import LangfuseBackend
from lubanagent.observability.adapters.langsmith import LangSmithBackend
from lubanagent.observability.adapters.noop import NoopBackend


def _build_langfuse(settings: Settings) -> ObservabilityBackend:
    return LangfuseBackend(
        host=settings.langfuse_host,
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
    )


def _build_langsmith(settings: Settings) -> ObservabilityBackend:
    return LangSmithBackend(
        api_key=settings.langsmith_api_key,
        project=settings.langsmith_project,
    )


def _build_none(settings: Settings) -> ObservabilityBackend:
    return NoopBackend()


BUILTIN_BACKENDS: dict[str, Callable[[Settings], ObservabilityBackend]] = {
    "langfuse": _build_langfuse,
    "langsmith": _build_langsmith,
    "none": _build_none,
}


def resolve_backend(
    settings: Settings, registry: Registry | None = None
) -> ObservabilityBackend:
    """选址（架构 §6.4）：

    1. settings.observability_backend 为名；
    2. 用户经 registry.register_observability_backend 注册的同名后端优先于内置；
    3. 内置 langfuse/langsmith 由 settings 字段构造；none → NoopBackend；
    4. 未知名 → LubanError(CONFIG_ERROR)；
    5. 返回 SafeBackend 包装后的实例（失败隔离默认生效；sink 由装配层接 db_failure_sink）。
    """
    name = str(settings.observability_backend)
    if registry is not None:
        user_backend = registry.get_observability_backend(name)
        if user_backend is not None:
            return SafeBackend(user_backend)
    factory = BUILTIN_BACKENDS.get(name)
    if factory is None:
        raise LubanError(
            ErrorCode.CONFIG_ERROR, f"未知观测后端: {name}", details={"backend": name}
        )
    return SafeBackend(factory(settings))
