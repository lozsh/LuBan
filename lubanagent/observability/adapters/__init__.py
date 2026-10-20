"""adapters：外部观测后端适配器 + 失败隔离包装 + 选址。"""

from lubanagent.observability.adapters.base import (
    FailureSink,
    SafeBackend,
    db_failure_sink,
)
from lubanagent.observability.adapters.langfuse import LangfuseBackend
from lubanagent.observability.adapters.langsmith import LangSmithBackend
from lubanagent.observability.adapters.noop import NoneHandle, NoopBackend
from lubanagent.observability.adapters.registry import BUILTIN_BACKENDS, resolve_backend

__all__ = [
    "BUILTIN_BACKENDS",
    "FailureSink",
    "LangSmithBackend",
    "LangfuseBackend",
    "NoneHandle",
    "NoopBackend",
    "SafeBackend",
    "db_failure_sink",
    "resolve_backend",
]
