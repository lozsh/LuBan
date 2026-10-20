"""RunRepository：运行记录 CRUD、筛选列表、token 用量与费用明细。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    EndReason,
    Run,
    RunStatus,
    TokenUsage,
)
from lubanagent.persistence.models import AgentVersionRow, CostRecordRow, RunRow, TokenUsageRow
from lubanagent.persistence.repositories import AgentRepository, RunRepository


def make_run(run_id: str, agent_key: str = "demo", **overrides) -> Run:
    data = {
        "id": run_id,
        "agent_key": agent_key,
        "agent_version": 1,
        "brain_profile": BrainProfile.SMART,
        "input": {"text": "你好"},
        "created_at": datetime(2026, 7, 23, 12, 0, 0),
    }
    return Run(**(data | overrides))


async def seed_agent(session: AsyncSession, key: str = "demo") -> None:
    await AgentRepository(session).create_agent(AgentDefinition(key=key, name=key))


async def test_create_and_get_run(session: AsyncSession) -> None:
    await seed_agent(session)
    repo = RunRepository(session)
    run = make_run("run-1", session_id="s-1", user_id="u-1")
    await repo.create_run(run)

    loaded = await repo.get_run("run-1")
    assert loaded is not None
    assert loaded.id == "run-1"
    assert loaded.agent_key == "demo"
    assert loaded.agent_version == 1
    assert loaded.session_id == "s-1"
    assert loaded.user_id == "u-1"
    assert loaded.status == RunStatus.PENDING
    assert loaded.input == {"text": "你好"}
    assert loaded.brain_profile == BrainProfile.SMART
    assert loaded.cost == 0.0
    assert loaded.created_at == datetime(2026, 7, 23, 12, 0, 0)

    # 版本化字段断言：runs.agent_version_id 指向 agent_versions 主键
    row = await session.get(RunRow, "run-1")
    version_row = await session.scalar(select(AgentVersionRow))
    assert row is not None and version_row is not None
    assert row.agent_version_id == version_row.id
    assert version_row.version == 1


async def test_get_run_missing_returns_none(session: AsyncSession) -> None:
    assert await RunRepository(session).get_run("ghost") is None


async def test_update_run(session: AsyncSession) -> None:
    await seed_agent(session)
    repo = RunRepository(session)
    run = make_run("run-1")
    await repo.create_run(run)

    run.status = RunStatus.COMPLETED
    run.output = "最终答复"
    run.end_reason = EndReason.FINAL_ANSWER
    run.token_usage = TokenUsage(input_tokens=10, output_tokens=20, model_calls=1)
    run.cost = 0.01
    run.started_at = datetime(2026, 7, 23, 12, 0, 1)
    run.ended_at = datetime(2026, 7, 23, 12, 0, 5)
    await repo.update_run(run)

    loaded = await repo.get_run("run-1")
    assert loaded is not None
    assert loaded.status == RunStatus.COMPLETED
    assert loaded.output == "最终答复"
    assert loaded.end_reason == EndReason.FINAL_ANSWER
    assert loaded.token_usage.total == 30
    assert loaded.cost == 0.01
    assert loaded.ended_at == datetime(2026, 7, 23, 12, 0, 5)


async def test_list_runs_filters(session: AsyncSession) -> None:
    await seed_agent(session, key="a")
    await seed_agent(session, key="b")
    repo = RunRepository(session)
    await repo.create_run(make_run("r1", agent_key="a", session_id="s-1", user_id="u-1"))
    await repo.create_run(make_run("r2", agent_key="a", session_id="s-2"))
    await repo.create_run(make_run("r3", agent_key="b", session_id="s-1"))

    assert {r.id for r in await repo.list_runs()} == {"r1", "r2", "r3"}
    assert {r.id for r in await repo.list_runs("a")} == {"r1", "r2"}
    assert {r.id for r in await repo.list_runs(session_id="s-1")} == {"r1", "r3"}
    assert {r.id for r in await repo.list_runs("a", session_id="s-1")} == {"r1"}
    assert {r.id for r in await repo.list_runs(user_id="u-1")} == {"r1"}
    assert await repo.list_runs(user_id="ghost") == []
    assert len(await repo.list_runs(limit=2)) == 2


async def test_add_token_usage_and_cost_record(session: AsyncSession) -> None:
    await seed_agent(session)
    repo = RunRepository(session)
    await repo.create_run(make_run("run-1"))
    await repo.add_token_usage("run-1", 0, "fake-chat", 10, 20)
    await repo.add_token_usage("run-1", None, "fake-chat", 1, 2)
    await repo.add_cost_record("run-1", "fake-chat", 0.02)
    await repo.add_cost_record("run-1", "fake-chat", 0.03, currency="USD")

    usages = (
        await session.scalars(
            select(TokenUsageRow).where(TokenUsageRow.run_id == "run-1")
        )
    ).all()
    assert [(u.step_index, u.input_tokens, u.output_tokens) for u in usages] == [
        (0, 10, 20),
        (None, 1, 2),
    ]

    costs = (
        await session.scalars(select(CostRecordRow).where(CostRecordRow.run_id == "run-1"))
    ).all()
    assert [(c.cost, c.currency) for c in costs] == [(0.02, "CNY"), (0.03, "USD")]
    total = await session.scalar(
        select(func.count()).select_from(CostRecordRow).where(CostRecordRow.run_id == "run-1")
    )
    assert total == 2
