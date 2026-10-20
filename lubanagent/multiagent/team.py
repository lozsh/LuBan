"""多 Agent 团队：supervisor + 成员委派拓扑，编译为 CompiledFlow 跑 GraphRunner（契约决策 1/2）。

图结构：supervisor 节点（decide）→ 条件边（delegate → member / answer → finalize）
→ member 节点执行完回 supervisor 节点；finalize 为汇点（自动接 END）。
不重写第二套调度器：checkpoint/中断/Trace/取消传播全部继承 D3-4。

路由策略（route() 声明，均可缺省 = 老行为零变化）：
- entry_member：指定入口成员，用户消息先进该成员（entry-member 节点），其输出回
  supervisor 决策后续；入口执行计入 delegations（共享同一委派上限，不可借入口绕过）。
- fanout：扇出并行，supervisor 一次决策委派多成员（{"agents": [...]}），member 节点内
  asyncio.gather 并行执行、结果按声明顺序汇总后回 supervisor。选型理由：图 state 是
  {"data": dict} 单通道（无 reducer），LangGraph 原生并行边会互相覆盖 state 更新，且
  GraphBuilder 的条件边不支持 Send 动态分发；gather 在单节点内合并一次 state 更新，
  预算继承（共享 guard 对象）/取消传播（节点边界 + 外层任务取消）/Trace（子 Run
  parent_run_id=编排 Run）与既有单成员委派完全同路。

运行期服务注入：step fn 需要 runtime/db/共享 guard，经模块级 contextvar 注入
（TeamRunner.run 全程有效；不进 state，不影响 checkpoint 序列化）。
编排 Run id 经 DB 反查（graph key + thread_id 唯一），子 Run parent_run_id 全部指向它。
"""

import asyncio
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from lubanagent.core import (
    BrainProfile,
    ErrorCode,
    LubanError,
    RunContext,
)
from lubanagent.extension import Registry
from lubanagent.graph import CompiledFlow, GraphBuilder, GraphResult, GraphRunner, StepFn
from lubanagent.persistence import AgentRepository, Database, RunRepository
from lubanagent.runtime import AgentRuntime, BudgetGuard, RunRequest

from .state import DelegationRecord, delegation_output
from .supervisor import SupervisorRouter, ensure_child_ok

_TEAM_TIMEOUT_SECONDS = 300.0  # 自建 guard 的默认超时时长（与 D3-4 GraphRunner 一致）

_SUPERVISOR_NODE = "supervisor"
_MEMBER_NODE = "member"
_FINALIZE_NODE = "finalize"
_ENTRY_MEMBER_NODE = "entry-member"


# ------------------------------------------------------------ 运行期注入（contextvar）


@dataclass
class _TeamInvocation:
    """一次编排 Run 的运行期服务：子 Run 调 runtime、共享 guard、编排 Run id 反查。"""

    runtime: AgentRuntime
    db: Database
    graph_key: str  # 编排 Run 的 agent_key（graph:team:<name>）
    guard: BudgetGuard | None
    user_id: str | None = None  # 父 ctx 透传观测标签，成员子 Run 继承
    _orch_run_ids: dict[str, str] = field(default_factory=dict)

    async def orchestration_run_id(self, thread_id: str) -> str:
        """编排 Run id = 子 Run 的 parent_run_id；按 (graph key, thread) 反查并缓存。"""
        cached = self._orch_run_ids.get(thread_id)
        if cached is not None:
            return cached
        async with self.db.session() as session:
            runs = await RunRepository(session).list_runs(
                self.graph_key, session_id=thread_id, limit=1
            )
        if not runs:
            raise LubanError(
                ErrorCode.RUN_NOT_FOUND,
                f"编排 Run 未落库: {self.graph_key} thread={thread_id}",
                details={"thread_id": thread_id},
            )
        self._orch_run_ids[thread_id] = runs[0].id
        return runs[0].id


_current_invocation: ContextVar[_TeamInvocation | None] = ContextVar(
    "lubanagent_multiagent_invocation", default=None
)


