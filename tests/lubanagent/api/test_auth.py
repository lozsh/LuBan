"""验收 3：鉴权三态（关 / 错 key 401 / 超速率 429）、health 免鉴权与装配期 fail fast（R6）。"""

import httpx
from fastapi import APIRouter

from lubanagent.api.auth import InMemoryRateLimiter
from lubanagent.app import create_app
from tests.lubanagent.api.conftest import make_client, make_settings


async def test_auth_disabled_allows_anonymous(client):
    response = await client.get("/v1/agents")
    assert response.status_code == 200


async def test_auth_enabled_rejects_missing_or_wrong_key(db, registry):
    async with await make_client(db, registry, auth_enabled=True, api_key="secret") as client:
        missing = await client.get("/v1/agents")
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "AUTH_FAILED"

        wrong = await client.get("/v1/agents", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        assert wrong.json()["error"]["code"] == "AUTH_FAILED"

        ok = await client.get("/v1/agents", headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200


async def test_extra_router_is_protected_even_without_its_own_dependency(db, registry, tmp_path):
    extra = APIRouter(prefix="/v1/custom")
    calls = []

    @extra.get("/ping")
    async def ping():
        calls.append(True)
        return {"status": "ok"}

    application = create_app(
        db=db,
        registry=registry,
        settings=make_settings(auth_enabled=True, api_key="synthetic-key", rate_limit_per_minute=1),
        extra_routers=(extra,),
        ui_dist=tmp_path / "missing-dist",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        path = "/v1/custom/ping"
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = await client.get(path, headers=headers)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "AUTH_FAILED"
            assert calls == []
        headers = {"Authorization": "Bearer synthetic-key"}
        assert (await client.get(path, headers=headers)).json() == {"status": "ok"}
        response = await client.get(path, headers=headers)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMITED"
        assert calls == [True]


async def test_health_skips_auth(db, registry):
    async with await make_client(db, registry, auth_enabled=True, api_key="secret") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


async def test_rate_limit_exceeded(db, registry):
    async with await make_client(db, registry, rate_limit_per_minute=2) as client:
        assert (await client.get("/v1/agents")).status_code == 200
        assert (await client.get("/v1/agents")).status_code == 200
        limited = await client.get("/v1/agents")
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"


async def test_anonymous_rate_limit_ignores_unverified_headers(db, registry):
    async with await make_client(db, registry, rate_limit_per_minute=2) as client:
        for token in ("a", "b"):
            assert (
                await client.get(
                    "/v1/agents",
                    headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": token},
                )
            ).status_code == 200
        assert (
            await client.get("/v1/agents", headers={"Authorization": "Bearer c"})
        ).status_code == 429
        assert (await client.get("/v1/agents")).status_code == 429


async def test_verified_subject_not_raw_token_is_rate_limit_key(db, registry):
    from lubanagent.extension import Principal

    class SubjectProvider:
        async def verify(self, request):
            return Principal(subject=request.headers["x-user"])

    registry.register_auth(SubjectProvider())
    async with await make_client(
        db, registry, auth_enabled=True, rate_limit_per_minute=2
    ) as client:
        for token in ("one", "two"):
            assert (
                await client.get(
                    "/v1/agents", headers={"X-User": "alice", "Authorization": f"Bearer {token}"}
                )
            ).status_code == 200
        assert (
            await client.get(
                "/v1/agents", headers={"X-User": "alice", "Authorization": "Bearer three"}
            )
        ).status_code == 429
        assert (await client.get("/v1/agents", headers={"X-User": "bob"})).status_code == 200


async def test_rate_limiter_eviction_and_fail_closed_at_capacity(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("lubanagent.api.auth.time.monotonic", lambda: clock[0])
    limiter = InMemoryRateLimiter(2, max_keys=2)
    assert await limiter.allow("a")
    assert await limiter.allow("b")
    assert not await limiter.allow("c")
    assert await limiter.allow("a")
    assert not await limiter.allow("a")
    clock[0] = 60.0
    assert await limiter.allow("c")
    assert len(limiter._hits) == 1


async def test_auth_enabled_without_key_fails_fast(db, registry):
    """R6：开启鉴权但未配 key 且无自定义 provider → 装配期 fail fast，而非每个请求 500。"""
    import pytest

    from lubanagent.core import ErrorCode, LubanError

    with pytest.raises(LubanError) as exc_info:
        await make_client(db, registry, auth_enabled=True, api_key=None)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    # 错误信息含三条修复出路：设 API_KEY / 注册自定义 AuthProvider / 关 AUTH_ENABLED
    message = exc_info.value.message
    assert "API_KEY" in message
    assert "AuthProvider" in message
    assert "AUTH_ENABLED" in message


async def test_auth_enabled_with_custom_provider_starts_without_key(db, registry):
    """合法路径：注册了自定义 AuthProvider 时，未配 API_KEY 也能正常装配（不触发 fail fast）。"""
    from lubanagent.core import ErrorCode, LubanError
    from lubanagent.extension import Principal

    class HeaderNameAuth:
        async def verify(self, request):
            if request.headers.get("x-user") == "jian":
                return Principal(subject="jian", claims={"auth": "custom"})
            raise LubanError(ErrorCode.AUTH_FAILED, "unknown user")

    registry.register_auth(HeaderNameAuth())
    async with await make_client(db, registry, auth_enabled=True, api_key=None) as client:
        ok = await client.get("/v1/agents", headers={"X-User": "jian"})
        assert ok.status_code == 200


async def test_custom_auth_provider_takes_priority(db, registry):
    """契约决策 2：用户注册的 AuthProvider 优先于内置 ApiKeyAuthProvider。"""
    from lubanagent.core import ErrorCode, LubanError
    from lubanagent.extension import Principal

    class HeaderNameAuth:
        async def verify(self, request):
            if request.headers.get("x-user") == "jian":
                return Principal(subject="jian", claims={"auth": "custom"})
            raise LubanError(ErrorCode.AUTH_FAILED, "unknown user")

    registry.register_auth(HeaderNameAuth())
    async with await make_client(db, registry, auth_enabled=True, api_key="secret") as client:
        # 内置 key 不再有效（用户 provider 接管鉴权）
        builtin = await client.get("/v1/agents", headers={"Authorization": "Bearer secret"})
        assert builtin.status_code == 401
        # 用户 provider 的规则生效
        ok = await client.get("/v1/agents", headers={"X-User": "jian"})
        assert ok.status_code == 200
