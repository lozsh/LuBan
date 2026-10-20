"""工具执行器: 参数校验、权限、审批闸门、超时、重试全部由代码强制执行。"""

import inspect
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

import anyio
from pydantic import BaseModel, ValidationError, create_model

from lubanagent.core import (
    ErrorCode,
    LubanError,
    RiskLevel,
    RunContext,
    ToolApprovalRequiredError,
    ToolDescriptor,
)
from lubanagent.extension import RegisteredTool, Registry, default_registry

ApprovalHandler = Callable[[ToolDescriptor, dict[str, Any]], Awaitable[bool]]  # True=批准

_JSON_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass(frozen=True)
class ToolResult:
    """一次工具执行的规范化结果; error 为 LubanError.to_dict()["error"]。"""

    success: bool
    value: Any = None
    error: dict[str, Any] | None = None
    duration_ms: float = 0.0
    attempts: int = 1


class ToolPermissionPolicy(Protocol):
    """确定性权限规则由代码执行, 拒绝时抛 LubanError(TOOL_PERMISSION_DENIED)。"""

    async def check(
        self, ctx: RunContext | None, tool: ToolDescriptor, args: dict[str, Any]
    ) -> None:
        """拒绝 -> 抛 LubanError(TOOL_PERMISSION_DENIED)。"""
        ...


class AllowAllToolPermissionPolicy:
    """默认权限策略: 不拒绝任何调用。"""

    async def check(
        self, ctx: RunContext | None, tool: ToolDescriptor, args: dict[str, Any]
    ) -> None:
        return None


