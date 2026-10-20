"""执行器治理管线: 校验/权限/审批/超时/重试/错误收敛/线程卸载。"""

import asyncio
import json
import time
from typing import Any

import pytest

from lubanagent.core import (
    BrainProfile,
    ErrorCode,
    LubanError,
    RiskLevel,
    RunContext,
    ToolDescriptor,
)
from lubanagent.extension import RegisteredTool, Registry, tool
from lubanagent.tool import (
    MockToolHandler,
    ToolExecutor,
    ToolResult,
    debug_tool,
    make_mock_registered,
)


def _echo_descriptor(registry: Registry, **kwargs: Any) -> ToolDescriptor:
    @tool(registry=registry, **kwargs)
    def echo(text: str, count: int = 1) -> str:
        return text * count

    return registry.get_tool("echo").descriptor


async def test_debug_tool_runs_decorated_function_without_runtime() -> None:
    reg = Registry()

    @tool(registry=reg)
    def greet(name: str, punctuation: str = "!") -> str:
        return f"hello {name}{punctuation}"

    result = await debug_tool(reg.get_tool("greet"), {"name": "luban"})
    assert result.success is True
    assert result.value == "hello luban!"
    assert result.attempts == 1
    assert result.duration_ms >= 0.0


async def test_validation_error_missing_required_and_handler_not_called() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    handler = MockToolHandler(result="x")
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler), {"count": 2}
    )
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR.value
    assert handler.calls == []


async def test_validation_error_wrong_type() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    handler = MockToolHandler(result="x")
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler),
        {"text": "a", "count": "not-an-int"},
    )
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR.value
    assert handler.calls == []


async def test_validation_applies_defaults() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    handler = MockToolHandler(result="ok")
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler), {"text": "ab"}
    )
    assert result.success is True
    assert handler.calls == [{"text": "ab", "count": 1}]


class _DenyPolicy:
    async def check(
        self, ctx: RunContext | None, tool: ToolDescriptor, args: dict[str, Any]
    ) -> None:
        raise LubanError(ErrorCode.TOOL_PERMISSION_DENIED, f"禁止调用: {tool.key}")


async def test_permission_denied_by_custom_policy() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    handler = MockToolHandler(result="x")
    executor = ToolExecutor(permission_policy=_DenyPolicy())
    result = await executor.execute(
        RegisteredTool(descriptor=descriptor, handler=handler), {"text": "a"}
    )
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_PERMISSION_DENIED.value
    assert handler.calls == []


async def test_approval_required_without_handler() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg, approval_required=True)
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=MockToolHandler(result="x")),
        {"text": "a"},
    )
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_APPROVAL_REQUIRED.value


async def test_high_risk_triggers_approval_gate() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg, risk=RiskLevel.HIGH)
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=MockToolHandler(result="x")),
        {"text": "a"},
    )
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_APPROVAL_REQUIRED.value


async def test_approval_handler_rejects() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg, approval_required=True)

    async def reject(tool: ToolDescriptor, args: dict[str, Any]) -> bool:
        return False

    executor = ToolExecutor(approval_handler=reject)
    result = await executor.execute(
        RegisteredTool(descriptor=descriptor, handler=MockToolHandler(result="x")),
        {"text": "a"},
    )
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_APPROVAL_REQUIRED.value
    assert result.error["details"]["decision"] == "rejected"


async def test_timeout_returns_run_timeout() -> None:
    reg = Registry()

    @tool(registry=reg, timeout_seconds=0.05)
    async def slow() -> str:
        await asyncio.sleep(1.0)
        return "done"

    registered = reg.get_tool("slow")
    result = await ToolExecutor().execute(registered, {})
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.RUN_TIMEOUT.value
    assert result.error["details"]["tool"] == "slow"
    assert result.error["details"]["timeout_seconds"] == 0.05


async def test_retry_idempotent_succeeds_on_third_attempt() -> None:
    reg = Registry()

    @tool(registry=reg, idempotent=True, max_retries=2)
    async def flaky() -> str:
        return "unused"

    registered = reg.get_tool("flaky")
    calls = 0

    async def handler() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("boom")
        return "ok"

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    executor = ToolExecutor(sleep=fake_sleep)
    result = await executor.execute(
        RegisteredTool(descriptor=registered.descriptor, handler=handler), {}
    )
    assert result.success is True
    assert result.value == "ok"
    assert result.attempts == 3
    assert sleeps == [0.1, 0.2]


async def test_non_idempotent_never_retries() -> None:
    reg = Registry()

    @tool(registry=reg, max_retries=2)
    async def side_effect() -> str:
        return "unused"

    registered = reg.get_tool("side_effect")
    calls = 0

    async def handler() -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=registered.descriptor, handler=handler), {}
    )
    assert result.success is False
    assert result.attempts == 1
    assert calls == 1


async def test_retry_exhausted_returns_last_error() -> None:
    reg = Registry()

    @tool(registry=reg, idempotent=True, max_retries=1)
    async def always_fail() -> str:
        return "unused"

    registered = reg.get_tool("always_fail")

    async def handler() -> str:
        raise RuntimeError("boom")

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = await ToolExecutor(sleep=fake_sleep).execute(
        RegisteredTool(descriptor=registered.descriptor, handler=handler), {}
    )
    assert result.success is False
    assert result.attempts == 2
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_EXECUTION_FAILED.value


async def test_luban_error_code_passthrough() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    handler = MockToolHandler(
        error=LubanError(ErrorCode.BUDGET_EXCEEDED, "预算不足", details={"left": 0})
    )
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler), {"text": "a"}
    )
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.BUDGET_EXCEEDED.value
    assert result.error["message"] == "预算不足"
    assert result.error["details"] == {"left": 0}


