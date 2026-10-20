"""验收 2：SSE 端到端 + Run 查询/取消。"""

from tests.lubanagent.api.conftest import create_demo_agent, sse_run_id
from tests.lubanagent.runtime.test_resume import _mark_resumable, _rewind_checkpoints


async def test_run_sse_end_to_end(client):
    await create_demo_agent(client)
    response = await client.post("/v1/agents/demo-agent/runs", json={"input": "你好"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    text = response.text
    assert "event: run.started" in text
    assert "event: run.completed" in text
    # 种子 fake 模型脚本回复（SEED_MODEL_DEFINITIONS.params.script）
    assert "你好，我是 LubanAgent。" in text

    run_id = sse_run_id(text)
    run = await client.get(f"/v1/runs/{run_id}")
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    assert run.json()["agent_key"] == "demo-agent"


async def test_run_with_history_and_session(client):
    await create_demo_agent(client, key="hist-agent")
    response = await client.post(
        "/v1/agents/hist-agent/runs",
        json={
            "input": "继续",
            "session_id": "s-1",
            "history": [
                {"role": "user", "content": "上一轮问题"},
                {"role": "assistant", "content": "上一轮回答"},
            ],
        },
    )
    assert response.status_code == 200
    assert "event: run.completed" in response.text
    run_id = sse_run_id(response.text)
    run = (await client.get(f"/v1/runs/{run_id}")).json()
    assert run["session_id"] == "s-1"


async def test_run_user_id_passthrough(client):
    """user_id 透传观测标签：请求体带什么落什么，引擎不做任何用户管理。"""
    await create_demo_agent(client, key="uid-agent")
    response = await client.post(
        "/v1/agents/uid-agent/runs",
        json={"input": "你好", "session_id": "s-u", "user_id": "u-1"},
    )
    assert response.status_code == 200
    run_id = sse_run_id(response.text)
    run = (await client.get(f"/v1/runs/{run_id}")).json()
    assert run["session_id"] == "s-u"
    assert run["user_id"] == "u-1"

    # 不传 → None；超长（>128）→ 422
    bare = await client.post("/v1/agents/uid-agent/runs", json={"input": "hi"})
    bare_id = sse_run_id(bare.text)
    assert (await client.get(f"/v1/runs/{bare_id}")).json()["user_id"] is None
    too_long = await client.post(
        "/v1/agents/uid-agent/runs", json={"input": "hi", "user_id": "x" * 129}
    )
    assert too_long.status_code == 422


async def test_run_agent_not_found(client):
    response = await client.post("/v1/agents/ghost/runs", json={"input": "hi"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"


async def test_run_version_not_found(client):
    await create_demo_agent(client)
    response = await client.post(
        "/v1/agents/demo-agent/runs", json={"input": "hi", "version": 99}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_VERSION_NOT_FOUND"


async def test_list_runs(client):
    await create_demo_agent(client)
    await create_demo_agent(client, key="other-agent")
    first = await client.post("/v1/agents/demo-agent/runs", json={"input": "你好"})
    run_id = sse_run_id(first.text)
    await client.post(
        "/v1/agents/other-agent/runs",
        json={"input": "你好", "session_id": "s-9", "user_id": "u-9"},
    )

    runs = (await client.get("/v1/runs")).json()
    assert len(runs) == 2
    assert any(run["id"] == run_id for run in runs)
    # 按创建时间倒序
    assert runs[0]["created_at"] >= runs[1]["created_at"]

    by_agent = (await client.get("/v1/runs", params={"agent_key": "demo-agent"})).json()
    assert len(by_agent) == 1
    assert by_agent[0]["agent_key"] == "demo-agent"

    by_session = (await client.get("/v1/runs", params={"session_id": "s-9"})).json()
    assert len(by_session) == 1
    assert by_session[0]["session_id"] == "s-9"

    by_user = (await client.get("/v1/runs", params={"user_id": "u-9"})).json()
    assert len(by_user) == 1
    assert by_user[0]["session_id"] == "s-9"
    assert by_user[0]["user_id"] == "u-9"
    ghost_user = (await client.get("/v1/runs", params={"user_id": "ghost"})).json()
    assert ghost_user == []

    empty = (await client.get("/v1/runs", params={"agent_key": "ghost"})).json()
    assert empty == []

    limited = (await client.get("/v1/runs", params={"limit": 1})).json()
    assert len(limited) == 1

    invalid = await client.get("/v1/runs", params={"limit": 0})
    assert invalid.status_code == 422


async def test_get_run_not_found(client):
    response = await client.get("/v1/runs/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUN_NOT_FOUND"


async def test_cancel_run(client):
    await create_demo_agent(client)
    response = await client.post("/v1/agents/demo-agent/runs", json={"input": "你好"})
    run_id = sse_run_id(response.text)
    # 已完成的 Run 取消返回 False；不存在的 Run 404
    cancel = await client.post(f"/v1/runs/{run_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json() == {"cancelled": False}
    missing = await client.post("/v1/runs/nope/cancel")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RUN_NOT_FOUND"


async def test_run_with_instructions_override(client):
    """G2 补丁：instructions_override 仅本次 Run 生效，不改版本化定义。"""
    from lubanagent.persistence import AgentRepository

    create = await client.post(
        "/v1/agents",
        json={
            "key": "override-bot",
            "name": "override-bot",
            "instructions": "你是原始人设。",
            "brain": {"profile": "smart"},
        },
    )
    assert create.status_code == 201
    response = await client.post(
        "/v1/agents/override-bot/runs",
        json={"input": "hi", "instructions_override": "你是临时人设。"},
    )
    assert response.status_code == 200
    assert "event: run.completed" in response.text
    # 定义未被改动
    db = client._transport.app.state.ctx.db  # 测试内直取装配上下文
    async with db.session() as session:
        definition = await AgentRepository(session).get_agent("override-bot")
    assert definition is not None
    assert definition.instructions == "你是原始人设。"


# ---------------------------------------------------- 缺口 #2：resume 端点契约


async def test_resume_endpoint_error_contract(client):
    # run 不存在 → 404 RUN_NOT_FOUND
    response = await client.post("/v1/runs/ghost/resume")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUN_NOT_FOUND"

    await create_demo_agent(client)
    first = await client.post("/v1/agents/demo-agent/runs", json={"input": "你好"})
    assert "event: run.completed" in first.text
    run_id = sse_run_id(first.text)

    # 终态拒绝 → 422 VALIDATION_ERROR
    response = await client.post(f"/v1/runs/{run_id}/resume")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_resume_endpoint_sse_happy_path(db, client):
    """默认装配启用 checkpoint：回退现场后 resume 走 SSE，帧格式与创建 Run 一致。"""
    await create_demo_agent(client)
    first = await client.post("/v1/agents/demo-agent/runs", json={"input": "你好"})
    assert first.status_code == 200
    run_id = sse_run_id(first.text)

    # 构造崩溃现场：回退 checkpoint + Run 行置 RUNNING（种子 fake 单步回复）
    await _rewind_checkpoints(db, run_id, keep=1)
    await _mark_resumable(db, run_id)

    response = await client.post(f"/v1/runs/{run_id}/resume")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert "event: run.resumed" in response.text
    assert "event: run.completed" in response.text

    run = (await client.get(f"/v1/runs/{run_id}")).json()
    assert run["status"] == "completed"


async def test_create_app_checkpointer_assembly(db, settings, registry):
    """create_app 缺省启用 LubanCheckpointer(db)；显式传 None 关闭且 resume 显式 CONFIG_ERROR。"""
    import pytest

    from lubanagent.app import create_app
    from lubanagent.core import ErrorCode, LubanError
    from lubanagent.persistence import LubanCheckpointer

    app_default = create_app(settings=settings, registry=registry, db=db)
    runtime = app_default.state.ctx.runtime
    assert isinstance(runtime._checkpointer, LubanCheckpointer)

    app_off = create_app(settings=settings, registry=registry, db=db, checkpointer=None)
    off_runtime = app_off.state.ctx.runtime
    assert off_runtime._checkpointer is None
    with pytest.raises(LubanError) as exc:
        await anext(off_runtime.stream_resume("whatever"))
    assert exc.value.code is ErrorCode.CONFIG_ERROR
