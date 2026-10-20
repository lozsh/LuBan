"""治理同权（行为契约 3/4）：整条预算熔断（共享 guard）与取消传播（复验 D3-4 机制）。"""

from typing import Any

import pytest
from sqlalchemy import select

from lubanagent.core import (
    BrainProfile,
    Budget,
    BudgetExceededError,
    EndReason,
    RunCancelledError,
    RunContext,
    RunStatus,
    TraceEventType,
)
from lubanagent.model import ScriptedFakeChatModel
from lubanagent.observability.cost import build_run_tree_cost_report
from lubanagent.persistence import Database, RunRepository, TraceRepository
from lubanagent.persistence.models import TokenUsageRow
from lubanagent.runtime import BudgetGuard, CancellationToken

from .conftest import seed_triage_team


def _parent_ctx(**kwargs: Any) -> RunContext:
    return RunContext(
        run_id="parent-run",
        agent_key="parent-agent",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        **kwargs,
    )


async def test_shared_guard_budget_exceeded(
    make_team_runner, db: Database, sequential_models
):
    """共享 guard（max_tokens 极小）→ supervisor decide 消费撞线 → 编排 Run BUDGET_EXCEEDED。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "delegate", "agent": "faq", "input": "x"}'],
        faq_script=["答"],
    )
    sequential_models.set_usage("fake-smart", (10, 10))  # triage 每次 decide 20 tokens
    guard = BudgetGuard(Budget(max_tokens=15), timeout_seconds=300.0)

    with pytest.raises(BudgetExceededError):
        await make_team_runner().run(
            team, "hi", parent_ctx=_parent_ctx(metadata={"budget_guard": guard})
        )

    assert guard.usage.total == 20  # decide 子 Run 消费累计进共享池（不允许烧完继续）
    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:team:triage-team")
    assert runs[0].status == RunStatus.FAILED
    assert runs[0].end_reason == EndReason.BUDGET_EXCEEDED
    async with db.session() as session:
        events = await TraceRepository(session).list_events(runs[0].id)
    assert any(event.type == TraceEventType.BUDGET_EXCEEDED for event in events)


async def test_shared_guard_accumulates_member_usage(
    make_team_runner, db: Database, sequential_models
):
    """未撞线时：supervisor 与成员消费全部累计进同一池，编排正常走完。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agent": "faq", "input": "退货政策"}',
            '{"action": "answer", "answer": "七天无理由"}',
        ],
        faq_script=["七天无理由"],
    )
    sequential_models.set_usage("fake-smart", (10, 10))
    sequential_models.set_usage("fake-fast", (10, 10))
    guard = BudgetGuard(Budget(max_tokens=1000), timeout_seconds=300.0)

    result = await make_team_runner().run(
        team, "退货政策？", parent_ctx=_parent_ctx(metadata={"budget_guard": guard})
    )

    assert result.status == "completed"
    # 2 次 supervisor decide + 1 次成员调用 = 3 次模型调用 × 20 tokens
    assert guard.usage.model_calls == 3
    assert guard.usage.total == 60


async def test_parent_token_cancelled_before_start(make_team_runner, db: Database):
    """父 token 已取消 → 首个节点边界即停 → 编排 Run 终态 CANCELLED。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "answer", "answer": "x"}'],
        faq_script=["y"],
    )
    token = CancellationToken()
    token.cancel()

    with pytest.raises(RunCancelledError):
        await make_team_runner().run(
            team, "hi", parent_ctx=_parent_ctx(metadata={"cancellation_token": token})
        )

    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:team:triage-team")
    assert runs[0].status == RunStatus.CANCELLED
    assert runs[0].parent_run_id == "parent-run"


class _CancellingFakeChatModel(ScriptedFakeChatModel):
    """每次模型调用后取消父 token，构造编排中途取消的确定性场景。"""

    token: Any = None

    def _next(self) -> Any:
        item = super()._next()
        if self.token is not None:
            self.token.cancel()
        return item


async def test_parent_token_cancelled_mid_flow(make_team_runner, db: Database, monkeypatch):
    """decide 完成后父 token 取消 → 成员节点边界停 → 编排 Run CANCELLED，成员未执行。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "delegate", "agent": "faq", "input": "x"}'],
        faq_script=["（不应执行）"],
    )
    token = CancellationToken()

    def factory(definition):
        return _CancellingFakeChatModel(
            script=list(definition.params.get("script", [])), token=token
        )

    # 覆盖 autouse 的 sequential 工厂（后 patch 生效）
    monkeypatch.setattr("lubanagent.runtime.agent_loop.create_chat_model", factory)

    with pytest.raises(RunCancelledError):
        await make_team_runner().run(
            team, "hi", parent_ctx=_parent_ctx(metadata={"cancellation_token": token})
        )

    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:team:triage-team")
        faq_runs = await RunRepository(session).list_runs("faq")
    assert runs[0].status == RunStatus.CANCELLED
    assert faq_runs == []  # 取消在成员节点边界生效，成员子 Run 未产生


# ------------------------------------------------------------ H11：本地成本归因


