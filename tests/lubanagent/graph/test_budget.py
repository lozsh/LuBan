"""预算继承（契约决策 3）：父 guard 共享 / 父 budget 自建 / 图自身 budget / 无 guard 回归。"""

import pytest
from sqlalchemy import select

from lubanagent.core import (
    BrainProfile,
    Budget,
    BudgetExceededError,
    EndReason,
    RunContext,
    RunStatus,
    RunTimeoutError,
    TraceEventType,
)
from lubanagent.graph import GraphBuilder, GraphRunner
from lubanagent.observability.cost import build_run_tree_cost_report
from lubanagent.persistence import Database, RunRepository, TraceRepository
from lubanagent.persistence.models import CostRecordRow
from lubanagent.runtime import BudgetGuard

from .conftest import UsageFakeChatModel, create_test_agent, seed_fake_brain


def _parent_ctx(**kwargs) -> RunContext:
    return RunContext(
        run_id="parent-run",
        agent_key="parent-agent",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        **kwargs,
    )


def _agent_flow():
    return GraphBuilder("g").agent("ask", "chat-agent").entry("ask").build()


def _patch_usage_model(monkeypatch, usage=(10, 10)):
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(script=["长回答"], usage=usage),
    )


async def test_shared_guard_agent_node_budget_exceeded(
    make_runner, make_runtime, db: Database, monkeypatch
):
    """父 ctx 带 budget_guard（max_tokens 极小）→ agent 节点消费撞线 → 图 Run BUDGET_EXCEEDED。"""
    await seed_fake_brain(db, script=["长回答"])
    await create_test_agent(db, key="chat-agent")
    _patch_usage_model(monkeypatch, usage=(10, 10))

    guard = BudgetGuard(Budget(max_tokens=15), timeout_seconds=300.0)
    ctx = _parent_ctx(metadata={"budget_guard": guard})
    runner = make_runner(runtime=make_runtime())

    with pytest.raises(BudgetExceededError):
        await runner.run(_agent_flow(), {"input": "hi"}, parent_ctx=ctx)

    assert guard.usage.total == 20  # 成员消费累计进共享池（不允许子 Run 烧完父 Run 继续）
    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:g")
    assert runs[0].status == RunStatus.FAILED
    assert runs[0].end_reason == EndReason.BUDGET_EXCEEDED
    async with db.session() as session:
        events = await TraceRepository(session).list_events(runs[0].id)
    assert any(e.type == TraceEventType.BUDGET_EXCEEDED for e in events)


async def test_shared_guard_accumulates_member_usage(
    make_runner, make_runtime, db: Database, monkeypatch
):
    """共享 guard 未撞线时：agent 节点消费照常累计进同一池，流程正常走完。"""
    await seed_fake_brain(db, script=["长回答"])
    await create_test_agent(db, key="chat-agent")
    _patch_usage_model(monkeypatch, usage=(10, 10))

    guard = BudgetGuard(Budget(max_tokens=1000), timeout_seconds=300.0)
    ctx = _parent_ctx(metadata={"budget_guard": guard})
    runner = make_runner(runtime=make_runtime())

    result = await runner.run(_agent_flow(), {"input": "hi"}, parent_ctx=ctx)

    assert result.status == "completed"
    assert result.output["agent_output"] == "长回答"
    assert guard.usage.total == 20  # 共享池累计了子 Run 消费


async def test_parent_budget_without_guard_builds_guard_timeout(make_runner, db: Database):
    """父 ctx 只有 budget 无 guard → 图自建 guard，节点边界超时生效 → end_reason=TIMEOUT。"""
    ctx = _parent_ctx(budget=Budget(max_seconds=0))  # deadline 即过
    flow = (
        GraphBuilder("g")
        .step("a", lambda s: {"a": 1})
        .step("b", lambda s: {"b": 2})
        .entry("a")
        .edge("a", "b")
        .build()
    )
    runner = make_runner()

    with pytest.raises(RunTimeoutError):
        await runner.run(flow, {}, parent_ctx=ctx)

    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:g")
    assert runs[0].status == RunStatus.FAILED
    assert runs[0].end_reason == EndReason.TIMEOUT


async def test_graph_own_budget_timeout(db: Database, registry):
    """无 parent_ctx 但图自身带 budget → 自建 guard 在节点边界生效。"""
    flow = GraphBuilder("g").step("a", lambda s: {"a": 1}).entry("a").build()
    runner = GraphRunner(db=db, registry=registry, budget=Budget(max_seconds=0))

    with pytest.raises(RunTimeoutError):
        await runner.run(flow, {})