def _require_invocation() -> _TeamInvocation:
    invocation = _current_invocation.get()
    if invocation is None:
        raise LubanError(
            ErrorCode.CONFIG_ERROR,
            "团队图节点只能在 TeamRunner.run 内执行（缺少运行期注入）",
        )
    return invocation


# ------------------------------------------------------------ 图节点（step fn）


def _is_delegate(state: dict[str, Any]) -> bool:
    decision = state.get("decision") or {}
    return decision.get("action") == "delegate"


def _decision_targets(decision: dict[str, Any]) -> list[str]:
    """取本轮委派目标（decision checkpoint 往返后为 dict）：扇出 agents 优先，否则单 agent。"""
    agents = decision.get("agents")
    if isinstance(agents, list) and agents:
        return [str(item) for item in agents]
    agent = decision.get("agent")
    return [str(agent)] if agent else []


def _supervisor_step(router: SupervisorRouter) -> StepFn:
    async def supervisor_node(state: dict[str, Any]) -> dict[str, Any]:
        invocation = _require_invocation()
        thread_id = str(state.get("thread_id") or "")
        parent_ctx = RunContext(
            run_id=await invocation.orchestration_run_id(thread_id),
            agent_key=router.agent_key,
            agent_version=0,  # 仅承载 run_id/guard 透传，版本由 Runtime 装载绑定
            user_id=invocation.user_id,
            brain_profile=BrainProfile.SMART,
            metadata=(
                {"budget_guard": invocation.guard} if invocation.guard is not None else {}
            ),
        )
        decision = await router.decide(state, runtime=invocation.runtime, parent_ctx=parent_ctx)
        messages = [dict(message) for message in state.get("messages") or []]
        if decision.action == "delegate":
            targets = decision.agents or ([decision.agent] if decision.agent else [])
            content = f"委派 {'、'.join(targets)}：{decision.input}"
        else:
            content = decision.answer or ""
        messages.append({"role": "supervisor", "content": content})
        return {"decision": decision.model_dump(), "messages": messages}

    return supervisor_node


async def _run_member(
    invocation: "_TeamInvocation", agent_key: str, member_input: str, thread_id: str
):
    """单成员子 Run（parent_run_id=编排 Run、共享 guard）；扇出与单委派共用同一构造。"""
    return await invocation.runtime.run(
        RunRequest(
            agent_key=agent_key,
            user_input=member_input,
            session_id=thread_id,
            user_id=invocation.user_id,
            parent_run_id=await invocation.orchestration_run_id(thread_id),
            budget_guard=invocation.guard,  # 成员消费累计进共享池（§10.6），并行子共用同一池
        )
    )


async def _member_node(state: dict[str, Any]) -> dict[str, Any]:
    """成员节点：Runtime 子 Run（parent_run_id=编排 Run、共享 guard），累积 DelegationRecord。

    扇出决策（agents 多成员）→ asyncio.gather 并行执行（单目标退化为顺序等价的单次
    调用）；结果按声明顺序合并，治理终态逐个经 ensure_child_ok 映射回图内异常。
    """
    invocation = _require_invocation()
    decision = state.get("decision") or {}
    targets = _decision_targets(decision)
    member_input = str(decision.get("input") or state.get("input") or "")
    thread_id = str(state.get("thread_id") or "")
    children = await asyncio.gather(
        *(_run_member(invocation, key, member_input, thread_id) for key in targets)
    )
    delegations = list(state.get("delegations") or [])
    messages = [dict(message) for message in state.get("messages") or []]
    for agent_key, child in zip(targets, children, strict=True):
        output = ensure_child_ok(child)  # 子 Run 治理终态映射回图内异常，图 Run 统一收口
        delegations.append(
            DelegationRecord(
                agent_key=agent_key, input=member_input, output=output, run_id=child.id
            )
        )
        messages.append({"role": agent_key, "content": output})
    return {"delegations": delegations, "messages": messages}