async def test_plain_exception_becomes_tool_execution_failed_without_stack() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    handler = MockToolHandler(error=ValueError("bad value"))
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler), {"text": "a"}
    )
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_EXECUTION_FAILED.value
    assert result.error["details"]["exception"] == "ValueError"
    assert "Traceback" not in result.error["message"]
    assert "bad value" not in result.error["message"]


async def test_plain_exception_body_not_leaked_into_details() -> None:
    """异常正文可能携带上游 URL/凭证：details 只留类型名，不拼 str(exc)。"""
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    handler = MockToolHandler(
        error=ValueError("connect to https://user:sk-synthetic-secret@api.invalid/v1 failed")
    )
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler), {"text": "a"}
    )
    assert result.error is not None
    assert "sk-synthetic-secret" not in json.dumps(result.error, ensure_ascii=False)
    assert result.error["details"] == {"tool": descriptor.key, "exception": "ValueError"}


async def test_execute_by_key_unknown_returns_tool_not_found() -> None:
    result = await ToolExecutor().execute_by_key("no.such.tool", {}, registry=Registry())
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.TOOL_NOT_FOUND.value


async def test_execute_by_key_uses_registry() -> None:
    reg = Registry()

    @tool(registry=reg)
    def ping() -> str:
        return "pong"

    result = await ToolExecutor().execute_by_key("ping", {}, registry=reg)
    assert result.success is True
    assert result.value == "pong"


async def test_sync_handler_offloaded_to_thread_runs_concurrently() -> None:
    reg = Registry()

    @tool(registry=reg, timeout_seconds=10.0)
    def blocking(name: str) -> str:
        time.sleep(0.2)
        return name

    registered = reg.get_tool("blocking")
    executor = ToolExecutor()
    start = time.perf_counter()
    results = await asyncio.gather(
        executor.execute(registered, {"name": "a"}),
        executor.execute(registered, {"name": "b"}),
    )
    elapsed = time.perf_counter() - start
    assert all(r.success for r in results)
    assert [r.value for r in results] == ["a", "b"]
    assert elapsed < 0.35  # 并发 < 串行 0.4s


async def test_non_serializable_value_falls_back_to_str() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg)

    class Weird:
        def __str__(self) -> str:
            return "weird-value"

    async def handler(**kwargs: Any) -> Any:
        return Weird()

    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler), {"text": "a"}
    )
    assert result.success is True
    assert result.value == "weird-value"


def test_tool_result_is_frozen_dataclass() -> None:
    result = ToolResult(success=True, value=1)
    with pytest.raises(AttributeError):
        result.success = False  # type: ignore[misc]


def test_make_mock_registered_builds_executable_tool() -> None:
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    registered = make_mock_registered(descriptor, result="canned")
    assert registered.descriptor is descriptor
    assert isinstance(registered.handler, MockToolHandler)


async def test_handler_ctx_convention_injection() -> None:
    """handler 签名声明 _ctx → 约定注入 RunContext; 未声明 → 不传 (普通工具零感知)。"""
    descriptor = ToolDescriptor(
        key="probe",
        name="probe",
        args_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    ctx = RunContext(
        run_id="r1", agent_key="a", agent_version=1, brain_profile=BrainProfile.SMART
    )

    # 声明 _ctx → 注入; ctx 为 None 时也按约定传 None (flow_as_tool 离线直调按现状)
    seen: dict[str, Any] = {}

    async def with_ctx(text: str, _ctx: RunContext | None = None) -> str:
        seen["ctx"] = _ctx
        return text

    registered = RegisteredTool(descriptor=descriptor, handler=with_ctx)
    result = await ToolExecutor().execute(registered, {"text": "x"}, ctx=ctx)
    assert result.success is True
    assert seen["ctx"] is ctx

    result = await ToolExecutor().execute(registered, {"text": "x"})
    assert result.success is True
    assert seen["ctx"] is None

    # 未声明 _ctx → 不传 (传了会 TypeError 收敛为 TOOL_EXECUTION_FAILED, 这里必须成功)
    async def plain(text: str) -> str:
        return text

    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=plain), {"text": "x"}, ctx=ctx
    )
    assert result.success is True
    assert result.value == "x"


async def test_handler_with_ctx_param_receives_run_context() -> None:
    """约定注入: handler 签名声明 _ctx → 传入 execute 收到的 RunContext。"""
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    seen: list[Any] = []

    async def handler(text: str, count: int = 1, _ctx: RunContext | None = None) -> str:
        seen.append(_ctx)
        return text * count

    ctx = RunContext(
        run_id="r1", agent_key="a", agent_version=1, brain_profile=BrainProfile.SMART
    )
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler), {"text": "ab"}, ctx=ctx
    )
    assert result.success is True
    assert result.value == "ab"
    assert seen == [ctx]


async def test_handler_without_ctx_param_unchanged() -> None:
    """无 _ctx 参数的 handler 维持现状: 不注入、按原签名调用。"""
    reg = Registry()
    descriptor = _echo_descriptor(reg)
    seen: list[dict[str, Any]] = []

    async def handler(text: str, count: int = 1) -> str:
        seen.append({"text": text, "count": count})
        return text * count

    ctx = RunContext(
        run_id="r1", agent_key="a", agent_version=1, brain_profile=BrainProfile.SMART
    )
    result = await ToolExecutor().execute(
        RegisteredTool(descriptor=descriptor, handler=handler),
        {"text": "ab", "count": 2},
        ctx=ctx,
    )
    assert result.success is True
    assert result.value == "abab"
    assert seen == [{"text": "ab", "count": 2}]
