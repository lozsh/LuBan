"""GraphRunner：图执行 = 编排子 Run（契约决策 3/4）。

每次图执行在 DB 落一条 Run（agent_key=graph:<flow>、agent_version=0、parent_run_id 透传），
节点流转写 STEP_STARTED/STEP_COMPLETED；父 token 取消传播与预算继承在节点边界代码强制。
预算继承链：parent_ctx.metadata["budget_guard"] 共享 → parent_ctx.budget 自建 → 图自身 budget。

interrupt → resume 的父级治理回接（风险 R2）：guard/token 是进程内对象、不进 checkpoint，
interrupt 时把已解析的 (guard, token) 按 thread_id 冻结在 runner 内存绑定表；resume 时按
优先级重建——显式 parent_ctx（跨进程/父 Run 已终结，调用方持上下文）> 冻结绑定 > 现状兜底。
跨进程重启后内存绑定丢失，预算池无法续接（usage 只存活在 guard 对象内），此时降级为
runner 自身 budget 兜底（如实降级，不假恢复）。

H12 恢复治理：终态 Run（COMPLETED/FAILED/CANCELLED）拒绝 resume；同一线程并发 resume
经 per-thread asyncio.Lock 串行——先到者推进，后到者撞终态显式拒绝，副作用不重复执行。
锁按 thread_id 常驻（与 checkpoint 生命周期同界），保证串行语义不因字典清理而穿崩。
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.types import Command
from sqlalchemy import select

from lubanagent.core import (
    AgentStatus,
    BrainProfile,
    Budget,
    BudgetExceededError,
    EndReason,
    ErrorCode,
    LubanError,
    Run,
    RunCancelledError,
    RunContext,
    RunStatus,
    RunTimeoutError,
    TraceEvent,
    TraceEventType,
)
from lubanagent.extension import Registry, default_registry
from lubanagent.observability import TraceWriter
from lubanagent.persistence import Database, RunRepository
from lubanagent.persistence.models import AgentRow, AgentVersionRow
from lubanagent.runtime import (
    AgentRuntime,
    BudgetGuard,
    CancellationToken,
    RunRequest,
)

from .builder import CompiledFlow, NodeHooks
from .checkpoint import LubanCheckpointer

_STUB_VERSION = 0  # 图 Run 的标记约定：流程无 Agent 版本概念
_GRAPH_TIMEOUT_SECONDS = 300.0  # 图自建 guard 的默认超时时长
# H12：可 resume 的只有 WAITING_APPROVAL（含 RUNNING 兜底）；这三个终态一律拒绝续跑
_TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


@dataclass(frozen=True)
class GraphResult:
    status: Literal["completed", "interrupted"]
    output: dict[str, Any]  # 终态 state（interrupted 时为当前 state）
    interrupt: dict[str, Any] | None  # interrupted 时的 interrupt payload
    run_id: str
    thread_id: str


@dataclass
class _ParentBinding:
    """interrupt 时冻结的父级治理绑定：resume 回接同一 guard/token 对象（同一池、同一取消链）。"""

    guard: BudgetGuard | None
    token: CancellationToken | None
    user_id: str | None


class GraphRunner:
    """跑 CompiledFlow：子 Run 建档 + 逐节点 Trace + interrupt/resume + 取消/预算传播。"""

    def __init__(
        self,
        *,
        db: Database,
        runtime: AgentRuntime | None = None,
        registry: Registry | None = None,
        checkpointer: LubanCheckpointer | None = None,
        budget: Budget | None = None,
    ) -> None:
        self._db = db
        self._runtime = runtime
        self._registry = registry if registry is not None else default_registry
        self._checkpointer = checkpointer if checkpointer is not None else LubanCheckpointer(db)
        self._trace_writer = TraceWriter(db)
        self._budget = budget  # 图自身 budget（parent_ctx 未带 guard/budget 时兜底；None=不 guard）
        # thread_id → interrupt 冻结的父级治理绑定；Run 到终态即清除，不无限增长
        self._parent_bindings: dict[str, _ParentBinding] = {}
        # thread_id → resume 串行锁（H12）：同一线程并发 resume 串行，防副作用重复执行
        self._resume_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------ 入口

    async def run(
        self,
        flow: CompiledFlow,
        input: dict[str, Any],
        *,
        thread_id: str | None = None,
        parent_ctx: RunContext | None = None,
    ) -> GraphResult:
        """新线程起跑；interrupted 返回当前 state + interrupt payload。"""
        self._check_runnable(flow)
        thread_id = thread_id or uuid.uuid4().hex
        run = await self._start_run(flow, input, thread_id=thread_id, parent_ctx=parent_ctx)
        guard = self._resolve_guard(parent_ctx)
        self._parent_bindings[thread_id] = self._freeze_binding(parent_ctx, guard)
        hooks = self._hooks(flow, run, parent_ctx=parent_ctx, guard=guard)
        compiled = flow.builder._compile(hooks=hooks, checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            result = await compiled.ainvoke({"data": dict(input)}, config)
        except BaseException as exc:
            await self._finalize_on_error(run, exc)
            raise
        return await self._outcome(flow, run, thread_id, result)

    async def resume(
        self,
        flow: CompiledFlow,
        thread_id: str,
        value: Any,
        *,
        parent_ctx: RunContext | None = None,
    ) -> GraphResult:
        """Command(resume=value) 续跑；线程不存在 → RUN_NOT_FOUND。

        父级治理绑定按优先级重建：显式 parent_ctx（跨进程重启/父 Run 已终结时由
        调用方持原 guard/token 重建）> interrupt 时冻结的内存绑定 > 现状兜底
        （runner 自身 budget，无则不 guard）。回接的是同一 guard/token 对象：
        预算消耗累计不重置、父级取消照常传播到恢复后的 Run。

        H12 治理不变量：① 同一线程并发 resume 经锁串行（先到者推进，后到者拒绝）；
        ② Run 已终态（COMPLETED/FAILED/CANCELLED）→ VALIDATION_ERROR 显式拒绝，
        终态不被改写、副作用不重复执行（幂等边界）。
        """
        self._check_runnable(flow)
        config = {"configurable": {"thread_id": thread_id}}
        async with self._resume_lock(thread_id):
            if await self._checkpointer.aget_tuple(config) is None:
                raise LubanError(
                    ErrorCode.RUN_NOT_FOUND,
                    f"图线程不存在: {thread_id}",
                    details={"thread_id": thread_id},
                )
            run = await self._load_run(flow, thread_id)
            if run.status in _TERMINAL_STATUSES:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"图 Run 已终态（{run.status.value}），拒绝重复 resume: {thread_id}",
                    details={"thread_id": thread_id, "status": run.status.value},
                )
            run.status = RunStatus.RUNNING
            async with self._db.session() as session:
                await RunRepository(session).update_run(run)
            ctx = parent_ctx if parent_ctx is not None else self._restore_parent_ctx(thread_id, run)
            guard = self._resolve_guard(ctx)
            # 绑定表刷新为本次生效的 ctx（多次 interrupt 的流程，下一次 resume 同样能回接）
            self._parent_bindings[thread_id] = self._freeze_binding(ctx, guard)
            hooks = self._hooks(flow, run, parent_ctx=ctx, guard=guard)
            compiled = flow.builder._compile(hooks=hooks, checkpointer=self._checkpointer)
            try:
                result = await compiled.ainvoke(Command(resume=value), config)
            except BaseException as exc:
                await self._finalize_on_error(run, exc)
                raise
            return await self._outcome(flow, run, thread_id, result)

    def _resume_lock(self, thread_id: str) -> asyncio.Lock:
        """per-thread resume 锁：常驻字典（与 checkpoint 生命周期同界），保证串行不穿崩。"""
        lock = self._resume_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            self._resume_locks[thread_id] = lock
        return lock

    # ------------------------------------------------------------ Run 生命周期

    @staticmethod
    def _extract_token(parent_ctx: RunContext | None) -> CancellationToken | None:
        """父取消链：parent_ctx.metadata["cancellation_token"]（无则不挂取消传播）。"""
        if parent_ctx is None:
            return None
        candidate = parent_ctx.metadata.get("cancellation_token")
        return candidate if isinstance(candidate, CancellationToken) else None

    @staticmethod
    def _freeze_binding(
        parent_ctx: RunContext | None, guard: BudgetGuard | None
    ) -> _ParentBinding:
        """把本次生效的治理对象冻结为可回接绑定（存对象引用，不复制 → 同一池同一链）。"""
        return _ParentBinding(
            guard=guard,
            token=GraphRunner._extract_token(parent_ctx),
            user_id=parent_ctx.user_id if parent_ctx is not None else None,
        )

    def _restore_parent_ctx(self, thread_id: str, run: Run) -> RunContext | None:
        """从冻结绑定重建父 ctx：metadata 回填同一 guard/token，_resolve_guard/_hooks 原样复用。

        run_id 取 run.parent_run_id（父 Run 落库时的原始父子关系，trace 链路不断）。
        无绑定（跨进程重启 / 新 runner 实例）→ None，走现状兜底。
        """
        binding = self._parent_bindings.get(thread_id)
        if binding is None:
            return None
        metadata: dict[str, Any] = {}
        if binding.guard is not None:
            metadata["budget_guard"] = binding.guard
        if binding.token is not None:
            metadata["cancellation_token"] = binding.token
        return RunContext(
            run_id=run.parent_run_id or run.id,
            agent_key=run.agent_key,
            agent_version=run.agent_version,
            user_id=binding.user_id,
            brain_profile=run.brain_profile,
            metadata=metadata,
        )

    def _resolve_guard(self, parent_ctx: RunContext | None) -> BudgetGuard | None:
        """预算继承链：父 guard 共享 → 父 budget 自建 → 图自身 budget → None（不 guard）。"""
        if parent_ctx is not None:
            candidate = parent_ctx.metadata.get("budget_guard")
            if isinstance(candidate, BudgetGuard):
                return candidate  # 共用父 guard：成员消费累计进同一池（§10.6）
            if parent_ctx.budget is not None:
                return BudgetGuard(parent_ctx.budget, timeout_seconds=_GRAPH_TIMEOUT_SECONDS)
        if self._budget is not None:
            return BudgetGuard(self._budget, timeout_seconds=_GRAPH_TIMEOUT_SECONDS)
        return None

    def _check_runnable(self, flow: CompiledFlow) -> None:
        if flow.has_agent_nodes and self._runtime is None:
            raise LubanError(
                ErrorCode.CONFIG_ERROR,
                f"图 {flow.name} 含 agent 节点，但 GraphRunner 未注入 runtime",
                details={"flow": flow.name},
            )

    async def _ensure_stub_agent(self, flow: CompiledFlow) -> None:
        """runs 表外键要求 agents/agent_versions 行存在：图 Run 落 stub（version=0 标记）。"""
        key = f"graph:{flow.name}"
        async with self._db.session() as session:
            agent_row = await session.scalar(select(AgentRow).where(AgentRow.key == key))
            if agent_row is None:
                agent_row = AgentRow(
                    key=key,
                    name=flow.name,
                    description=flow.description or "图编排流程",
                    current_version=_STUB_VERSION,
                    status=AgentStatus.PUBLISHED.value,
                )
                session.add(agent_row)
                await session.flush()
            version_row = await session.scalar(
                select(AgentVersionRow).where(
                    AgentVersionRow.agent_id == agent_row.id,
                    AgentVersionRow.version == _STUB_VERSION,
                )
            )
            if version_row is None:
                session.add(
                    AgentVersionRow(
                        agent_id=agent_row.id,
                        version=_STUB_VERSION,
                        definition={
                            "key": key,
                            "name": flow.name,
                            "description": flow.description,
                        },
                        status=AgentStatus.PUBLISHED.value,
                    )
                )

    async def _start_run(
        self,
        flow: CompiledFlow,
        input: dict[str, Any],
        *,
        thread_id: str,
        parent_ctx: RunContext | None,
    ) -> Run:
        await self._ensure_stub_agent(flow)
        now = datetime.now(UTC)
        run = Run(
            id=uuid.uuid4().hex,
            agent_key=f"graph:{flow.name}",
            agent_version=_STUB_VERSION,
            session_id=thread_id,
            user_id=parent_ctx.user_id if parent_ctx else None,
            parent_run_id=parent_ctx.run_id if parent_ctx else None,
            status=RunStatus.RUNNING,
            input=dict(input),
            # BrainProfile 枚举无 "graph" 档：透传触发方 profile，缺省 SMART
            brain_profile=parent_ctx.brain_profile if parent_ctx else BrainProfile.SMART,
            model_key=parent_ctx.model_key if parent_ctx else None,
            created_at=now,
            started_at=now,
        )
        await self._trace_writer.start_run(run)
        return run

    async def _load_run(self, flow: CompiledFlow, thread_id: str) -> Run:
        async with self._db.session() as session:
            runs = await RunRepository(session).list_runs(
                f"graph:{flow.name}", session_id=thread_id, limit=1
            )
        if not runs:
            raise LubanError(
                ErrorCode.RUN_NOT_FOUND,
                f"图 Run 不存在: graph:{flow.name} thread={thread_id}",
                details={"thread_id": thread_id},
            )
        return runs[0]

    async def _outcome(
        self, flow: CompiledFlow, run: Run, thread_id: str, result: dict[str, Any]
    ) -> GraphResult:
        data = dict(result.get("data") or {})
        pending = result.get("__interrupt__") or ()
        if pending:
            # 人工节点暂停：Run 置 WAITING_APPROVAL（resume 后续跑），checkpoint 已落库
            run.status = RunStatus.WAITING_APPROVAL
            async with self._db.session() as session:
                await RunRepository(session).update_run(run)
            payload = pending[0].value
            return GraphResult(
                status="interrupted",
                output=data,
                interrupt=payload if isinstance(payload, dict) else {"value": payload},
                run_id=run.id,
                thread_id=thread_id,
            )
        await self._finalize(
            run,
            RunStatus.COMPLETED,
            EndReason.FINAL_ANSWER,
            output=json.dumps(data, ensure_ascii=False, default=str),
        )
        self._parent_bindings.pop(thread_id, None)  # 终态 → 释放冻结绑定
        return GraphResult(
            status="completed",
            output=data,
            interrupt=None,
            run_id=run.id,
            thread_id=thread_id,
        )

    async def _finalize_on_error(self, run: Run, exc: BaseException) -> None:
        self._parent_bindings.pop(run.session_id, None)  # 终态 → 释放冻结绑定
        # 终态映射复用 D1-4 语义：取消/预算/超时各自落终态 + end_reason
        # asyncio.CancelledError：父 Run 取消 → 事件流关闭连带取消在途图执行，语义即 CANCELLED
        if isinstance(exc, (RunCancelledError, asyncio.CancelledError)):
            await self._finalize(run, RunStatus.CANCELLED, EndReason.CANCELLED)
            return
        if isinstance(exc, BudgetExceededError):
            error = exc.to_dict()["error"]
            await self._trace_writer.emit(
                self._event(run.id, TraceEventType.BUDGET_EXCEEDED, {"error": error})
            )
            await self._finalize(run, RunStatus.FAILED, EndReason.BUDGET_EXCEEDED, error=error)
            return
        if isinstance(exc, RunTimeoutError):
            await self._finalize(
                run, RunStatus.FAILED, EndReason.TIMEOUT, error=exc.to_dict()["error"]
            )
            return
        if isinstance(exc, LubanError):
            error = exc.to_dict()["error"]
        else:
            error = LubanError(
                ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
            ).to_dict()["error"]
        await self._finalize(run, RunStatus.FAILED, EndReason.ERROR, error=error)

    async def _finalize(
        self,
        run: Run,
        status: RunStatus,
        end_reason: EndReason,
        *,
        error: dict[str, Any] | None = None,
        output: str | None = None,
    ) -> None:
        run.status = status
        run.end_reason = end_reason
        run.error = error
        run.output = output
        run.ended_at = datetime.now(UTC)
        await self._trace_writer.finish_run(run)

    # ------------------------------------------------------------ 节点钩子

    def _hooks(
        self,
        flow: CompiledFlow,
        run: Run,
        *,
        parent_ctx: RunContext | None,
        guard: BudgetGuard | None,
    ) -> NodeHooks:
        token = self._extract_token(parent_ctx)
        counter = {"index": 0}

        def check_cancelled() -> None:
            if token is not None:
                token.throw_if_cancelled()  # 父取消 → 节点边界代码强制

        def check_budget() -> None:
            if guard is not None:
                guard.check_time()  # 预算继承 → 节点边界超时代码强制

        async def on_node_started(node: str, kind: str) -> None:
            await self._trace_writer.emit(
                self._event(
                    run.id,
                    TraceEventType.STEP_STARTED,
                    {"node": node, "kind": kind, "flow": flow.name},
                    step_index=counter["index"],
                )
            )

        async def on_node_completed(node: str, kind: str, state: dict[str, Any]) -> None:
            await self._trace_writer.emit(
                self._event(
                    run.id,
                    TraceEventType.STEP_COMPLETED,
                    {"node": node, "kind": kind, "flow": flow.name, "state_keys": sorted(state)},
                    step_index=counter["index"],
                )
            )
            counter["index"] += 1

        run_agent = None
        if self._runtime is not None:
            runtime = self._runtime
            graph_run_id = run.id
            thread_id = run.session_id

            async def run_agent(agent_key: str, user_input: str) -> str:
                """节点=Agent：内部跑 Runtime 一次（子 Run，parent_run_id + guard 透传，§10.6）。"""
                child = await runtime.run(
                    RunRequest(
                        agent_key=agent_key,
                        user_input=user_input,
                        session_id=thread_id,
                        user_id=parent_ctx.user_id if parent_ctx else None,
                        parent_run_id=graph_run_id,
                        budget_guard=guard,  # 成员消费累计进共享池
                    )
                )
                if child.status is not RunStatus.COMPLETED:
                    # 子 Run 治理终态映射回图内异常，由图 Run 终态映射统一收口
                    details = {"agent_key": agent_key, "child_run_id": child.id}
                    if child.end_reason is EndReason.BUDGET_EXCEEDED:
                        raise BudgetExceededError(
                            f"agent 节点子 Run 预算超限: {agent_key}", details=details
                        )
                    if child.end_reason is EndReason.TIMEOUT:
                        raise RunTimeoutError(
                            f"agent 节点子 Run 超时: {agent_key}", details=details
                        )
                    raise LubanError(
                        ErrorCode.INTERNAL_ERROR,
                        f"agent 节点子 Run 未成功: {agent_key} → {child.status.value}",
                        details=details,
                    )
                return child.output or ""

        return NodeHooks(
            check_cancelled=check_cancelled,
            check_budget=check_budget,
            on_node_started=on_node_started,
            on_node_completed=on_node_completed,
            run_agent=run_agent,
        )

    @staticmethod
    def _event(
        run_id: str,
        event_type: TraceEventType,
        payload: dict[str, Any],
        *,
        step_index: int | None = None,
    ) -> TraceEvent:
        return TraceEvent(
            id=f"evt-{uuid.uuid4().hex}",
            run_id=run_id,
            type=event_type,
            step_index=step_index,
            payload=payload,
            created_at=datetime.now(UTC),
        )


__all__ = ["GraphResult", "GraphRunner"]
