"""agent 节点：fake Agent 子 Run 落库且 parent_run_id=图 Run；无 runtime → CONFIG_ERROR。"""

import pytest

from lubanagent.core import ErrorCode, LubanError, RunStatus
from lubanagent.graph import GraphBuilder
from lubanagent.persistence import Database, RunRepository

from .conftest import create_test_agent, seed_fake_brain


async def test_agent_node_produces_child_run(make_runner, make_runtime, db: Database):
    await seed_fake_brain(db, script=["退款政策：七天无理由"])
    await create_test_agent(db, key="faq-agent")

    flow = (
        GraphBuilder("consult")
        .agent("ask", "faq-agent", input_from="question", output_to="answer")
        .step("wrap", lambda s: {"final": f"[{s['answer']}]"})
        .entry("ask")
        .edge("ask", "wrap")
        .build()
    )
    runner = make_runner(runtime=make_runtime())
    result = await runner.run(flow, {"question": "能退款吗"})

    assert result.status == "completed"
    assert result.output["answer"] == "退款政策：七天无理由"
    assert result.output["final"] == "[退款政策：七天无理由]"

    # 行为契约 3：子 Run 落库且 parent_run_id=图 Run 的 run_id
    async with db.session() as session:
        children = await RunRepository(session).list_runs(
            "faq-agent", session_id=result.thread_id
        )
    assert len(children) == 1
    child = children[0]
    assert child.parent_run_id == result.run_id
    assert child.status == RunStatus.COMPLETED
    assert child.agent_version == 1  # 子 Run 绑各自 agent 版本

    # 图 Run 本身是 graph:<flow> 标记
    async with db.session() as session:
        graph_run = await RunRepository(session).get_run(result.run_id)
    assert graph_run.agent_key == "graph:consult"
    assert graph_run.agent_version == 0


async def test_agent_node_child_run_inherits_user_id(
    make_runner, make_runtime, db: Database
):
    """user_id 继承：图 Run 与成员子 Run 都带父 ctx 的 user_id（按用户过滤不漏子 Run）。"""
    await seed_fake_brain(db, script=["好的"])
    await create_test_agent(db, key="faq-agent")

    flow = (
        GraphBuilder("uid-flow")
        .agent("ask", "faq-agent", input_from="question", output_to="answer")
        .entry("ask")
        .build()
    )
    from lubanagent.core import BrainProfile, RunContext

    parent_ctx = RunContext(
        run_id="parent-run", agent_key="caller", agent_version=1, user_id="u-777",
        brain_profile=BrainProfile.SMART,
    )
    runner = make_runner(runtime=make_runtime())
    result = await runner.run(flow, {"question": "hi"}, parent_ctx=parent_ctx)

    assert result.status == "completed"
    async with db.session() as session:
        graph_run = await RunRepository(session).get_run(result.run_id)
        children = await RunRepository(session).list_runs(
            "faq-agent", session_id=result.thread_id
        )
    assert graph_run is not None and graph_run.user_id == "u-777"
    assert len(children) == 1 and children[0].user_id == "u-777"


async def test_agent_node_without_runtime_config_error(make_runner):
    flow = GraphBuilder("g").agent("ask", "any-agent").entry("ask").build()
    runner = make_runner()  # 未注入 runtime
    with pytest.raises(LubanError) as exc_info:
        await runner.run(flow, {"input": "hi"})
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