def _entry_member_step(agent_key: str) -> StepFn:
    """入口成员节点：用户消息先进指定成员（不经 supervisor 直接纳管），输出回 supervisor。"""

    async def entry_member_node(state: dict[str, Any]) -> dict[str, Any]:
        invocation = _require_invocation()
        member_input = str(state.get("input") or "")
        thread_id = str(state.get("thread_id") or "")
        child = await _run_member(invocation, agent_key, member_input, thread_id)
        output = ensure_child_ok(child)
        delegations = list(state.get("delegations") or [])
        delegations.append(
            DelegationRecord(
                agent_key=agent_key, input=member_input, output=output, run_id=child.id
            )
        )
        messages = [dict(message) for message in state.get("messages") or []]
        messages.append({"role": agent_key, "content": output})
        return {"delegations": delegations, "messages": messages}

    return entry_member_node


def _finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    decision = state.get("decision") or {}
    answer = decision.get("answer")
    if not answer:
        delegations = state.get("delegations") or []
        if delegations:
            answer = delegation_output(delegations[-1])
    return {"final_output": answer or ""}


# ------------------------------------------------------------ 构建器与产物


class TeamBuilder:
    """团队 DSL 构建器；拓扑校验在 build() 一次性完成。"""

    def __init__(self, name: str, *, description: str = "") -> None:
        self.name = name
        self.description = description
        self._supervisor_key: str | None = None
        self._member_keys: list[str] = []
        self._member_hints: dict[str, str] = {}
        self._max_delegations = 8
        self._entry_member: str | None = None
        self._fanout = False

    def supervisor(self, agent_key: str) -> "TeamBuilder":
        self._supervisor_key = agent_key
        return self

    def agent(self, agent_key: str, *, description: str = "") -> "TeamBuilder":
        """登记成员（可多次）；description 为路由提示，定义库描述优先。"""
        self._member_keys.append(agent_key)
        if description:
            self._member_hints[agent_key] = description
        return self

    def route(
        self,
        *,
        max_delegations: int = 8,
        entry_member: str | None = None,
        fanout: bool = False,
    ) -> "TeamBuilder":
        """路由策略声明（均可缺省 = 默认 supervisor 直接纳管，行为零变化）。

        - max_delegations：委派上限，按成员执行次数计——扇出一轮 k 个成员 = k 次，
          入口成员执行同样计入（不可借入口绕过上限）；触顶 supervisor 强制 answer。
        - entry_member：指定入口成员，用户消息先进该成员，其输出回 supervisor 决策后续。
        - fanout：启用扇出并行，supervisor 可一次决策委派多成员并行执行，结果汇总回
          supervisor 合成回复（prompt 提供多成员语法、解析支持 agents 列表）。
        """
        self._max_delegations = max_delegations
        self._entry_member = entry_member
        self._fanout = fanout
        return self

    def build(self) -> "Team":
        """校验：supervisor 已设、成员 ≥1、成员不含 supervisor → 否则 VALIDATION_ERROR。"""
        if not self._supervisor_key:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR, f"团队 {self.name} 未设置 supervisor"
            )
        if not self._member_keys:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR, f"团队 {self.name} 至少需要一个成员 Agent"
            )
        if self._supervisor_key in self._member_keys:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"团队 {self.name} 成员不能包含 supervisor: {self._supervisor_key}",
            )
        if self._entry_member is not None and self._entry_member not in self._member_keys:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"团队 {self.name} 入口成员必须是已登记成员: {self._entry_member}",
            )
        router = SupervisorRouter(
            agent_key=self._supervisor_key,
            members=self._member_keys,
            max_delegations=self._max_delegations,
            fanout=self._fanout,
        )
        builder = GraphBuilder(f"team:{self.name}", description=self.description)
        builder.step(_SUPERVISOR_NODE, _supervisor_step(router))
        builder.step(_MEMBER_NODE, _member_node)
        builder.step(_FINALIZE_NODE, _finalize_node)
        if self._entry_member is not None:
            # 入口成员拓扑：entry-member 先跑（用户消息直进成员），输出回 supervisor
            builder.step(_ENTRY_MEMBER_NODE, _entry_member_step(self._entry_member))
            builder.entry(_ENTRY_MEMBER_NODE)
            builder.edge(_ENTRY_MEMBER_NODE, _SUPERVISOR_NODE)
        else:
            builder.entry(_SUPERVISOR_NODE)
        builder.when(
            _SUPERVISOR_NODE, _is_delegate, then=_MEMBER_NODE, otherwise=_FINALIZE_NODE
        )
        builder.edge(_MEMBER_NODE, _SUPERVISOR_NODE)
        # 路由提示挂到图构建器上随 Team 携带（DB 定义描述优先，见 TeamRunner._member_descriptions）
        builder.member_description_hints = dict(self._member_hints)  # type: ignore[attr-defined]
        return Team(
            name=self.name,
            description=self.description,
            supervisor_key=self._supervisor_key,
            member_keys=list(self._member_keys),
            entry_member=self._entry_member,
            fanout=self._fanout,
            flow=builder.build(),
        )


