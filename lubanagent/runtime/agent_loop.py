"""Agent 执行时：LangGraph prebuilt agent 承担模型↔工具循环，本层只做治理包装。"""

import asyncio
import json
import logging
import uuid
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError

from lubanagent.config import Settings
from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    BudgetExceededError,
    CapabilityMismatchError,
    EndReason,
    ErrorCode,
    LubanError,
    ModelDefinition,
    Run,
    RunCancelledError,
    RunContext,
    RunStatus,
    RunTimeoutError,
    Step,
    TokenUsage,
    ToolDescriptor,
    TraceEvent,
    TraceEventType,
)
from lubanagent.core.redact import redact_value
from lubanagent.extension import (
    ObservabilityBackend,
    RegisteredTool,
    Registry,
    default_registry,
)
from lubanagent.memory.summary import LlmSummarizer
from lubanagent.model import (
    BrainResolution,
    BrainResolver,
    check_capability,
    create_chat_model,
    resolve_brain_for_agent,
)
from lubanagent.observability import SafeBackend, TraceWriter, resolve_backend
from lubanagent.observability.cost.pricing import prices_of
from lubanagent.persistence import (
    AgentRepository,
    Database,
    LubanCheckpointer,
    ModelRepository,
    RunRepository,
)
from lubanagent.tool import ApprovalManager, ToolExecutor
from lubanagent.tool.executor import model_from_schema

from . import capabilities
from .budget_guard import BudgetGuard
from .cancellation import CancellationRegistry, CancellationToken
from .context_builder import ContextBuilder

if TYPE_CHECKING:  # 仅类型标注；运行期由 capabilities 模块真正使用
    from lubanagent.memory import Summarizer
    from lubanagent.skill import SkillRegistry


class McpToolDiscoverer(Protocol):
    """MCP 工具发现最小协议（runtime 不 import mcp 模块，分层自闭环）。

    McpRegistry 天然满足；测试可用 stub。"""

    async def discover_tools(
        self, key: str, *, into: Registry | None = None
    ) -> list[ToolDescriptor]: ...

logger = logging.getLogger(__name__)

_END = object()  # 队列收尾哨兵

# 取消/超时检查点白名单（代码强制）：模型事件 + 工具收尾事件。
# 工具的 framing chain 事件不做检查点——到达时工具协程可能尚未落图 Run，
# 此时 throw 会让事件流 aclose 强杀在途工具协程（丢确定性终态）。
_CHECKPOINT_EVENTS = frozenset(
    {
        "on_chat_model_start",
        "on_chat_model_stream",
        "on_chat_model_end",
        "on_tool_end",
        "on_tool_error",
    }
)

EventEmitter = Callable[[TraceEvent], Awaitable[None]]

_TERMINAL_EVENT_TYPE: dict[RunStatus, TraceEventType] = {
    RunStatus.COMPLETED: TraceEventType.RUN_COMPLETED,
    RunStatus.FAILED: TraceEventType.RUN_FAILED,
    RunStatus.CANCELLED: TraceEventType.RUN_CANCELLED,
}

# 可 resume 的状态 = 非终态（对齐 H12 _TERMINAL_STATUSES 语义）
_TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


def _thread_id(run_id: str) -> str:
    """普通 Run 的 checkpoint 线程标识（与图编排 thread 命名空间隔离）。"""
    return f"run-{run_id}"

_MESSAGE_ROLES: dict[str, str] = {
    "system": "system",
    "human": "user",
    "ai": "assistant",
    "tool": "tool",
    "function": "tool",
}


