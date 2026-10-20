"""取消令牌与注册表单元测试。"""

import pytest

from lubanagent.core import ErrorCode, RunCancelledError
from lubanagent.runtime import CancellationRegistry, CancellationToken


def test_token_throw_after_cancel() -> None:
    token = CancellationToken()
    assert token.cancelled is False
    token.throw_if_cancelled()
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(RunCancelledError) as exc_info:
        token.throw_if_cancelled()
    assert exc_info.value.code == ErrorCode.RUN_CANCELLED


def test_registry_create_returns_same_token_for_same_run() -> None:
    registry = CancellationRegistry()
    first = registry.create("run-1")
    second = registry.create("run-1")
    assert first is second
    assert registry.create("run-2") is not first


def test_registry_cancel_unknown_run_returns_false() -> None:
    registry = CancellationRegistry()
    assert registry.cancel("missing") is False
    token = registry.create("run-1")
    assert registry.cancel("run-1") is True
    assert token.cancelled is True


def test_registry_discard() -> None:
    registry = CancellationRegistry()
    registry.create("run-1")
    registry.discard("run-1")
    assert registry.cancel("run-1") is False
    registry.discard("run-1")  # 幂等


async def test_cancel_after_run_finished_returns_false(make_runtime, db, registry):
    """H12 cancel/finish 竞态：Run 终态后 token 已 discard，cancel 返回 False 不误报。"""
    from lubanagent.runtime import RunRequest

    from .conftest import create_test_agent, seed_fake_brain

    await seed_fake_brain(db, script=["最终答案"])
    await create_test_agent(db)
    runtime = make_runtime()
    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="你好"))
    assert run.status.value == "completed"
    assert await runtime.cancel(run.id) is False  # 终态后 token 已 discard，无可取消对象


async def test_cancel_unknown_run_returns_false_via_runtime(make_runtime, db, registry):
    """未知/未起跑 run_id cancel → False（不抛、不误报）。"""
    runtime = make_runtime()
    assert await runtime.cancel("missing-run") is False
