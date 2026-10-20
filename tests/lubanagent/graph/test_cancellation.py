"""取消传播（契约决策 3）：父 token 取消 → 节点边界 RunCancelledError → Run 终态 CANCELLED。"""

import pytest

from lubanagent.core import BrainProfile, RunCancelledError, RunContext, RunStatus
from lubanagent.graph import GraphBuilder
from lubanagent.persistence import Database, RunRepository
from lubanagent.runtime import CancellationToken


def _parent_ctx(token: CancellationToken) -> RunContext:
    return RunContext(
        run_id="parent-run",
        agent_key="parent-agent",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
        metadata={"cancellation_token": token},
    )


async def test_parent_token_cancelled_before_start(make_runner, db: Database):
    token = CancellationToken()
    token.cancel()  # 父已取消

    flow = (
        GraphBuilder("g")
        .step("a", lambda s: {"a": 1})
        .step("b", lambda s: {"b": 2})
        .entry("a")
        .edge("a", "b")
        .build()
    )
    runner = make_runner()
    with pytest.raises(RunCancelledError):
        await runner.run(flow, {}, parent_ctx=_parent_ctx(token))

    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:g")
    assert len(runs) == 1
    assert runs[0].status == RunStatus.CANCELLED
    assert runs[0].parent_run_id == "parent-run"


async def test_parent_token_cancelled_mid_flow(make_runner, db: Database):
    token = CancellationToken()

    def first(state):
        token.cancel()  # 第一个节点内取消 → 第二个节点边界必须抛
        return {"a": 1}

    flow = (
        GraphBuilder("g")
        .step("a", first)
        .step("b", lambda s: {"b": 2})
        .entry("a")
        .edge("a", "b")
        .build()
    )
    runner = make_runner()
    with pytest.raises(RunCancelledError):
        await runner.run(flow, {}, parent_ctx=_parent_ctx(token))

    async with db.session() as session:
        runs = await RunRepository(session).list_runs("graph:g")
    assert runs[0].status == RunStatus.CANCELLED


async def test_no_parent_ctx_runs_normally(make_runner):
    flow = GraphBuilder("g").step("a", lambda s: {"a": 1}).entry("a").build()
    result = await make_runner().run(flow, {})
    assert result.status == "completed"
