"""resolve_backend 选址：none/builtin/用户注册优先/未知名报错 + 降级显式。"""

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from lubanagent.config import Settings
from lubanagent.core import BrainProfile, Run, RunStatus
from lubanagent.core.errors import ErrorCode, LubanError
from lubanagent.extension import Registry
from lubanagent.observability import (
    BUILTIN_BACKENDS,
    LangfuseBackend,
    NoopBackend,
    SafeBackend,
    resolve_backend,
)
from lubanagent.observability.adapters.noop import NoneHandle


def make_run() -> Run:
    return Run(
        id="run-1",
        agent_key="demo",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        created_at=datetime(2026, 7, 23, tzinfo=UTC),
    )


class MyCorpBackend:
    @property
    def name(self) -> str:
        return "mycorp"

    def start_trace(self, run: Run) -> object:
        return object()

    def emit(self, handle: Any, event: Any) -> None:
        return None

    def end_trace(
        self, handle: Any, status: RunStatus, summary: dict[str, Any] | None = None
    ) -> None:
        return None


def test_resolve_none_returns_noop() -> None:
    settings = Settings(observability_backend="none")
    backend = resolve_backend(settings)
    assert isinstance(backend, SafeBackend)
    assert backend.name == "noop"
    assert isinstance(backend._inner, NoopBackend)
    handle = backend.start_trace(make_run())
    assert isinstance(handle, NoneHandle)


def test_resolve_langfuse_without_keys_degrades_to_noop(caplog) -> None:
    settings = Settings(
        observability_backend="langfuse",
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )
    backend = resolve_backend(settings)
    assert isinstance(backend._inner, LangfuseBackend)
    with caplog.at_level(logging.WARNING):
        handle = backend.start_trace(make_run())
    assert isinstance(handle, NoneHandle)  # key 缺失自动降级，不抛错
    assert any("langfuse key 缺失" in r.message for r in caplog.records)


def test_resolve_user_registered_backend_wins() -> None:
    registry = Registry()
    mine = MyCorpBackend()
    registry.register_observability_backend("mycorp", mine)
    # observability_backend 的 Literal 校验只约束内置名；用户名经 model_construct 注入
    settings = Settings.model_construct(observability_backend="mycorp")
    backend = resolve_backend(settings, registry)
    assert isinstance(backend, SafeBackend)
    assert backend.name == "mycorp"
    assert backend._inner is mine


def test_resolve_unknown_backend_raises_config_error() -> None:
    settings = Settings.model_construct(observability_backend="ghost")
    with pytest.raises(LubanError) as exc_info:
        resolve_backend(settings)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert exc_info.value.details == {"backend": "ghost"}


def test_builtin_backends_registry_keys() -> None:
    assert set(BUILTIN_BACKENDS) == {"langfuse", "langsmith", "none"}
    settings = Settings(observability_backend="none")
    assert isinstance(BUILTIN_BACKENDS["none"](settings), NoopBackend)