def model_from_schema(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """按 JSON Schema 动态构建 pydantic 校验模型, properties/required/default 生效。"""
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for prop, spec in properties.items():
        py_type: Any = _JSON_TYPES.get((spec or {}).get("type", ""), Any)
        if prop in required:
            fields[prop] = (py_type, ...)
        elif "default" in (spec or {}):
            fields[prop] = (py_type, spec["default"])
        else:
            fields[prop] = (py_type | None, None)
    return create_model(name, **fields)  # type: ignore[call-overload]


def _is_async_callable(fn: Callable[..., Any]) -> bool:
    if inspect.iscoroutinefunction(fn):
        return True
    call = getattr(fn, "__call__", None)  # noqa: B004  # 兼容 async __call__ 实例 (如 MockToolHandler)
    return inspect.iscoroutinefunction(call)


def _accepts_ctx(fn: Callable[..., Any]) -> bool:
    """handler 签名显式声明 _ctx 才注入 RunContext (约定式扩展, 普通工具零感知)。"""
    target = fn if inspect.isfunction(fn) else getattr(fn, "__call__", fn)  # noqa: B004  # 兼容 async __call__ 实例
    try:
        return "_ctx" in inspect.signature(target).parameters
    except (TypeError, ValueError):  # 部分内置 callable 无签名
        return False


def _json_safe(value: Any) -> Any:
    """返回值 JSON 兜底: 不可序列化时转 str, 保证 Trace/DB 可落库。"""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


class ToolExecutor:
    """工具治理执行器; 管线顺序即契约, 非幂等工具永不重试。"""

    def __init__(
        self,
        *,
        permission_policy: ToolPermissionPolicy | None = None,
        approval_handler: ApprovalHandler | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._permission_policy = permission_policy
        self._approval_handler = approval_handler
        self._clock = clock if clock is not None else time.perf_counter
        self._sleep = sleep if sleep is not None else anyio.sleep

    async def execute(
        self,
        registered: RegisteredTool,
        args: dict[str, Any],
        *,
        ctx: RunContext | None = None,
    ) -> ToolResult:
        descriptor = registered.descriptor
        contract = descriptor.contract
        start = self._clock()

        # 1. 参数校验: 失败不进入执行
        try:
            validated = self._validate_args(descriptor, args)
        except LubanError as exc:
            return self._fail(exc, start, attempts=0)

        # 2. 权限检查: 无策略 = 放行
        if self._permission_policy is not None:
            try:
                await self._permission_policy.check(ctx, descriptor, validated)
            except LubanError as exc:
                return self._fail(exc, start, attempts=0)

        # 3. 人工确认闸门: 无 handler 时由 Runtime 接住暂停 Run
        if contract.approval_required or descriptor.risk == RiskLevel.HIGH:
            if self._approval_handler is None:
                return self._fail(
                    ToolApprovalRequiredError(
                        f"工具 {descriptor.key} 需要人工确认", details={"tool": descriptor.key}
                    ),
                    start,
                    attempts=0,
                )
            approved = await self._approval_handler(descriptor, validated)
            if not approved:
                return self._fail(
                    LubanError(
                        ErrorCode.TOOL_APPROVAL_REQUIRED,
                        f"工具 {descriptor.key} 审批被拒绝",
                        details={"tool": descriptor.key, "decision": "rejected"},
                    ),
                    start,
                    attempts=0,
                )

        # 4-6. 执行 + 超时 + 重试: 仅幂等工具可重试, 指数退避 0.1s x 2^n
        max_attempts = contract.max_retries + 1 if contract.idempotent else 1
        attempt = 0
        while True:
            attempt += 1
            try:
                value = await self._invoke(
                    registered.handler, validated, contract.timeout_seconds, ctx=ctx
                )
                return ToolResult(
                    success=True,
                    value=_json_safe(value),
                    duration_ms=self._elapsed_ms(start),
                    attempts=attempt,
                )
            except TimeoutError:
                error: LubanError = LubanError(
                    ErrorCode.RUN_TIMEOUT,
                    f"工具 {descriptor.key} 执行超时 ({contract.timeout_seconds}s)",
                    details={"tool": descriptor.key, "timeout_seconds": contract.timeout_seconds},
                )
            except LubanError as exc:
                error = exc
            except Exception as exc:  # 契约: 统一收敛为 TOOL_EXECUTION_FAILED
                # 异常正文可能携带上游 URL/凭证，不进错误详情；类型名供排查，堆栈仅日志
                error = LubanError(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    f"工具 {descriptor.key} 执行失败: {type(exc).__name__}",
                    details={
                        "tool": descriptor.key,
                        "exception": type(exc).__name__,
                    },
                )
            if attempt < max_attempts:
                await self._sleep(0.1 * 2 ** (attempt - 1))
                continue
            return self._fail(error, start, attempts=attempt)

    async def execute_by_key(
        self,
        key: str,
        args: dict[str, Any],
        *,
        registry: Registry | None = None,
        ctx: RunContext | None = None,
    ) -> ToolResult:
        reg = registry if registry is not None else default_registry
        try:
            registered = reg.get_tool(key)
        except LubanError as exc:  # TOOL_NOT_FOUND
            return ToolResult(success=False, error=exc.to_dict()["error"], attempts=0)
        return await self.execute(registered, args, ctx=ctx)

    def _validate_args(self, descriptor: ToolDescriptor, args: dict[str, Any]) -> dict[str, Any]:
        model = model_from_schema(f"{descriptor.name}Args", descriptor.args_schema)
        try:
            return model.model_validate(args).model_dump()
        except ValidationError as exc:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"工具 {descriptor.key} 参数校验失败",
                details={
                    "tool": descriptor.key,
                    "errors": exc.errors(include_context=False, include_url=False),
                },
            ) from None

    async def _invoke(
        self,
        handler: Callable[..., Any],
        args: dict[str, Any],
        timeout_seconds: float,
        *,
        ctx: RunContext | None = None,
    ) -> Any:
        if _accepts_ctx(handler):
            # 治理上下文约定注入 (如 flow_as_tool 的 parent_ctx 预算继承/取消传播)
            args = {**args, "_ctx": ctx}
        with anyio.fail_after(timeout_seconds):
            if _is_async_callable(handler):
                return await handler(**args)
            return await anyio.to_thread.run_sync(partial(handler, **args))

    def _elapsed_ms(self, start: float) -> float:
        return (self._clock() - start) * 1000.0

    def _fail(self, error: LubanError, start: float, *, attempts: int) -> ToolResult:
        return ToolResult(
            success=False,
            error=error.to_dict()["error"],
            duration_ms=self._elapsed_ms(start),
            attempts=attempts,
        )
