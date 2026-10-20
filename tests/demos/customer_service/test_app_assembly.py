"""H02: 用户装配的团队路由与框架鉴权、限流及 SPA 的顺序。"""

import httpx
import pytest

from app import main as user_main
from app.api import teams
from app.knowledge_sources import product_faq, project_docs
from lubanagent.config import Settings


@pytest.fixture
def assembled_app(db, registry, monkeypatch, tmp_path, request):
    dist = tmp_path / "dist" if request.param else tmp_path / "missing-dist"
    if request.param:
        dist.mkdir()
        (dist / "index.html").write_text("<html>temporary spa</html>")
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        observability_backend="none",
        auth_enabled=True,
        api_key="synthetic-test-key",
        rate_limit_per_minute=2,
        env="dev",
    )
    monkeypatch.setattr(user_main, "get_settings", lambda: settings)
    monkeypatch.setattr(user_main, "create_database", lambda _: db)
    monkeypatch.setattr(user_main, "default_registry", registry)
    monkeypatch.setattr(product_faq, "default_registry", registry)
    monkeypatch.setattr(project_docs, "default_registry", registry)
    return user_main.create_app(ui_dist=dist), request.param


@pytest.mark.parametrize("assembled_app", [False, True], indirect=True, ids=["no-dist", "dist"])
async def test_runtime_receives_capability_registries(assembled_app):
    """H09：工厂把 skill/mcp 注册表同实例透传 Runtime（注册表/运行视图/管理 API 同源）。"""
    application, _ = assembled_app
    ctx = application.state.ctx
    assert ctx.runtime._skill_registry is application.state.skill_registry
    assert ctx.runtime._mcp_registry is not None


@pytest.mark.parametrize("assembled_app", [False, True], indirect=True, ids=["no-dist", "dist"])
async def test_team_route_auth_rate_limit_and_spa(assembled_app, monkeypatch):
    application, has_dist = assembled_app
    calls = []

    async def fake_run(self, team, user_input, *, thread_id=None, parent_ctx=None):
        calls.append((team.name, user_input, thread_id))
        return type("Result", (), {
            "status": "completed", "output": "fake answer",
            "run_id": "fake-run", "thread_id": thread_id,
        })()

    monkeypatch.setattr(teams.TeamRunner, "run", fake_run)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        path = "/v1/teams/cs-team/runs"
        body = {"input": "hello", "thread_id": "fake-thread"}
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            denied = await client.post(path, json=body, headers=headers)
            assert denied.status_code == 401
            assert denied.json()["error"]["code"] == "AUTH_FAILED"
            assert calls == []

        headers = {"Authorization": "Bearer synthetic-test-key"}
        ok = await client.post(path, json=body, headers=headers)
        assert ok.status_code == 200, ok.text
        assert ok.json() == {
            "status": "completed", "output": "fake answer",
            "run_id": "fake-run", "thread_id": "fake-thread",
        }
        assert calls == [("cs-team", "hello", "fake-thread")]

        again = await client.post(path, json=body, headers=headers)
        assert again.status_code == 200
        limited = await client.post(path, json=body, headers=headers)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert len(calls) == 2

        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        for unknown in ("/v1/unregistered", "/v1"):
            missing = await client.get(unknown)
            assert missing.status_code == 404
            assert "application/json" in missing.headers["content-type"]
            assert "temporary spa" not in missing.text

        spa = await client.get("/agents")
        if has_dist:
            assert spa.status_code == 200
            assert "temporary spa" in spa.text
            asset = await client.get("/assets/not-found.js")
            assert asset.status_code == 404
            assert "temporary spa" not in asset.text
            asset = await client.get("/favicon.ico")
            assert asset.status_code == 404
            assert "temporary spa" not in asset.text
        else:
            assert spa.status_code == 404