async def test_local_attribution_different_prices_per_model(
    make_team_runner, db: Database, sequential_models
):
    """H11 本地归因：每个子 Run 按自己模型实际单价计费并存自己的成本；共享池=各子项之和。

    supervisor（fake-smart）与成员（fake-fast）单价不同；编排 Run 自身无模型调用
    own_cost=0；报表 total = own + 后代，不把父聚合值与子项再次相加。
    """
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agent": "faq", "input": "退货政策"}',
            '{"action": "answer", "answer": "七天无理由"}',
        ],
        faq_script=["七天无理由"],
        # smart：input 0.002/1k、output 0.001/1k；fast：input 0.004/1k、output 0.002/1k
        pricing={"fake-smart": (0.002, 0.001), "fake-fast": (0.004, 0.002)},
    )
    sequential_models.set_usage("fake-smart", (10, 10))  # 每次 decide
    sequential_models.set_usage("fake-fast", (20, 10))  # 成员一次
    guard = BudgetGuard(Budget(max_cost=100.0), timeout_seconds=300.0)

    result = await make_team_runner().run(
        team, "退货政策？", parent_ctx=_parent_ctx(metadata={"budget_guard": guard})
    )

    assert result.status == "completed"
    # 共享池：2×decide 按 smart 价 + 1×成员按 fast 价，各按当次实际单价
    smart_call = 10 / 1000 * 0.002 + 10 / 1000 * 0.001
    fast_call = 20 / 1000 * 0.004 + 10 / 1000 * 0.002
    assert guard.cost == pytest.approx(smart_call * 2 + fast_call)
    assert guard.usage.model_calls == 3

    async with db.session() as session:
        orch_runs = await RunRepository(session).list_runs("graph:team:triage-team")
        triage_runs = await RunRepository(session).list_runs("triage")
        faq_runs = await RunRepository(session).list_runs("faq")
        orch_id = orch_runs[0].id
        tree = await build_run_tree_cost_report(session, orch_id)
    # 编排 Run 自身无消费：cost/token_usage 均为本地值 0（不落共享池累计值）
    assert orch_runs[0].cost == 0.0
    assert orch_runs[0].token_usage.total == 0
    # 每个子 Run 落自己的成本（2 个 decide 子 Run + 1 个成员子 Run）
    assert [run.cost for run in triage_runs] == pytest.approx([smart_call, smart_call])
    assert faq_runs[0].cost == pytest.approx(fast_call)
    assert faq_runs[0].token_usage.total == 30
    # 报表口径：own=0，后代=各子项之和=共享池值；total 明示相加，不重复计数
    assert tree.own_cost == 0.0
    assert tree.descendants_cost == pytest.approx(guard.cost)
    assert tree.total_cost == pytest.approx(guard.cost)
    assert tree.descendant_runs == 3


async def test_fanout_parallel_children_share_pool_atomically(
    make_team_runner, db: Database, sequential_models
):
    """H11 并行消费：扇出两成员并行共用同一 guard，消费原子累计不丢失不错账。"""
    team = await seed_triage_team(
        db,
        triage_script=[
            '{"action": "delegate", "agents": ["faq", "refund"], "input": "x"}',
            '{"action": "answer", "answer": "done"}',
        ],
        faq_script=["faq 答"],
        refund_script=["refund 答"],
        fanout=True,
        pricing={"fake-smart": (0.001, 0.001), "fake-fast": (0.003, 0.001),
                 "fake-cheap": (0.0005, 0.0005)},
    )
    sequential_models.set_usage("fake-smart", (10, 10))
    sequential_models.set_usage("fake-fast", (30, 10))
    sequential_models.set_usage("fake-cheap", (40, 20))
    guard = BudgetGuard(Budget(max_cost=100.0), timeout_seconds=300.0)

    result = await make_team_runner().run(
        team, "hi", parent_ctx=_parent_ctx(metadata={"budget_guard": guard})
    )

    assert result.status == "completed"
    # 2×decide(smart) + faq(fast) + refund(cheap)，并行 gather 消费全部入池
    smart_call = 10 / 1000 * 0.002
    fast_call = 30 / 1000 * 0.003 + 10 / 1000 * 0.001
    cheap_call = 40 / 1000 * 0.0005 + 20 / 1000 * 0.0005
    assert guard.usage.model_calls == 4
    assert guard.cost == pytest.approx(smart_call * 2 + fast_call + cheap_call)

    async with db.session() as session:
        faq_runs = await RunRepository(session).list_runs("faq")
        refund_runs = await RunRepository(session).list_runs("refund")
    # 并行子 Run 各自本地归因，互不串价
    assert faq_runs[0].cost == pytest.approx(fast_call)
    assert refund_runs[0].cost == pytest.approx(cheap_call)


async def test_budget_exceeded_consumption_preserved(
    make_team_runner, db: Database, sequential_models
):
    """H11 失败路径：撞线子 Run 的本次消费不消失——账本留痕 + 本地值含撞线调用。"""
    team = await seed_triage_team(
        db,
        triage_script=['{"action": "answer", "answer": "x"}'],
        faq_script=["不应执行"],
    )
    sequential_models.set_usage("fake-smart", (10, 10))  # 撞线调用：20 tokens
    guard = BudgetGuard(Budget(max_tokens=15), timeout_seconds=300.0)

    with pytest.raises(BudgetExceededError):
        await make_team_runner().run(
            team, "hi", parent_ctx=_parent_ctx(metadata={"budget_guard": guard})
        )

    assert guard.usage.total == 20  # 撞线调用消费已入共享池
    async with db.session() as session:
        runs = await RunRepository(session).list_runs("triage")
        ledger = list(
            (
                await session.scalars(
                    select(TokenUsageRow).where(TokenUsageRow.run_id == runs[0].id)
                )
            ).all()
        )
    # 撞线的 decide 子 Run：本地值含本次调用，账本留痕
    assert runs[0].end_reason == EndReason.BUDGET_EXCEEDED
    assert runs[0].token_usage.total == 20
    assert runs[0].token_usage.model_calls == 1
    assert len(ledger) == 1
    assert ledger[0].input_tokens == 10
    assert ledger[0].output_tokens == 10