def _messages_payload(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """MODEL_CALL_STARTED 的 input 载荷：[{role, content}, ...]（H10 事件 schema）。"""
    return [
        {"role": _MESSAGE_ROLES.get(str(getattr(message, "type", "")), "user"),
         "content": _chunk_text(message)}
        for message in messages
    ]


def _new_event(
    run_id: str,
    event_type: TraceEventType,
    payload: dict[str, Any] | None = None,
    *,
    step_index: int | None = None,
) -> TraceEvent:
    return TraceEvent(
        id=f"evt-{uuid.uuid4().hex}",
        run_id=run_id,
        type=event_type,
        step_index=step_index,
        payload=dict(payload or {}),
        created_at=datetime.now(UTC),
    )


def _error_dict(exc: BaseException) -> dict[str, Any]:
    """LubanError 用其 error dict（details 脱敏）；其余收敛为不含异常正文的 INTERNAL_ERROR。

    非 LubanError 异常的消息可能携带 SDK/上游正文/URL/本机路径，只保留类型名；
    完整堆栈仅进日志，不进 run.error/RUN_FAILED 事件/SSE。
    """
    if isinstance(exc, LubanError):
        return _redact_error_dict(exc.to_dict()["error"])
    stable = f"内部错误: {type(exc).__name__}"
    return LubanError(ErrorCode.INTERNAL_ERROR, stable).to_dict()["error"]


def _redact_error_dict(error: dict[str, Any] | None) -> dict[str, Any] | None:
    """错误 dict 的审计副本：details 经 redact_value 脱敏，其余字段原样。"""
    if error is None:
        return None
    details = error.get("details")
    if isinstance(details, dict):
        return {**error, "details": redact_value(details)}
    return error


def _chunk_text(chunk: BaseMessage) -> str:
    content = chunk.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


@dataclass(frozen=True)
class RunRequest:
    agent_key: str
    user_input: str
    session_id: str | None = None
    user_id: str | None = None  # 透传观测标签（无用户管理语义）
    version: int | None = None  # 缺省 = agents.current_version
    variables: dict[str, str] | None = None
    history: list[BaseMessage] | None = None
    run_id: str | None = None  # 缺省生成 uuid4 hex
    parent_run_id: str | None = None  # 子 Run 契约（§10.6）
    budget_guard: "BudgetGuard | None" = None  # 子 Run 预算继承：共用父 guard，消费进同一池
    instructions_override: str | None = None  # 试跑临时替换 instructions（不入库，版本定义不变）
    # H10 录制重放：Run 局部模型注入（不查不改共享 brain mapping，不写 models 表）
    model_override: ModelDefinition | None = None
    # H10 录制重放：Run 局部定义注入（安全副本：清空 skills/mcp_servers/长期记忆/
    # tool_signatures；不改库内版本定义）
    definition_override: AgentDefinition | None = None


class AgentRuntime:
    """流式执行 Agent：prebuilt agent + 步数/预算/超时/取消/Trace 治理包装。"""

    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        registry: Registry | None = None,
        observability: ObservabilityBackend | None = None,
        approval_manager: ApprovalManager | None = None,
        executor: ToolExecutor | None = None,
        skill_registry: "SkillRegistry | None" = None,
        summarizer: "Summarizer | None" = None,
        user_key_fn: Callable[[RunRequest], str | None] | None = None,
        mcp_registry: "McpToolDiscoverer | None" = None,
        checkpointer: LubanCheckpointer | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._registry = registry if registry is not None else default_registry
        # 可选能力件：Skill 注册表 / 摘要器 / 长期记忆用户标识来源 / MCP 客户端登记处
        # （缺省即对应能力不启用；Agent 定义开启而缺依赖 → prepare/发现期显式 CONFIG_ERROR）
        self._skill_registry = skill_registry
        self._summarizer = summarizer
        self._summarizer_attempted = False  # lazy 默认摘要器只试一次
        self._user_key_fn = user_key_fn
        self._mcp_registry: McpToolDiscoverer | None = mcp_registry
        self._discovered_mcp: set[str] = set()  # 已完成工具发现的 server key（幂等）
        if observability is None:
            observability = resolve_backend(settings, self._registry)
        self._obs: SafeBackend = (
            observability if isinstance(observability, SafeBackend) else SafeBackend(observability)
        )
        self._approval_manager = approval_manager
        self._executor = executor if executor is not None else ToolExecutor()
        self._trace_writer = TraceWriter(db)
        self._cancellations = CancellationRegistry()
        self._context_builder = ContextBuilder()
        # 缺口 #2：普通 Run 逐步 checkpoint（None = 不启用，现有行为零变化）；
        # per-run resume 串行锁（对齐 H12：常驻字典，锁与 checkpoint 生命周期同界）
        self._checkpointer = checkpointer
        self._resume_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ 入口

    async def _ensure_summarizer(self) -> "Summarizer | None":
        """未显式注入 summarizer 时按 cheap 槽位 lazy 构造 LlmSummarizer（缓存）。

        构造失败只记 warning，摘要记忆退化为会话记忆（已登记兑底，不阻断 Run）。
        """
        if self._summarizer is not None:
            return self._summarizer
        if self._summarizer_attempted:
            return None
        self._summarizer_attempted = True
        try:
            async with self._db.session() as session:
                resolver = BrainResolver(ModelRepository(session), env=self._settings.env)
                cheap = await resolver.resolve(BrainProfile.CHEAP)
            self._summarizer = LlmSummarizer(create_chat_model(cheap))
        except Exception:
            logger.warning("摘要器（cheap 槽位）构造失败，摘要记忆退化为会话记忆", exc_info=True)
        return self._summarizer

    async def _discover_mcp_tools(self, definition: AgentDefinition) -> None:
        """definition.mcp_servers 非空 → 逐 server 发现远端工具注册进 registry（幂等）。

        未注入 mcp_registry → CONFIG_ERROR（不静默看似开启）；
        mcp_servers 为空 → 本方法不被调用，零连接零副作用。
        """
        if self._mcp_registry is None:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"Agent {definition.key} 引用了 MCP Server，但运行时未注入 McpRegistry"
                "（部署者经 create_app(mcp_registry=...) 或 AgentRuntime(mcp_registry=...) 注入）",
                details={"mcp_servers": list(definition.mcp_servers)},
            )
        for server in definition.mcp_servers:
            if server in self._discovered_mcp:
                continue
            await self._mcp_registry.discover_tools(server, into=self._registry)
            self._discovered_mcp.add(server)

    async def stream(self, request: RunRequest) -> AsyncIterator[TraceEvent]:
        """流式执行：终态必出 RUN_COMPLETED / RUN_FAILED / RUN_CANCELLED 之一。"""
        run_id = request.run_id or uuid.uuid4().hex
        # 1. 装载定义（definition_override 时用调用方传入的安全副本，跳过 DB 读取；
        #    不存在 -> 抛 LubanError，不产生 Run）
        definition = (
            request.definition_override
            if request.definition_override is not None
            else await self._load_definition(request)
        )
        # 2. 版本可运行性校验（工具存在 + 签名未漂移）
        self._check_runnable(definition)
        # 3. 大脑解析（能力校验 + 受控降级；model_override 时 Run 局部注入，H10 录制重放）
        try:
            resolution = await self._resolve_brain(
                definition, model_override=request.model_override
            )
        except CapabilityMismatchError as exc:
            yield _new_event(
                run_id,
                TraceEventType.RUN_FAILED,
                {"end_reason": EndReason.ERROR.value, "error": _error_dict(exc)},
            )
            return

        queue: asyncio.Queue[Any] = asyncio.Queue()
        task = asyncio.create_task(self._execute(request, definition, resolution, run_id, queue))
        abandoned = False
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    break
                yield item
        except (GeneratorExit, asyncio.CancelledError):
            abandoned = True
            raise
        finally:
            if abandoned and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

    async def run(self, request: RunRequest) -> Run:
        """非流式：消费 stream 后从 DB 读回终态 Run。"""
        if request.run_id is None:
            request = replace(request, run_id=uuid.uuid4().hex)
        async for _ in self.stream(request):
            pass
        async with self._db.session() as session:
            run = await RunRepository(session).get_run(request.run_id)
        if run is None:
            raise LubanError(
                ErrorCode.RUN_NOT_FOUND,
                f"Run 未落库: {request.run_id}（启动前失败不产生 Run 记录）",
            )
        return run

    async def cancel(self, run_id: str) -> bool:
        return self._cancellations.cancel(run_id)

    def _resume_lock(self, run_id: str) -> asyncio.Lock:
        """per-run resume 锁：常驻字典（与 checkpoint 生命周期同界），保证串行不穿崩。"""
        lock = self._resume_locks.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            self._resume_locks[run_id] = lock
        return lock

    async def stream_resume(self, run_id: str) -> AsyncIterator[TraceEvent]:
        """从最近 checkpoint 续跑非终态 Run（缺口 #2；治理对齐 H12，见契约）。

        校验（都在锁内）：终态拒绝 / 本进程活跃拒绝 / 无 checkpoint 拒绝；
        校验全部通过后整段续跑持锁，保证同 run 并发 resume 串行。
        """
        if self._checkpointer is None:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                "本 Runtime 未启用 checkpoint（AgentRuntime(checkpointer=...)），无法 resume",
                details={"run_id": run_id},
            )
        async with self._resume_lock(run_id):
            async with self._db.session() as session:
                run = await RunRepository(session).get_run(run_id)
            if run is None:
                raise LubanError(
                    ErrorCode.RUN_NOT_FOUND,
                    f"Run 不存在: {run_id}",
                    details={"run_id": run_id},
                )
            if run.status in _TERMINAL_STATUSES:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"Run 已终态（{run.status.value}），拒绝重复 resume: {run_id}",
                    details={"run_id": run_id, "status": run.status.value},
                )
            if self._cancellations.is_active(run_id):
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"Run 正在本进程执行中，拒绝并发 resume: {run_id}",
                    details={"run_id": run_id},
                )
            if run.parent_run_id is not None:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"子 Run 由父编排推进，不接受直接 resume: {run_id}",
                    details={"run_id": run_id, "parent_run_id": run.parent_run_id},
                )
            if await self._checkpointer.aget_tuple(
                {"configurable": {"thread_id": _thread_id(run_id)}}
            ) is None:
                raise LubanError(
                    ErrorCode.RUN_NOT_FOUND,
                    f"无可续跑的 checkpoint: {run_id}",
                    details={"run_id": run_id, "reason": "no_checkpoint"},
                )
            request = RunRequest(
                agent_key=run.agent_key,
                user_input=(run.input or {}).get("user_input", ""),
                session_id=run.session_id,
                user_id=run.user_id,
                version=run.agent_version,
                variables=(run.input or {}).get("variables") or {},
                run_id=run_id,
            )
            definition = await self._load_definition(request)
            self._check_runnable(definition)
            resolution = await self._resolve_brain(definition)
            if resolution.definition.key != run.model_key:
                # 脑映射已变：如实更新 run 行（与图 resume「现状兜底」同语义）
                run.model_key = resolution.definition.key
            queue: asyncio.Queue[Any] = asyncio.Queue()
            task = asyncio.create_task(
                self._execute(request, definition, resolution, run_id, queue, resume_run=run)
            )
            abandoned = False
            try:
                while True:
                    item = await queue.get()
                    if item is _END:
                        break
                    yield item
            except (GeneratorExit, asyncio.CancelledError):
                abandoned = True
                raise
            finally:
                if abandoned and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task

    # ------------------------------------------------------- 启动前（步骤 1-3）

    async def _load_definition(self, request: RunRequest) -> AgentDefinition:
        async with self._db.session() as session:
            repo = AgentRepository(session)
            if request.version is not None:
                snapshot = await repo.get_version(request.agent_key, request.version)
                if snapshot is None:
                    current = await repo.get_agent(request.agent_key)
                    code = (
                        ErrorCode.AGENT_NOT_FOUND
                        if current is None
                        else ErrorCode.AGENT_VERSION_NOT_FOUND
                    )
                    raise LubanError(
                        code,
                        f"Agent 版本不存在: {request.agent_key} v{request.version}",
                        details={"agent_key": request.agent_key, "version": request.version},
                    )
                definition = snapshot.definition
                definition.version = snapshot.version
                definition.status = snapshot.status
                return definition
            definition = await repo.get_agent(request.agent_key)
            if definition is None:
                raise LubanError(
                    ErrorCode.AGENT_NOT_FOUND,
                    f"Agent 不存在: {request.agent_key}",
                    details={"agent_key": request.agent_key},
                )
            return definition

    def _check_runnable(self, definition: AgentDefinition) -> None:
        for key in definition.tools:
            if not self._registry.has_tool(key):
                raise LubanError(
                    ErrorCode.AGENT_VERSION_UNRUNNABLE,
                    f"Agent {definition.key} 引用的工具已注销: {key}",
                    details={"tool": key},
                )
            recorded = definition.tool_signatures.get(key)
            if recorded:
                current = self._registry.get_tool(key).descriptor.signature_hash
                if recorded != current:
                    raise LubanError(
                        ErrorCode.AGENT_VERSION_UNRUNNABLE,
                        f"Agent {definition.key} 引用的工具签名漂移: {key}",
                        details={"tool": key},
                    )

    async def _resolve_brain(
        self, definition: AgentDefinition, *, model_override: ModelDefinition | None = None
    ) -> BrainResolution:
        """model_override 注入时走 Run 局部解析（能力校验后直接构造，不查共享映射）。"""
        if model_override is not None:
            report = check_capability(definition, model_override)
            if not report.ok:
                raise CapabilityMismatchError(
                    f"Agent {definition.key} 重放模型注入能力不足且无可用降级",
                    details={
                        "missing": report.missing,
                        "tried": [definition.brain.profile.value],
                    },
                )
            return BrainResolution(
                profile=definition.brain.profile,
                definition=model_override,
                used_fallback=False,
                resolved_profile=definition.brain.profile,
            )
        async with self._db.session() as session:
            resolver = BrainResolver(ModelRepository(session), env=self._settings.env)
            return await resolve_brain_for_agent(definition, resolver)

    # ------------------------------------------------------- 执行体（步骤 4-11）

    async def _execute(
        self,
        request: RunRequest,
        definition: AgentDefinition,
        resolution: BrainResolution,
        run_id: str,
        queue: asyncio.Queue[Any],
        *,
        resume_run: Run | None = None,
    ) -> None:
        trace_started = False
        handle: Any = None
        run: Run | None = None
        guard: BudgetGuard | None = None
        # 本 Run 本地归因（H11）：Run 行/费用账本只落自己的消费，不落共享池累计值；
        # 在 try 外初始化，异常提前出圈时收尾也能拿到确定值
        own_usage = TokenUsage()
        own_cost = 0.0
        token: CancellationToken | None = None
        buffered: list[TraceEvent] = []  # 建档前的事件，建档后补落库
        terminal: TraceEvent | None = None
        abandoned = False
        last_output: str | None = None
        event_stream: Any = None

        async def emit(event: TraceEvent, *, persist: bool = True) -> None:
            if trace_started:
                if persist:
                    await self._trace_writer.emit(event)
                self._obs.emit(handle, event)
            else:
                buffered.append(event)
            await queue.put(event)

        try:
            # 3b. 降级留痕（回填 run_id，建档后补落库）
            for fallback_event in resolution.fallback_events:
                await emit(fallback_event.model_copy(update={"run_id": run_id}))

            # 4. Run 建档 + RUN_STARTED + start_trace + hooks pre_run
            #    resume 模式：不建档——复用已有行，状态置 RUNNING + RUN_RESUMED（trace 同行连续）
            now = datetime.now(UTC)
            if resume_run is not None:
                run = resume_run
                run.status = RunStatus.RUNNING
                await self._trace_writer.resume_run(run)
                trace_started = True
                handle = self._obs.start_trace(run)
                for pending in buffered:  # 补落续跑前事件
                    await self._trace_writer.emit(pending)
                    self._obs.emit(handle, pending)
                buffered.clear()
                # RUN_RESUMED 已由 resume_run 落库，这里只外发流与观测
                await emit(
                    _new_event(run_id, TraceEventType.RUN_RESUMED, {"resumed": True}),
                    persist=False,
                )
            else:
                run = Run(
                    id=run_id,
                    agent_key=definition.key,
                    agent_version=definition.version,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    # 预算继承经 request.budget_guard 共享（D3-4）；完整治理链收口 D3-5
                    parent_run_id=request.parent_run_id,
                    status=RunStatus.RUNNING,
                    input={"user_input": request.user_input, "variables": request.variables or {}},
                    brain_profile=resolution.resolved_profile,
                    model_key=resolution.definition.key,
                    created_at=now,
                    started_at=now,
                )
                await self._trace_writer.start_run(run)
                trace_started = True
                handle = self._obs.start_trace(run)
                for pending in buffered:  # 补落建档前事件
                    await self._trace_writer.emit(pending)
                    self._obs.emit(handle, pending)
                buffered.clear()
                # RUN_STARTED 已由 start_run 落库，这里只外发流与观测
                await emit(_new_event(run_id, TraceEventType.RUN_STARTED), persist=False)
            ctx = RunContext(
                run_id=run_id,
                agent_key=definition.key,
                agent_version=definition.version,
                session_id=request.session_id,
                user_id=request.user_id,
                brain_profile=resolution.resolved_profile,
                model_key=resolution.definition.key,
                budget=definition.boundary.budget,
            )
            await self._call_hooks("pre_run", ctx)

            # 5. 治理起表：传入共享 guard 则直接共用（预算继承），否则按自身 boundary 新建。
            # call_pricing = 本 Run 解析到的模型实际单价（H11）：每次调用按当次模型单价
            # 计费，不回落共享池里其他 Run 的单价；无单价显式 None（成本记 0，不硬上限承诺）
            call_pricing = prices_of(resolution.definition)
            guard = request.budget_guard
            if guard is None and resume_run is not None:
                # resume（缺口 #2）：以账本已消费量初始化——崩溃续跑不重复放水
                async with self._db.session() as session:
                    used_in, used_out, used_cost = await RunRepository(session).run_usage(run_id)
                guard = BudgetGuard(
                    definition.boundary.budget,
                    timeout_seconds=definition.boundary.timeout_seconds,
                    pricing=call_pricing,
                    initial_input_tokens=used_in,
                    initial_output_tokens=used_out,
                    initial_cost=used_cost,
                )
            elif guard is None:
                guard = BudgetGuard(
                    definition.boundary.budget,
                    timeout_seconds=definition.boundary.timeout_seconds,
                    pricing=call_pricing,
                )
            token = self._cancellations.create(run_id)
            # 治理注入：flow_as_tool 链路（父 Agent → 图子 Run）的预算继承/取消传播经
            # ctx.metadata 传递（GraphRunner._resolve_guard / _hooks 读取同名键）
            ctx.metadata["budget_guard"] = guard
            ctx.metadata["cancellation_token"] = token
            token.throw_if_cancelled()
            # 有 max_cost 但本 Run 模型无单价（含共享池继承的 max_cost）：费用硬上限对本
            # Run 的调用不可执行 → 显式告警，不静默承诺费用上限（H11）
            cost_capped = (
                definition.boundary.budget is not None
                and definition.boundary.budget.max_cost is not None
            ) or guard.max_cost is not None
            if call_pricing is None and cost_capped:
                await emit(
                    _new_event(
                        run_id,
                        TraceEventType.BUDGET_WARNING,
                        {
                            "reason": "no_pricing",
                            "model_key": resolution.definition.key,
                        },
                    )
                )

            # 6. 上下文 + 模型（先装配可选能力：记忆/知识/Skill，默认全关零副作用）
            # 6a. MCP 客户端：定义引用了 server → 先发现远端工具（缺依赖显式 CONFIG_ERROR，
            #     走 RUN_FAILED 终态；mcp_servers 为空 → 零连接零副作用）
            if definition.mcp_servers:
                await self._discover_mcp_tools(definition)
            if definition.memory.summary:
                await self._ensure_summarizer()  # lazy 默认摘要器（cheap 槽位）
            cap_ctx = await capabilities.prepare(
                definition,
                user_input=request.user_input,
                session_id=request.session_id,
                user_key=(self._user_key_fn(request) if self._user_key_fn is not None else None),
                db=self._db,
                registry=self._registry,
                skill_registry=self._skill_registry,
                summarizer=self._summarizer,
            )
            if cap_ctx.knowledge_no_hit:
                # 知识检索全 miss（守门过滤后为空且配了兜底话术）：代码强制短路拒答。
                # 不调模型（零 token 消耗、无 MODEL_* 事件），Run 仍走正常终态链路；
                # 兜底话术作为 output 落 Run 与会话记忆（finalize 照常回写）
                last_output = definition.knowledge.no_hit_fallback or ""
                await emit(
                    _new_event(
                        run_id,
                        TraceEventType.KNOWLEDGE_NO_HIT,
                        {
                            "sources": list(definition.knowledge.sources),
                            "score_threshold": definition.knowledge.score_threshold,
                            # 过滤前后条数：阈值校准排障观测点
                            "hits_raw": cap_ctx.knowledge_hits_raw,
                            "hits_kept": cap_ctx.knowledge_hits_kept,
                        },
                    )
                )
                outcome = (RunStatus.COMPLETED, EndReason.KNOWLEDGE_NO_HIT, None)
                extra_event: TraceEvent | None = None
            else:
                if request.instructions_override is not None:
                    # Prompt 调试：仅本次 Run 临时替换 instructions（不动版本化定义）
                    definition = definition.model_copy(
                        update={"instructions": request.instructions_override}
                    )
                system_prompt = self._context_builder.build_system_prompt(
                    definition, variables=request.variables
                )
                if cap_ctx.system_suffix:
                    # KV-cache 语义：能力内容动态后置到 system prompt 尾部，稳定前缀不变
                    system_prompt = (
                        f"{system_prompt}\n\n{cap_ctx.system_suffix}"
                        if system_prompt
                        else cap_ctx.system_suffix
                    )
                messages: list[BaseMessage] = []
                if system_prompt:
                    messages.append(SystemMessage(content=system_prompt))
                if cap_ctx.history is not None:
                    # 记忆层已按窗口截断（摘要记忆另前置摘要），不再二次截窗，只追加本轮输入
                    messages.extend(cap_ctx.history)
                    messages.append(HumanMessage(content=request.user_input))
                else:
                    messages.extend(
                        self._context_builder.build_messages(
                            history=request.history,
                            user_input=request.user_input,
                            window_size=definition.memory.window_size,
                        )
                    )
                model = create_chat_model(resolution.definition)

                # 7. 工具桥接（definition.tools 经 Registry + 能力件给的 extra_tools）
                executor = self._executor_for_run(run_id, run, emit)
                step_state = {"index": 0}
                tools = [
                    self._bridge_tool(
                        self._registry.get_tool(key),
                        run_id,
                        executor=executor,
                        ctx=ctx,
                        emit=emit,
                        step_state=step_state,
                    )
                    for key in definition.tools
                ]
                tools.extend(
                    self._bridge_tool(
                        registered,
                        run_id,
                        executor=executor,
                        ctx=ctx,
                        emit=emit,
                        step_state=step_state,
                    )
                    for registered in cap_ctx.extra_tools
                )

                # 8. 循环执行（prebuilt agent 承担模型↔工具循环；缺口 #2：checkpointer
                #    注入时逐步 checkpoint，thread_id 与图编排 thread 命名空间隔离）
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    from langgraph.prebuilt import create_react_agent

                    agent = create_react_agent(
                        model,
                        tools=tools,
                        checkpointer=self._checkpointer,  # None = 不启用 checkpoint
                    )
                model_step_started: datetime | None = None
                run_config: dict[str, Any] = {
                    "recursion_limit": definition.boundary.max_steps * 2 + 1
                }
                if self._checkpointer is not None:
                    run_config["configurable"] = {"thread_id": _thread_id(run_id)}
                event_stream = agent.astream_events(
                    None if resume_run is not None else {"messages": messages},
                    config=run_config,
                    version="v2",
                )
                # 工具内嵌套 Run（flow_as_tool → 图/子 Agent）的 langchain 事件经 contextvar
                # 泄进本流：parent_ids 含工具 run_id 的事件不属于本 Run——子 Run 有自己的
                # 事件流与用量账本，泄入会重复计费/串 token 流（代码强制隔离）
                tool_run_ids: set[str] = set()
                async for raw in event_stream:
                    kind = raw["event"]
                    if kind == "on_tool_start":
                        tool_run_ids.add(raw["run_id"])
                        continue
                    if tool_run_ids.intersection(raw.get("parent_ids") or ()):
                        # 嵌套事件只过滤、不在其上做取消/超时检查：否则父 Run 会因工具内子事件
                        # 提前 throw，事件流 aclose 连带强杀在途 tool 协程（图 Run 来不及落终态，
                        # 或取消语义丢成 FAILED）。取消传播经 _ctx 共享 token 由子图节点边界
                        # 代码强制；父 Run 在下一个本 Run 事件点取消，两侧都拿确定性终态。
                        continue
                    # 取消/超时检查点白名单 = 模型事件 + 工具收尾事件（代码强制）。
                    # 框架/路由类 chain 事件不做检查点：工具的 framing 事件（tools 节点）到达时
                    # 工具协程可能尚未落图 Run，此时 throw 会让 aclose 强杀在途工具协程；
                    # 白名单下取消在「当前工具收尾 / 下一次模型调用」处确定性生效。
                    if kind in _CHECKPOINT_EVENTS:
                        token.throw_if_cancelled()
                        guard.check_time()
                    if kind == "on_chat_model_start":
                        model_step_started = datetime.now(UTC)
                        # H10 事件 schema：started 载荷带 model_key + input（消息序列）
                        await emit(
                            _new_event(
                                run_id,
                                TraceEventType.MODEL_CALL_STARTED,
                                {
                                    "model_key": resolution.definition.key,
                                    "input": _messages_payload(messages),
                                },
                                step_index=step_state["index"],
                            )
                        )
                    elif kind == "on_chat_model_stream":
                        text = _chunk_text(raw["data"]["chunk"])
                        if text:
                            await emit(
                                _new_event(
                                    run_id,
                                    TraceEventType.MODEL_TOKEN_DELTA,
                                    {"text": text},
                                    step_index=step_state["index"],
                                )
                            )
                    elif kind == "on_chat_model_end":
                        output = raw["data"]["output"]
                        usage_meta = getattr(output, "usage_metadata", None) or {}
                        raw_in = usage_meta.get("input_tokens")
                        raw_out = usage_meta.get("output_tokens")
                        usage_known = raw_in is not None and raw_out is not None
                        # None = 模型未回报 usage（显式未知）；预算 consume 仍按已知数值
                        input_tokens = int(raw_in) if usage_known else None
                        output_tokens = int(raw_out) if usage_known else None
                        # 先落用量明细（撞预算也要留痕；未知存 NULL），再过预算闸。
                        # 记账顺序（H11，代码强制）：用量账本 → 共享池累计（consume）→
                        # 本地归因 → 限额判定（enforce）——四步之间无 await（并行子 Run
                        # 消费原子累计，不跨 await 用过期余额）；enforce 撞线抛错时本次
                        # 消费已入账本/共享池/本地值，失败/取消/重试路径消费不消失
                        await self._trace_writer.record_model_usage(
                            run_id,
                            step_index=step_state["index"],
                            model_key=resolution.definition.key,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )
                        call_cost = guard.consume(
                            input_tokens=input_tokens or 0,
                            output_tokens=output_tokens or 0,
                            pricing=call_pricing,  # 显式传当次模型单价（含 None 语义）
                        )
                        own_usage.input_tokens += input_tokens or 0
                        own_usage.output_tokens += output_tokens or 0
                        own_usage.model_calls += 1
                        own_cost += call_cost
                        ctx.usage = own_usage  # ctx 语义=本 Run 归因；共享池经 guard.usage
                        guard.enforce()
                        if not output.tool_calls and output.content:
                            last_output = (
                                output.content
                                if isinstance(output.content, str)
                                else str(output.content)
                            )
                        step = Step(
                            index=step_state["index"],
                            kind="model",
                            name=resolution.definition.key,
                            started_at=model_step_started or datetime.now(UTC),
                            ended_at=datetime.now(UTC),
                            token_usage=TokenUsage(
                                input_tokens=input_tokens or 0,
                                output_tokens=output_tokens or 0,
                                model_calls=1,
                            ),
                        )
                        await self._call_hooks("post_step", ctx, step)
                        # MODEL_CALL_COMPLETED 载荷 schema（H10 钉死）：
                        # {model_key, text, tool_calls(args 脱敏), input_tokens|None,
                        #  output_tokens|None, usage_known}——录制重放与适配器的同一数据源。
                        # 放在 post_step 之后发，不改变 hook 与工具桥的既有时序
                        await emit(
                            _new_event(
                                run_id,
                                TraceEventType.MODEL_CALL_COMPLETED,
                                {
                                    "model_key": resolution.definition.key,
                                    "text": _chunk_text(output),
                                    "tool_calls": [
                                        {
                                            "name": call.get("name"),
                                            "args": redact_value(dict(call.get("args") or {})),
                                            "id": call.get("id"),
                                        }
                                        for call in (output.tool_calls or [])
                                    ],
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "usage_known": usage_known,
                                },
                                step_index=step_state["index"],
                            )
                        )
                        step_state["index"] += 1

                outcome = (RunStatus.COMPLETED, EndReason.FINAL_ANSWER, None)
                extra_event: TraceEvent | None = None
        except GraphRecursionError as exc:
            outcome = (RunStatus.FAILED, EndReason.STEP_LIMIT, _error_dict(exc))
            extra_event = None
        except BudgetExceededError as exc:
            outcome = (RunStatus.FAILED, EndReason.BUDGET_EXCEEDED, _error_dict(exc))
            extra_event = _new_event(
                run_id,
                TraceEventType.BUDGET_EXCEEDED,
                {"error": _error_dict(exc)},
            )
        except RunTimeoutError as exc:
            outcome = (RunStatus.FAILED, EndReason.TIMEOUT, _error_dict(exc))
            extra_event = None
        except RunCancelledError:
            outcome = (RunStatus.CANCELLED, EndReason.CANCELLED, None)
            extra_event = None
        except asyncio.CancelledError:
            abandoned = True
            outcome = (RunStatus.CANCELLED, EndReason.CANCELLED, None)
            extra_event = None
        except LubanError as exc:
            outcome = (RunStatus.FAILED, EndReason.ERROR, _error_dict(exc))
            extra_event = None
        except Exception as exc:  # 兜底：任何异常都收敛为 RUN_FAILED
            logger.exception("Run %s 执行异常", run_id)
            outcome = (RunStatus.FAILED, EndReason.ERROR, _error_dict(exc))
            extra_event = None

        # 关闭事件流：异常出圈（取消/预算/超时/步数）时取消在途工具调用，
        # 避免工具内嵌套的图/子 Agent Run 成为无主后台任务；正常结束是 no-op
        if event_stream is not None:
            with suppress(Exception):
                await event_stream.aclose()

        # 9-11. 边界命中映射 + 收尾 + 终态唯一
        if extra_event is not None and not abandoned:
            try:
                await emit(extra_event)
            except Exception:
                logger.exception("Run %s 外发 %s 失败", run_id, extra_event.type.value)
        status, end_reason, error = outcome
        try:
            terminal = await self._finalize(
                run_id=run_id,
                run=run,
                handle=handle,
                trace_started=trace_started,
                status=status,
                end_reason=end_reason,
                error=error,
                output=last_output,
                usage=own_usage,
                cost=own_cost,
                emit=emit if not abandoned else None,
            )
        except Exception:
            logger.exception("Run %s 收尾异常", run_id)
            terminal = None
        # 10b. 能力收尾：会话/摘要记忆回写（内部消化异常，不阻断终态）
        await capabilities.finalize(
            definition,
            session_id=request.session_id,
            run_id=run_id,
            user_input=request.user_input,
            output=last_output,
            db=self._db,
            summarizer=self._summarizer,
        )
        if token is not None:
            self._cancellations.discard(run_id)
        if abandoned:
            raise asyncio.CancelledError
        if terminal is None:
            # finally 兜底：流程异常出圈也要补一个终态事件
            terminal = _new_event(
                run_id,
                TraceEventType.RUN_FAILED,
                {
                    "end_reason": EndReason.ERROR.value,
                    "error": LubanError(ErrorCode.INTERNAL_ERROR, "run 收尾异常").to_dict()[
                        "error"
                    ],
                },
            )
            await queue.put(terminal)
        await queue.put(_END)

    # ------------------------------------------------------------------ 收尾

    async def _finalize(
        self,
        *,
        run_id: str,
        run: Run | None,
        handle: Any,
        trace_started: bool,
        status: RunStatus,
        end_reason: EndReason,
        error: dict[str, Any] | None,
        output: str | None,
        usage: TokenUsage,
        cost: float,
        emit: Callable[..., Awaitable[None]] | None,
    ) -> TraceEvent:
        # H11 本地归因：usage/cost 是本 Run 自己的消费（子 Run 不落共享池累计值），
        # 费用账本按本 Run 实际 model_key 记自己的成本，报表侧聚合不重复相加
        if run is not None and trace_started:
            run.status = status
            run.end_reason = end_reason
            run.error = error
            run.output = output
            run.token_usage = usage
            run.cost = cost
            run.ended_at = datetime.now(UTC)
            await self._trace_writer.finish_run(run)
            if cost > 0 and run.model_key is not None:
                await self._trace_writer.record_cost(run.id, model_key=run.model_key, cost=cost)
        payload: dict[str, Any] = {"end_reason": end_reason.value}
        if error is not None:
            payload["error"] = error
        if status is RunStatus.COMPLETED and output is not None:
            payload["output"] = output
        terminal = _new_event(run_id, _TERMINAL_EVENT_TYPE[status], payload)
        if emit is not None:
            await emit(terminal, persist=False)  # 终态事件已由 finish_run 落库
        if handle is not None:
            self._obs.end_trace(
                handle,
                status,
                summary={
                    "usage": usage.model_dump(),
                    "cost": cost,
                    "end_reason": end_reason.value,
                },
            )
            await self._obs.drain()
        return terminal

    # ------------------------------------------------------------------ 工具桥

    def _executor_for_run(self, run_id: str, run: Run, emit: EventEmitter) -> ToolExecutor:
        """每 run 一个审批 handler；无 ApprovalManager 时用共享 executor。"""
        if self._approval_manager is None:
            return self._executor
        manager = self._approval_manager

        async def gate(descriptor: ToolDescriptor, args: dict[str, Any]) -> bool:
            approval = await manager.create_request(run_id, descriptor, args)
            # 先落 WAITING_APPROVAL 再发事件：事件到达调用方时状态已可见
            await self._set_run_status(run, RunStatus.WAITING_APPROVAL)
            await emit(
                _new_event(
                    run_id,
                    TraceEventType.TOOL_APPROVAL_REQUESTED,
                    {
                        "approval_id": approval.id,
                        "tool": descriptor.key,
                        "args": redact_value(args),
                    },
                )
            )
            approved = await manager.wait_for_decision(approval.id)
            resolved = await manager.get(approval.id)
            await self._set_run_status(run, RunStatus.RUNNING)
            await emit(
                _new_event(
                    run_id,
                    TraceEventType.TOOL_APPROVAL_RESOLVED,
                    {
                        "approval_id": approval.id,
                        "tool": descriptor.key,
                        "approved": approved,
                        "status": resolved.status,
                        "decided_by": resolved.decided_by,
                        "decided_at": resolved.decided_at.isoformat()
                        if resolved.decided_at
                        else None,
                    },
                )
            )
            return approved

        return ToolExecutor(
            permission_policy=getattr(self._executor, "_permission_policy", None),
            approval_handler=gate,
        )

    def _bridge_tool(
        self,
        registered: RegisteredTool,
        run_id: str,
        *,
        executor: ToolExecutor,
        ctx: RunContext,
        emit: EventEmitter,
        step_state: dict[str, int],
    ) -> StructuredTool:
        """RegisteredTool -> langchain StructuredTool；治理事件与 stream 主序列同通道。"""
        descriptor = registered.descriptor
        key = descriptor.key
        args_model = model_from_schema(f"{descriptor.name}BridgeArgs", descriptor.args_schema)

        async def bridge(**kwargs: Any) -> str:
            step_index = step_state["index"]
            await emit(
                _new_event(
                    run_id,
                    TraceEventType.TOOL_CALL_STARTED,
                    {"tool": key, "args": redact_value(kwargs)},
                    step_index=step_index,
                )
            )
            await self._call_hooks("pre_tool", ctx, descriptor, kwargs)
            started_at = datetime.now(UTC)
            result = await executor.execute(registered, kwargs, ctx=ctx)
            if result.success:
                await emit(
                    _new_event(
                        run_id,
                        TraceEventType.TOOL_CALL_COMPLETED,
                        {
                            "tool": key,
                            "result": redact_value(result.value),
                            "duration_ms": result.duration_ms,
                        },
                        step_index=step_index,
                    )
                )
                error = None
            else:
                await emit(
                    _new_event(
                        run_id,
                        TraceEventType.TOOL_CALL_FAILED,
                        {"tool": key, "error": _redact_error_dict(result.error)},
                        step_index=step_index,
                    )
                )
                error = result.error
            step = Step(
                index=step_index,
                kind="tool",
                name=key,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                error=error,
            )
            await self._call_hooks("post_step", ctx, step)
            step_state["index"] += 1
            if result.success:
                if isinstance(result.value, str):
                    return result.value
                return json.dumps(result.value, ensure_ascii=False)
            # 工具失败回喂模型（治理错误不炸循环，模型可解释失败）
            return json.dumps({"error": result.error}, ensure_ascii=False)

        return StructuredTool.from_function(
            func=None,
            coroutine=bridge,
            name=key,
            description=descriptor.description,
            args_schema=args_model,
            infer_schema=False,
        )

    # ------------------------------------------------------------------ helpers

    async def _set_run_status(self, run: Run, status: RunStatus) -> None:
        run.status = status
        async with self._db.session() as session:
            await RunRepository(session).update_run(run)

    async def _call_hooks(self, method: str, *args: Any) -> None:
        """逐个 await，异常只记日志不阻断。"""
        for hook in self._registry.hooks():
            callback = getattr(hook, method, None)
            if callback is None:
                continue
            try:
                await callback(*args)
            except Exception:
                logger.exception("hook %s.%s 异常（不阻断 Run）", type(hook).__name__, method)


__all__ = ["AgentRuntime", "RunRequest"]
