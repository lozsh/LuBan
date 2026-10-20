"""Trace（行为契约 5）：每节点 STEP_STARTED/STEP_COMPLETED 齐全、含 node 名，可回放。"""

from lubanagent.core import TraceEventType
from lubanagent.graph import GraphBuilder
from lubanagent.persistence import Database, TraceRepository


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
    started_nodes = [
        e.payload["node"] for e in events if e.type == TraceEventType.STEP_STARTED
    ]
    # a 一次、h 两次（interrupt 后 resume 节点重执行）、b 一次
    assert started_nodes == ["a", "h", "h", "b"]
    assert resumed.status == "completed"