async def test_no_parent_no_budget_no_guard_regression(make_runner):
    """无 parent_ctx 且无图 budget → 无 guard（现状行为回归）。"""
    flow = (
        GraphBuilder("g")
        .step("a", lambda s: {"a": 1})
        .step("b", lambda s: {"b": s["a"] + 1})
        .entry("a")
        .edge("a", "b")
        .build()
    )
    result = await make_runner().run(flow, {})
    assert result.status == "completed"
    assert result.output["b"] == 2


# ------------------------------------------------------------ H11：本地成本归因与报表口径


async def test_child_billed_at_own_price_and_stores_local_cost(
    make_runner, make_runtime, db: Database, monkeypatch
):
    """子 Run 按自己模型实际单价计费：共享池=子单价合计，子 Run 落自己的成本而非共享池值。

    图编排 Run 自身无模型调用：own_cost=0；子 Run own_cost=自己的费用；
    total = own + descendants 明示相加，不重复计数。
    """
    # 子模型单价：input 0.002 元/1k、output 0.001 元/1k
    await seed_fake_brain(db, script=["长回答"], pricing=(0.002, 0.001))
    await create_test_agent(db, key="chat-agent")
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(script=["长回答"], usage=(1000, 500)),
    )

    guard = BudgetGuard(Budget(max_cost=100.0), timeout_seconds=300.0)
    ctx = _parent_ctx(metadata={"budget_guard": guard})
    runner = make_runner(runtime=make_runtime())

    result = await runner.run(_agent_flow(), {"input": "hi"}, parent_ctx=ctx)

    assert result.status == "completed"
    # 共享池：子 Run 调用按子模型单价计费（1000/1000*0.002 + 500/1000*0.001 = 0.0025）
    assert guard.cost == pytest.approx(0.0025)
    assert guard.usage.total == 1500

    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:g")
        graph_run_id = runs[0].id
        child_runs = await RunRepository(session).list_runs("chat-agent")
        child_costs = list(
            (
                await session.scalars(
                    select(CostRecordRow).where(CostRecordRow.run_id == child_runs[0].id)
                )
            ).all()
        )
    # 子 Run 落自己的成本（= 本例唯一消费，等于共享池值而非某个累计快照）
    assert child_runs[0].cost == pytest.approx(0.0025)
    assert child_runs[0].token_usage.total == 1500
    assert child_runs[0].token_usage.model_calls == 1
    assert [c.cost for c in child_costs] == pytest.approx([0.0025])
    assert [c.model_key for c in child_costs] == ["fake-chat"]

    # 报表口径：编排 Run own=0、后代=子 Run 成本，total 明示相加
    async with db.session() as session:
        tree = await build_run_tree_cost_report(session, graph_run_id)
    assert tree.own_cost == 0.0
    assert tree.descendants_cost == pytest.approx(0.0025)
    assert tree.total_cost == pytest.approx(0.0025)
    assert tree.descendant_runs == 1


async def test_child_run_without_pricing_warns_not_silent_cap(
    make_runner, make_runtime, db: Database, monkeypatch
):
    """共享 guard 带 max_cost 但子模型无单价 → BUDGET_WARNING（明示不承诺费用硬上限）。"""
    await seed_fake_brain(db, script=["长回答"])  # 不配价
    await create_test_agent(db, key="chat-agent")
    _patch_usage_model(monkeypatch, usage=(10, 10))

    guard = BudgetGuard(Budget(max_cost=1.0), timeout_seconds=300.0)
    ctx = _parent_ctx(metadata={"budget_guard": guard})
    runner = make_runner(runtime=make_runtime())

    result = await runner.run(_agent_flow(), {"input": "hi"}, parent_ctx=ctx)

    assert result.status == "completed"
    assert guard.cost == 0.0  # 无单价：成本不虚记 0 之外的数，也不熔断
    async with db.session() as session:
        child_runs = await RunRepository(session).list_runs("chat-agent")
        events = await TraceRepository(session).list_events(child_runs[0].id)
    warnings = [e for e in events if e.type == TraceEventType.BUDGET_WARNING]
    assert len(warnings) == 1
    assert warnings[0].payload["reason"] == "no_pricing"
    assert warnings[0].payload["model_key"] == "fake-chat"
