"""report 集成测试：2 agent × 3 run 真实 DB 记录 → 聚合值/分天/未知价格断言。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import AgentDefinition, BrainProfile, ModelDefinition, Run
from lubanagent.observability.cost import build_cost_report, build_run_tree_cost_report
from lubanagent.persistence import AgentRepository, ModelRepository, RunRepository
from lubanagent.persistence.models import TokenUsageRow
from lubanagent.persistence.repositories.cost import CostRepository


def make_model(key: str, **params) -> ModelDefinition:
    return ModelDefinition(
        key=key, provider="p", model_name=key, factory="f", params=params
    )


async def seed_agent(session: AsyncSession, key: str) -> None:
    await AgentRepository(session).create_agent(AgentDefinition(key=key, name=key))


async def seed_run(
    session: AsyncSession,
    run_id: str,
    agent_key: str,
    *,
    session_id: str | None = None,
    created_at: datetime | None = None,
    parent_run_id: str | None = None,
) -> None:
    run = Run(
        id=run_id,
        agent_key=agent_key,
        agent_version=1,
        session_id=session_id,
        parent_run_id=parent_run_id,
        brain_profile=BrainProfile.SMART,
        created_at=created_at or datetime.now(UTC),
    )
    await RunRepository(session).create_run(run)


async def seed_usage(
    session: AsyncSession,
    run_id: str,
    model_key: str,
    input_tokens: int,
    output_tokens: int,
    *,
    created_at: datetime | None = None,
) -> None:
    repo = RunRepository(session)
    await repo.add_token_usage(run_id, None, model_key, input_tokens, output_tokens)
    if created_at is not None:
        # created_at 覆盖：改刚写入的行（跨天数据构造）
        rows = (
            await session.scalars(
                select(TokenUsageRow).where(TokenUsageRow.run_id == run_id)
            )
        ).all()
        rows[-1].created_at = created_at
        await session.flush()


@pytest.fixture
async def seeded(session: AsyncSession) -> None:
    """2 agent × 3 run：agent-a 用 cheap-model，agent-b 用未注册模型。"""
    await ModelRepository(session).upsert_model(
        make_model("cheap-model", input_price_per_1k=1.0, output_price_per_1k=2.0)
    )
    for agent_key, input_base in (("agent-a", 100), ("agent-b", 50)):
        await seed_agent(session, agent_key)
        for index in range(3):
            run_id = f"{agent_key}-run-{index}"
            await seed_run(
                session, run_id, agent_key, session_id=f"{agent_key}-session-{index % 2}"
            )
            model_key = "cheap-model" if agent_key == "agent-a" else "ghost-model"
            await seed_usage(
                session,
                run_id,
                model_key,
                input_base * (index + 1),
                input_base // 10 * (index + 1),
            )
    repo = RunRepository(session)
    for index in range(3):
        await repo.add_cost_record(f"agent-a-run-{index}", "cheap-model", 0.01)
        await repo.add_cost_record(f"agent-b-run-{index}", "ghost-model", 0.5)


async def test_aggregate_by_agent(session: AsyncSession, seeded: None) -> None:
    rows = await CostRepository(session).aggregate_by_agent()
    assert [row["agent_key"] for row in rows] == ["agent-a", "agent-b"]
    # agent-a：100+200+300 / 10+20+30；agent-b：50+100+150 / 5+10+15
    assert rows[0]["runs"] == 3
    assert rows[0]["input_tokens"] == 600
    assert rows[0]["output_tokens"] == 60
    assert rows[0]["model_keys"] == ["cheap-model"]
    assert rows[1]["runs"] == 3
    assert rows[1]["input_tokens"] == 300
    assert rows[1]["output_tokens"] == 30
    assert rows[1]["model_keys"] == ["ghost-model"]


async def test_aggregate_by_agent_since_filter(session: AsyncSession, seeded: None) -> None:
    rows = await CostRepository(session).aggregate_by_agent(
        since=datetime.now(UTC) + timedelta(hours=1)
    )
    assert rows == []


async def test_aggregate_by_session(session: AsyncSession, seeded: None) -> None:
    rows = await CostRepository(session).aggregate_by_session("agent-a")
    # agent-a 的 3 个 run 分布在 2 个 session（0,1,0）
    assert {row["session_id"] for row in rows} == {"agent-a-session-0", "agent-a-session-1"}
    by_id = {row["session_id"]: row for row in rows}
    assert by_id["agent-a-session-0"]["runs"] == 2
    assert by_id["agent-a-session-1"]["runs"] == 1
    assert by_id["agent-a-session-0"]["input_tokens"] == 100 + 300


async def test_aggregate_by_day_groups_and_windows(session: AsyncSession) -> None:
    await seed_agent(session, "agent-day")
    now = datetime.now(UTC)
    for index, days_ago in enumerate((0, 2, 30)):
        run_id = f"day-run-{index}"
        await seed_run(session, run_id, "agent-day", created_at=now - timedelta(days=days_ago))
        await seed_usage(
            session,
            run_id,
            "cheap-model",
            100 * (index + 1),
            10,
            created_at=now - timedelta(days=days_ago),
        )
    rows = await CostRepository(session).aggregate_by_day(days=7)
    # 30 天前的记录被窗口排除；今天与 2 天前各一组
    assert len(rows) == 2
    assert rows[0]["day"] < rows[1]["day"]
    assert rows[0]["input_tokens"] == 200  # 2 天前
    assert rows[1]["input_tokens"] == 100  # 今天
    assert all(row["runs"] == 1 for row in rows)


async def test_cost_rows_and_filter(session: AsyncSession, seeded: None) -> None:
    rows = await CostRepository(session).cost_rows()
    by_model = {row["model_key"]: row for row in rows}
    assert by_model["cheap-model"]["cost"] == pytest.approx(0.03)
    assert by_model["ghost-model"]["cost"] == pytest.approx(1.5)

    rows_a = await CostRepository(session).cost_rows(agent_key="agent-a")
    assert [row["model_key"] for row in rows_a] == ["cheap-model"]


async def test_build_cost_report_collects_unknown_pricing(
    session: AsyncSession, seeded: None
) -> None:
    report = await build_cost_report(session, days=7)
    assert len(report.by_agent) == 2
    assert len(report.by_day) == 1  # 全部用量发生在今天
    assert {row["model_key"] for row in report.cost_by_model} == {
        "cheap-model",
        "ghost-model",
    }
    cheap = next(row for row in report.cost_by_model if row["model_key"] == "cheap-model")
    assert cheap["input_price_per_1k"] == 1.0
    assert cheap["output_price_per_1k"] == 2.0
    # ghost-model 未注册 → 未知价格 → 总成本记 None 而非 0
    assert report.unknown_pricing_models == ["ghost-model"]
    assert report.total_cost is None


async def test_build_cost_report_total_when_all_priced(session: AsyncSession) -> None:
    await ModelRepository(session).upsert_model(
        make_model("cheap-model", input_price_per_1k=1.0, output_price_per_1k=2.0)
    )
    await seed_agent(session, "agent-x")
    await seed_run(session, "run-x", "agent-x")
    await seed_usage(session, "run-x", "cheap-model", 100, 10)
    await RunRepository(session).add_cost_record("run-x", "cheap-model", 0.42)

    report = await build_cost_report(session)
    assert report.unknown_pricing_models == []
    assert report.total_cost == pytest.approx(0.42)


# ------------------------------------------------------------ H11：Run 树本地/后代归因


async def test_run_tree_cost_report_own_vs_descendants(session: AsyncSession) -> None:
    """H11 报表口径：父自身与全部后代费用明示分开，total = own + descendants 不重复相加。"""
    await seed_agent(session, "agent-tree")
    # 树：root -> child -> grandchild；旁支 unrelated 不计入
    await seed_run(session, "tree-root", "agent-tree")
    await seed_run(session, "tree-child", "agent-tree", parent_run_id="tree-root")
    await seed_run(session, "tree-grandchild", "agent-tree", parent_run_id="tree-child")
    await seed_run(session, "unrelated", "agent-tree")
    repo = RunRepository(session)
    await repo.add_cost_record("tree-root", "m-root", 1.0)
    await repo.add_cost_record("tree-child", "m-child", 2.0)
    await repo.add_cost_record("tree-grandchild", "m-grand", 3.0)
    await repo.add_cost_record("tree-grandchild", "m-grand-2", 0.5)
    await repo.add_cost_record("unrelated", "m-other", 100.0)

    tree = await build_run_tree_cost_report(session, "tree-root")
    assert tree.own_cost == pytest.approx(1.0)
    assert tree.descendants_cost == pytest.approx(5.5)  # child 2.0 + grandchild 3.5
    assert tree.total_cost == pytest.approx(6.5)
    assert tree.own_runs == 1
    assert tree.descendant_runs == 2

    # 叶子视角：own=3.5，无后代
    leaf = await build_run_tree_cost_report(session, "tree-grandchild")
    assert leaf.own_cost == pytest.approx(3.5)
    assert leaf.descendants_cost == 0.0
    assert leaf.total_cost == pytest.approx(3.5)


async def test_run_tree_cost_report_leafless_and_missing(session: AsyncSession) -> None:
    await seed_agent(session, "agent-lone")
    await seed_run(session, "lone", "agent-lone")
    tree = await build_run_tree_cost_report(session, "lone")
    assert tree.own_cost == 0.0
    assert tree.total_cost == 0.0
    assert tree.descendant_runs == 0

    from lubanagent.core import LubanError

    with pytest.raises(LubanError):
        await build_run_tree_cost_report(session, "missing-run")
