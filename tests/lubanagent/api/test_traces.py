"""验收 6：traces/{run_id} —— 完成 Run 后返回 trace + 非空 events。"""

from datetime import UTC, datetime

from lubanagent.core import BrainProfile, Run, RunStatus, TraceEvent, TraceEventType
from lubanagent.observability import TraceWriter
from tests.lubanagent.api.conftest import create_demo_agent, sse_run_id


async def test_trace_after_completed_run(client):
    await create_demo_agent(client)
    response = await client.post("/v1/agents/demo-agent/runs", json={"input": "你好"})
    run_id = sse_run_id(response.text)

    trace = await client.get(f"/v1/traces/{run_id}")
    assert trace.status_code == 200
    body = trace.json()
    assert body["trace"]["run_id"] == run_id
    assert body["trace"]["status"] == "completed"
    assert len(body["events"]) > 0
    types = {event["type"] for event in body["events"]}
    assert "run_started" in types
    assert "run_completed" in types


async def test_trace_not_found(client):
    response = await client.get("/v1/traces/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUN_NOT_FOUND"


async def test_trace_api_serves_redacted_events(db, client):
    """trace API 输出与 DB 审计副本同源：秘密键字段不出现在响应里。"""
    await create_demo_agent(client)
    writer = TraceWriter(db)
    run = Run(
        id="trace-redact-run",
        agent_key="demo-agent",
        agent_version=1,
        status=RunStatus.RUNNING,
        brain_profile=BrainProfile.SMART,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )
    await writer.start_run(run)
    await writer.emit(
        TraceEvent(
            id="evt-redact-api",
            run_id="trace-redact-run",
            type=TraceEventType.TOOL_CALL_STARTED,
            payload={"tool": "refund", "args": {"api_key": "sk-synthetic-secret"}},
            created_at=datetime.now(UTC),
        )
    )
    response = await client.get("/v1/traces/trace-redact-run")
    assert response.status_code == 200
    assert "sk-synthetic-secret" not in response.text
