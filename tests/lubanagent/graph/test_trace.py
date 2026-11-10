"""Trace（行为契约 5）：每节点 STEP_STARTED/STEP_COMPLETED 齐全、含 node 名，可回放。"""

import logging

from lubanagent.config import configure_logging
from lubanagent.core import BrainProfile, RunContext, TraceEventType
from lubanagent.core.log_context import log_context
from lubanagent.graph import GraphBuilder
from lubanagent.observability.logging import DatabaseLogWriter
from lubanagent.persistence import Database, TraceRepository
from lubanagent.persistence.repositories.log import ApplicationLogRepository
from lubanagent.runtime import RunRequest

from .conftest import create_test_agent, seed_fake_brain


async def _events(db: Database, run_id: str):
    async with db.session() as session:
        return await TraceRepository(session).list_events(run_id)


async def test_step_events_for_every_node(make_runner, db: Database):
    flow = (
        GraphBuilder("traced")
        .step("a", lambda s: {"x": 1})
        .step("b", lambda s: {"y": s["x"] + 1})
        .entry("a")
        .edge("a", "b")
        .build()
    )
    result = await make_runner().run(flow, {})
    events = await _events(db, result.run_id)

    started = [e for e in events if e.type == TraceEventType.STEP_STARTED]
    completed = [e for e in events if e.type == TraceEventType.STEP_COMPLETED]
    assert [e.payload["node"] for e in started] == ["a", "b"]
    assert [e.payload["node"] for e in completed] == ["a", "b"]
    assert all(e.payload["flow"] == "traced" for e in started)
    assert "y" in completed[1].payload["state_keys"]  # state 摘要

    # Run 级事件齐全：STARTED + COMPLETED
    types = {e.type for e in events}
    assert TraceEventType.RUN_STARTED in types
    assert TraceEventType.RUN_COMPLETED in types


async def test_interrupt_resume_keeps_single_run_trace(make_runner, db: Database):
    flow = (
        GraphBuilder("traced-h")
        .step("a", lambda s: {"x": 1})
        .human("h", prompt="确认")
        .step("b", lambda s: {"done": True})
        .entry("a")
        .edge("a", "h")
        .edge("h", "b")
        .build()
    )
    runner = make_runner()
    first = await runner.run(flow, {})
    assert first.status == "interrupted"
    resumed = await runner.resume(flow, first.thread_id, "ok")

    events = await _events(db, first.run_id)
    started_nodes = [e.payload["node"] for e in events if e.type == TraceEventType.STEP_STARTED]
    # a 一次、h 两次（interrupt 后 resume 节点重执行）、b 一次
    assert started_nodes == ["a", "h", "h", "b"]
    assert resumed.status == "completed"


async def test_graph_resume_and_child_logs_keep_persisted_root_session(
    make_runner, make_runtime, db
):
    configure_logging()
    writer = DatabaseLogWriter(db)
    await seed_fake_brain(db, script=["answer"])
    await create_test_agent(db, key="parent-agent")
    await create_test_agent(db, key="child-agent")
    parent = await make_runtime().run(
        RunRequest(agent_key="parent-agent", user_input="hello", session_id="conversation-root")
    )

    def log_step(state):
        logging.getLogger("app.graph.node").info("node resumed or started")
        return {"question": "continue"}

    flow = (
        GraphBuilder("correlated-resume")
        .step("a", log_step)
        .human("confirm", prompt="continue?")
        .agent("child", "child-agent", input_from="question", output_to="answer")
        .step("b", log_step)
        .entry("a")
        .edge("a", "confirm")
        .edge("confirm", "child")
        .edge("child", "b")
        .build()
    )
    parent_ctx = RunContext(
        run_id=parent.id,
        session_id="conversation-root",
        agent_key="parent-agent",
        agent_version=1,
        brain_profile=BrainProfile.SMART,
    )
    runner = make_runner(runtime=make_runtime())
    with log_context(log_sink=writer.enqueue, request_id="first-request", subject="user:admin"):
        first = await runner.run(flow, {}, parent_ctx=parent_ctx)
    assert first.status == "interrupted"
    # A new runner has no live parent binding: persisted links are still sufficient
    # to recover the root session and IDs, but credentials are not inherited.
    restored_runner = make_runner(runtime=make_runtime())
    with log_context(log_sink=writer.enqueue, request_id="resume-request", subject="user:auditor"):
        done = await restored_runner.resume(flow, first.thread_id, "ok")
    assert done.status == "completed"
    await writer.flush()
    async with db.session() as session:
        items, total = await ApplicationLogRepository(session).list_logs(root_run_id=parent.id)
    assert total > 0 and {item["session_id"] for item in items} == {"conversation-root"}
    nodes = [item for item in items if item["logger"] == "app.graph.node"]
    assert len(nodes) == 2
    assert {item["request_id"] for item in nodes} == {"first-request", "resume-request"}
    assert all(item["run_id"] == first.run_id for item in nodes)
    assert all(item["parent_run_id"] == parent.id for item in nodes)
    children = [item for item in items if item["message"] == "Agent run started"]
    assert children and children[0]["parent_run_id"] == first.run_id
    assert children[0]["root_run_id"] == parent.id
    assert children[0]["subject"] == "user:auditor"
    assert any(item["message"] == "Graph execution resumed" for item in items)
    assert any(item["message"] == "Graph execution finished" for item in items)
    await writer.close()
