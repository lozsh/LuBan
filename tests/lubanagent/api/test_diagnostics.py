"""排查闭环：程序日志关联落库、Trace ID 查询、分页与请求错误关联。"""

import asyncio
import logging

import httpx
import pytest
from fastapi import APIRouter

from lubanagent.app import create_app, seed_defaults
from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import tool
from lubanagent.graph import GraphBuilder, GraphRunner, flow_as_tool
from lubanagent.model import ScriptedFakeChatModel
from lubanagent.persistence import Database, RunRepository
from lubanagent.persistence.repositories.log import ApplicationLogRepository
from tests.lubanagent.api.conftest import create_demo_agent, make_settings, sse_run_id
from tests.lubanagent.runtime.conftest import create_test_agent, seed_fake_brain


async def test_conversation_logs_persist_with_run_trace_and_session(client, registry, db):
    @tool(registry=registry, description="测试同步工具中的线程日志")
    def log_echo(text: str) -> str:
        logging.getLogger("app.test_echo").info("业务开始", extra={"detail": text})
        return f"echo:{text}"

    descriptor = registry.get_tool("log_echo").descriptor
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "log_echo", "args": {"text": "工具参数"}, "id": "call-a"}]},
            "最终回答完整内容",
        ],
    )
    await create_test_agent(
        db,
        key="log-agent",
        tools=["log_echo"],
        tool_signatures={"log_echo": descriptor.signature_hash},
    )
    run_ids = []
    for index in range(2):
        response = await client.post(
            "/v1/agents/log-agent/runs",
            json={
                "input": f"问题{index}",
                "session_id": "two-round-session",
            },
            headers={"X-Request-ID": f"diagnostic-request-{index}"},
        )
        assert response.status_code == 200
        assert response.headers["x-request-id"] == f"diagnostic-request-{index}"
        run_id = sse_run_id(response.text)
        assert response.headers["x-run-id"] == run_id
        assert response.headers["x-trace-id"] == f"trace-{run_id}"
        run_ids.append(run_id)
        trace = await client.get(f"/v1/traces/trace-{run_id}")
        assert trace.status_code == 200
        assert any(e["type"] == "model_call_completed" for e in trace.json()["events"])
    page = (
        await client.get(
            "/v1/diagnostics/runs",
            params={
                "session_id": "two-round-session",
                "limit": 1,
            },
        )
    ).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert page["items"][0]["output"] == "最终回答完整内容"
    second = (
        await client.get(
            "/v1/diagnostics/runs",
            params={
                "session_id": "two-round-session",
                "limit": 1,
                "offset": 1,
            },
        )
    ).json()
    assert second["items"][0]["id"] != page["items"][0]["id"]
    logs = (
        await client.get(
            "/v1/logs",
            params={
                "session_id": "two-round-session",
                "logger": "app.test_echo",
            },
        )
    ).json()
    assert logs["total"] == 2
    assert {item["run_id"] for item in logs["items"]} == set(run_ids)
    for item in logs["items"]:
        assert item["trace_id"] == "trace-" + item["run_id"]
        assert item["root_run_id"] == item["run_id"]
        assert item["session_id"] == "two-round-session"
        assert item["pathname"].endswith("test_diagnostics.py") and item["lineno"] > 0
        assert item["request_id"] in {"diagnostic-request-0", "diagnostic-request-1"}
    detail = await client.get(f"/v1/diagnostics/runs/trace-{run_ids[0]}")
    assert detail.status_code == 200
    assert detail.json()["run"]["output"] == "最终回答完整内容"
    assert detail.json()["log_count"] >= 3


async def test_concurrent_contexts_do_not_cross(client, registry, db):
    @tool(registry=registry, description="并发协程日志")
    async def parallel_log(text: str) -> str:
        await asyncio.sleep(0.01)
        logging.getLogger("app.parallel").info(text)
        return text

    descriptor = registry.get_tool("parallel_log").descriptor
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "parallel_log", "args": {"text": "并发工具"}, "id": "p"}]},
            "done",
        ],
    )
    await create_test_agent(
        db,
        key="parallel-agent",
        tools=["parallel_log"],
        tool_signatures={"parallel_log": descriptor.signature_hash},
    )

    async def execute(session):
        result = await client.post(
            "/v1/agents/parallel-agent/runs",
            json={
                "input": session,
                "session_id": session,
            },
        )
        return session, sse_run_id(result.text)

    pairs = await asyncio.gather(execute("session-a"), execute("session-b"))
    for session, run in pairs:
        items = (
            await client.get(
                "/v1/logs",
                params={
                    "session_id": session,
                    "logger": "app.parallel",
                },
            )
        ).json()["items"]
        assert len(items) == 1 and items[0]["run_id"] == run