@dataclass(frozen=True)
class Team:
    name: str
    description: str
    supervisor_key: str
    member_keys: list[str]
    flow: CompiledFlow
    entry_member: str | None = None  # 指定入口成员（None = supervisor 直接纳管）
    fanout: bool = False  # 扇出并行启用标记（False = 单成员委派，老行为）


class TeamRunner:
    """对外的运行入口（薄封装 GraphRunner，语义=编排 Run）。"""

    def __init__(
        self, *, db: Database, runtime: AgentRuntime, registry: Registry | None = None
    ) -> None:
        self._db = db
        self._runtime = runtime
        self._graph_runner = GraphRunner(db=db, runtime=runtime, registry=registry)

    async def run(
        self,
        team: Team,
        user_input: str,
        *,
        thread_id: str | None = None,
        parent_ctx: RunContext | None = None,
    ) -> GraphResult:
        thread_id = thread_id or uuid.uuid4().hex
        guard, parent_ctx = self._resolve_guard(parent_ctx)
        invocation = _TeamInvocation(
            runtime=self._runtime,
            db=self._db,
            graph_key=f"graph:team:{team.name}",
            guard=guard,
            user_id=parent_ctx.user_id if parent_ctx is not None else None,
        )
        token = _current_invocation.set(invocation)
        try:
            return await self._graph_runner.run(
                team.flow,
                {
                    "input": user_input,
                    "thread_id": thread_id,
                    "member_descriptions": await self._member_descriptions(team),
                    "messages": [],
                    "delegations": [],
                },
                thread_id=thread_id,
                parent_ctx=parent_ctx,
            )
        finally:
            _current_invocation.reset(token)

    # ------------------------------------------------------------ 内部

    def _resolve_guard(
        self, parent_ctx: RunContext | None
    ) -> tuple[BudgetGuard | None, RunContext | None]:
        """与 GraphRunner 同一套预算继承链；自建 guard 回注 metadata，节点边界与子 Run 共一池。"""
        if parent_ctx is None:
            return None, None
        candidate = parent_ctx.metadata.get("budget_guard")
        if isinstance(candidate, BudgetGuard):
            return candidate, parent_ctx
        if parent_ctx.budget is not None:
            guard = BudgetGuard(parent_ctx.budget, timeout_seconds=_TEAM_TIMEOUT_SECONDS)
            enriched = parent_ctx.model_copy(
                update={"metadata": {**parent_ctx.metadata, "budget_guard": guard}}
            )
            return guard, enriched
        return None, parent_ctx

    async def _member_descriptions(self, team: Team) -> dict[str, str]:
        """路由提示为底、定义库描述覆盖（取自定义库为准）。"""
        hints: dict[str, str] = getattr(team.flow.builder, "member_description_hints", {})
        descriptions = dict(hints)
        async with self._db.session() as session:
            repo = AgentRepository(session)
            for key in [team.supervisor_key, *team.member_keys]:
                definition = await repo.get_agent(key)
                if definition is not None:
                    descriptions[key] = definition.description
        return descriptions


__all__ = ["Team", "TeamBuilder", "TeamRunner"]
