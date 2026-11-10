"""执行关联上下文：异步任务与 anyio/to_thread 自动继承，作用域退出恢复原值。"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("luban_log_context", default=None)
CORRELATION_FIELDS = (
    "request_id",
    "session_id",
    "run_id",
    "trace_id",
    "parent_run_id",
    "root_run_id",
    "subject",
)


def current_log_context() -> dict[str, Any]:
    """返回副本，禁止调用方原地更改其他并行任务的上下文。"""
    return dict(_CONTEXT.get() or {})


def update_log_context(**values: Any) -> None:
    """补充当前请求作用域的可信主体；外层 log_context 退出仍恢复原上下文。"""
    _CONTEXT.set({**(_CONTEXT.get() or {}), **values})


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    """仅覆盖提供的字段；内部 sink/persist 标志不作为日志字段输出。"""
    token = _CONTEXT.set({**(_CONTEXT.get() or {}), **values})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def log_sink() -> Callable[[Any], None] | None:
    context = _CONTEXT.get() or {}
    if not context.get("persist_logs", True):
        return None
    return context.get("log_sink")