async def test_log_redaction_and_exception_stack(client, registry, db):
    @tool(registry=registry, description="失败与脱敏")
    def fail_log(text: str) -> str:
        logging.getLogger("app.secrets").info(
            "password=synthetic-secret",
            extra={
                "api_key": "sk-synthetic-key",
                "nested": {"authorization": "Bearer hidden"},
            },
        )
        raise RuntimeError("password=hidden-exception")

    descriptor = registry.get_tool("fail_log").descriptor
    await seed_fake_brain(
        db,
        script=[
            {"tool_calls": [{"name": "fail_log", "args": {"text": "x"}, "id": "f"}]},
            "done",
        ],
    )
    await create_test_agent(
        db,
        key="fail-agent",
        tools=["fail_log"],
        tool_signatures={"fail_log": descriptor.signature_hash},
    )
    response = await client.post(
        "/v1/agents/fail-agent/runs", json={"input": "x", "session_id": "redact"}
    )
    assert response.status_code == 200
    logs = await client.get("/v1/logs", params={"session_id": "redact", "limit": 200})
    assert "synthetic-secret" not in logs.text and "sk-synthetic-key" not in logs.text
    assert "hidden-exception" not in logs.text
    stack = [item for item in logs.json()["items"] if item["exception"]]
    assert stack and "RuntimeError" in stack[0]["exception"]
    assert "fail_log" in stack[0]["exception"]


async def test_request_id_on_errors_and_filters(client):
    response = await client.post(
        "/v1/agents/unknown/runs", json={"input": "x"}, headers={"X-Request-ID": "error-request"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["details"]["request_id"] == "error-request"
    assert response.headers["x-request-id"] == "error-request"
    bad_id = await client.get("/health", headers={"X-Request-ID": "bad value"})
    assert bad_id.headers["x-request-id"] != "bad value"
    assert (await client.get("/v1/logs", params={"limit": 0})).status_code == 422
    assert (
        await client.get(
            "/v1/logs",
            params={
                "from_time": "2030-01-01T00:00:00Z",
                "to_time": "2020-01-01T00:00:00Z",
            },
        )
    ).status_code == 422
    assert (await client.get("/v1/diagnostics/runs/missing")).status_code == 404
    await create_demo_agent(client)


async def test_session_response_header_never_contains_control_characters(client):
    await create_demo_agent(client)
    for session_id in ("unsafe\r\nX-Forged: yes", "中文会话"):
        response = await client.post(
            "/v1/agents/demo-agent/runs", json={"input": "hello", "session_id": session_id}
        )
        assert response.status_code == 200
        assert "x-session-id" not in response.headers
        assert "x-forged" not in response.headers
        logs = (await client.get("/v1/logs", params={"session_id": session_id})).json()
        assert logs["total"] > 0


async def test_unexpected_error_has_request_id_and_persisted_stack(db, registry):
    extra = APIRouter(prefix="/v1/custom")

    @extra.get("/fail")
    async def fail():
        raise RuntimeError("password=never-show-this")

    application = create_app(
        db=db, registry=registry, settings=make_settings(), extra_routers=(extra,)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/custom/fail", headers={"X-Request-ID": "crash-1"})
        assert response.status_code == 500
        assert response.headers["x-request-id"] == "crash-1"
        assert response.json()["error"]["details"]["request_id"] == "crash-1"
        logs = (await client.get("/v1/logs", params={"request_id": "crash-1"})).json()
    assert any("RuntimeError" in (item["exception"] or "") for item in logs["items"])
    assert "never-show-this" not in str(logs)


async def test_multiple_app_sinks_and_startup_failure_cleanup(db, registry, tmp_path):
    second = Database(f"sqlite+aiosqlite:///{tmp_path}/second.db")
    from lubanagent.persistence import Base

    async with second.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await seed_defaults(second)

    async def startup():
        logging.getLogger("app.startup").info("startup recorded")
        raise RuntimeError("startup failed")

    first_app = create_app(db=db, registry=registry, settings=make_settings())
    second_app = create_app(
        db=second, registry=registry, settings=make_settings(), on_startup=startup
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first_app), base_url="http://test"
    ) as client:
        await client.get("/health", headers={"X-Request-ID": "first-app"})
    with pytest.raises(RuntimeError, match="startup failed"):
        async with second_app.router.lifespan_context(second_app):
            pass
    assert second_app.state.log_writer._closed
    assert second_app.state.log_writer._task.done()
    async with second.session() as session:
        _, first_count = await ApplicationLogRepository(session).list_logs(request_id="first-app")
        items, _ = await ApplicationLogRepository(session).list_logs(logger="app.startup")
    assert first_count == 0 and items[0]["message"] == "startup recorded"
    await second.close()


async def test_database_only_bootstrap_fails_without_accessible_admin(db, registry):
    application = create_app(
        db=db,
        registry=registry,
        settings=make_settings(auth_enabled=True, api_key=None, auth_database_enabled=True),
    )
    with pytest.raises(LubanError) as caught:
        async with application.router.lifespan_context(application):
            pass
    assert caught.value.code == ErrorCode.CONFIG_ERROR
    assert "LUBAN_ADMIN_PASSWORD" in caught.value.message
    assert application.state.log_writer._closed


async def test_nested_graph_run_detail_keeps_execution_tree_separate(
    client, app, db, registry, monkeypatch
):
    @tool(registry=registry, description="子 Agent 的业务日志")
    def child_echo(text: str) -> str:
        logging.getLogger("app.nested.child").info("nested child code ran")
        return text

    child = registry.get_tool("child_echo")
    await create_test_agent(
        db,
        key="nested-child",
        tools=["child_echo"],
        tool_signatures={"child_echo": child.descriptor.signature_hash},
    )

    def node_log(state):
        logging.getLogger("app.nested.graph").info("graph code ran")
        return {"input": state.get("input", "question")}

    flow = (
        GraphBuilder("nested")
        .step("prepare", node_log)
        .agent("child", "nested-child")
        .entry("prepare")
        .edge("prepare", "child")
        .build()
    )
    registered = flow_as_tool(
        flow,
        GraphRunner(db=db, runtime=app.state.ctx.runtime, registry=registry),
        key="nested_flow",
    )
    registry.register_tool(registered.descriptor, registered.handler)
    await create_test_agent(
        db,
        key="nested-parent",
        tools=["nested_flow"],
        tool_signatures={"nested_flow": registered.descriptor.signature_hash},
    )
    await seed_fake_brain(db, script=["placeholder"])
    scripts = [
        [
            {"tool_calls": [{"name": "nested_flow", "args": {"input": "question"}, "id": "g1"}]},
            "parent final answer",
        ],
        [
            {"tool_calls": [{"name": "child_echo", "args": {"text": "child"}, "id": "c1"}]},
            "child final answer",
        ],
        ["second round answer"],
    ]
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: ScriptedFakeChatModel(script=scripts.pop(0)),
    )
    first = await client.post(
        "/v1/agents/nested-parent/runs",
        json={"input": "question", "session_id": "nested-conversation"},
        headers={"X-Request-ID": "nested-request"},
    )
    assert first.status_code == 200
    root_id = sse_run_id(first.text)
    second = await client.post(
        "/v1/agents/nested-parent/runs",
        json={"input": "next question", "session_id": "nested-conversation"},
    )
    second_id = sse_run_id(second.text)
    logs = (
        await client.get(
            "/v1/logs", params={"session_id": "nested-conversation", "logger": "app.nested"}
        )
    ).json()["items"]
    assert len(logs) == 2
    assert {item["root_run_id"] for item in logs} == {root_id}
    assert {item["request_id"] for item in logs} == {"nested-request"}
    child_log = next(item for item in logs if item["logger"] == "app.nested.child")
    graph_log = next(item for item in logs if item["logger"] == "app.nested.graph")
    assert graph_log["parent_run_id"] == root_id
    assert child_log["parent_run_id"] == graph_log["run_id"]
    detail = (await client.get(f"/v1/diagnostics/runs/{child_log['trace_id']}")).json()
    assert detail["run"]["output"] == "child final answer"
    assert detail["runs"][0]["id"] == root_id
    assert len(detail["runs"]) == len(detail["traces"]) == 3
    assert second_id not in {item["id"] for item in detail["runs"]}
    async with db.session() as session:
        assert len(await RunRepository(session).run_tree(child_log["run_id"])) == 3
