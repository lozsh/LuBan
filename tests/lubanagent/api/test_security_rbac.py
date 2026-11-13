"""平台 RBAC 验收：登录、角色授权、会话撤销和后端权限裁决。"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import delete

from lubanagent.api.auth import required_permission
from lubanagent.app import create_app, seed_defaults
from lubanagent.core import ErrorCode, LubanError, ToolDescriptor
from lubanagent.extension import Principal, Registry
from lubanagent.persistence import (
    Database,
    SecurityRepository,
)
from lubanagent.persistence.models.security import (
    AuthSessionRow,
    RolePermissionRow,
    RoleRow,
    UserRoleRow,
    UserRow,
)
from lubanagent.persistence.repositories.security import hash_password, token_hash
from tests.lubanagent.api.conftest import make_settings

PASSWORD = "a-very-secret-password"
MASTER = "master-key"


async def make_security_client(db: Database, registry: Registry, **overrides) -> httpx.AsyncClient:
    await seed_defaults(db)
    settings = make_settings(
        auth_enabled=True, api_key=MASTER, observability_backend="none", **overrides
    )
    application = create_app(settings=settings, registry=registry, db=db)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test")


async def create_account(
    client: httpx.AsyncClient,
    username: str,
    *,
    role_keys: list[str],
    enabled: bool = True,
) -> dict:
    response = await client.post(
        "/v1/security/users",
        headers={"Authorization": f"Bearer {MASTER}"},
        json={
            "username": username,
            "display_name": username.title(),
            "password": PASSWORD,
            "role_keys": role_keys,
            "enabled": enabled,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def login(client: httpx.AsyncClient, username: str, password: str = PASSWORD) -> str:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_master_key_me_and_security_route_contract(db, registry):
    async with await make_security_client(db, registry) as client:
        assert (await client.get("/v1/v1/security/users")).status_code == 404
        response = await client.get("/v1/auth/me", headers=bearer(MASTER))
        assert response.status_code == 200
        body = response.json()
        assert body["subject"] == "api-key-holder"
        assert body["username"] == "api-key-holder"
        assert body["display_name"] == "Bootstrap Admin"
        assert body["roles"] == ["admin"]
        assert body["permissions"] == ["*"]
        assert body["auth_enabled"] is True
        assert body["is_bootstrap"] is True
        assert "password_hash" not in body
        users = await client.get("/v1/security/users", headers=bearer(MASTER))
        assert users.status_code == 200


async def test_custom_provider_empty_scopes_is_read_only(db, registry):
    class ReadOnlyProvider:
        async def verify(self, request):
            return Principal(subject="external-user")

    registry.register_auth(ReadOnlyProvider())
    settings = make_settings(auth_enabled=True, api_key=None, observability_backend="none")
    application = create_app(settings=settings, registry=registry, db=db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        read = await client.get("/v1/agents", headers={"X-User": "any"})
        assert read.status_code == 200
        write = await client.post(
            "/v1/agents",
            headers={"X-User": "any"},
            json={"key": "no", "name": "no", "instructions": "no", "tools": []},
        )
        assert write.status_code == 403
        assert write.json()["error"]["code"] == "PERMISSION_DENIED"
        me = await client.get("/v1/auth/me", headers={"X-User": "any"})
        assert me.status_code == 200
        assert me.json()["permissions"] == ["config:read"]
        assert me.json()["subject"] == "external-user"


async def test_brain_slot_catalog_permissions_and_live_revocation(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "slot-viewer", role_keys=["viewer"])
        await create_account(client, "slot-operator", role_keys=["operator"])
        viewer = bearer(await login(client, "slot-viewer"))
        operator = bearer(await login(client, "slot-operator"))
        for path in [
            "/v1/brain-profiles",
            "/v1/brain-profiles/smart/references",
            "/v1/brain-mappings",
        ]:
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers=viewer)).status_code == 200
        writes = [
            ("POST", "/v1/brain-profiles", {"key": "blocked", "name": "Blocked"}),
            ("PATCH", "/v1/brain-profiles/smart", {"name": "Blocked"}),
            ("DELETE", "/v1/brain-profiles/vision", None),
            ("PUT", "/v1/brain-mappings", {"profile": "smart", "model_key": "fake-chat"}),
        ]
        for method, path, body in writes:
            denied = await client.request(method, path, headers=viewer, json=body)
            assert denied.status_code == 403, denied.text
            assert denied.json()["error"]["details"]["required_permission"] == "config:write"
        created = await client.post(
            "/v1/brain-profiles",
            headers=operator,
            json={"key": "allowed", "name": "Allowed", "model_key": "fake-chat"},
        )
        assert created.status_code == 201
        assert (
            await client.patch(
                "/v1/brain-profiles/allowed", headers=operator, json={"name": "Edited"}
            )
        ).status_code == 200
        assert (
            await client.delete("/v1/brain-profiles/allowed", headers=operator)
        ).status_code == 200
        assert (
            await client.patch(
                "/v1/security/roles/operator",
                headers=bearer(MASTER),
                json={"name": "Read only", "permissions": ["config:read"]},
            )
        ).status_code == 200
        assert (
            await client.post(
                "/v1/brain-profiles",
                headers=operator,
                json={"key": "revoked", "name": "Revoked"},
            )
        ).status_code == 403
        assert (await client.get("/v1/brain-profiles", headers=operator)).status_code == 200


async def test_database_token_works_while_master_key_configured(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "root-admin", role_keys=["admin"])
        token = await login(client, "root-admin")
        me = await client.get("/v1/auth/me", headers=bearer(token))
        assert me.status_code == 200
        body = me.json()
        assert body["id"]
        assert body["role_keys"] == ["admin"]
        assert body["roles"] == ["admin"]
        assert len(body["permissions"]) == 10
        assert body["subject"] == f"user:{body['id']}"
        assert "password_hash" not in body
        users = await client.get("/v1/security/users", headers=bearer(token))
        assert users.status_code == 200


async def test_role_change_and_disable_take_effect_immediately(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "root-admin", role_keys=["admin"])
        await create_account(client, "operator", role_keys=["operator"])
        admin_token = await login(client, "root-admin")
        operator_token = await login(client, "operator")

        before = await client.post(
            "/v1/agents",
            headers=bearer(operator_token),
            json={"key": "demo", "name": "demo", "instructions": "demo", "tools": []},
        )
        assert before.status_code == 201

        shrink = await client.patch(
            "/v1/security/roles/operator",
            headers=bearer(admin_token),
            json={"name": "操作员", "permissions": ["config:read"]},
        )
        assert shrink.status_code == 200
        denied = await client.post(
            "/v1/agents",
            headers=bearer(operator_token),
            json={"key": "demo-2", "name": "demo", "instructions": "demo", "tools": []},
        )
        assert denied.status_code == 403

        users = (await client.get("/v1/security/users", headers=bearer(admin_token))).json()
        operator_id = next(user["id"] for user in users if user["username"] == "operator")
        disabled = await client.patch(
            f"/v1/security/users/{operator_id}",
            headers=bearer(admin_token),
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert (await client.get("/v1/auth/me", headers=bearer(operator_token))).status_code == 401


async def test_password_reset_revokes_existing_sessions(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "root-admin", role_keys=["admin"])
        token = await login(client, "root-admin")
        users = (await client.get("/v1/security/users", headers=bearer(token))).json()
        user_id = users[0]["id"]
        new_password = "another-very-secret-password"
        response = await client.patch(
            f"/v1/security/users/{user_id}",
            headers=bearer(token),
            json={"password": new_password},
        )
        assert response.status_code == 200
        assert (await client.get("/v1/auth/me", headers=bearer(token))).status_code == 401
        assert await login(client, "root-admin", new_password)


async def test_logout_revokes_token_immediately(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "root-admin", role_keys=["admin"])
        token = await login(client, "root-admin")
        response = await client.post("/v1/auth/logout", headers=bearer(token))
        assert response.status_code == 200
        assert (await client.get("/v1/auth/me", headers=bearer(token))).status_code == 401


async def test_login_rate_limit_buckets_by_client_host_not_username(db, registry):
    async with await make_security_client(db, registry, rate_limit_per_minute=1) as client:
        first = await client.post(
            "/v1/auth/login", json={"username": "missing-one", "password": PASSWORD}
        )
        assert first.status_code == 401
        second = await client.post(
            "/v1/auth/login", json={"username": "missing-two", "password": PASSWORD}
        )
        assert second.status_code == 429


async def test_seed_is_idempotent_and_does_not_resurrect_revoked_operator_grants(db):
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        await repo.update_role("operator", name="受控操作员", permissions=["config:read"])
        await repo.seed()
        roles = {role["key"]: role for role in await repo.list_roles()}
        assert roles["operator"]["name"] == "受控操作员"
        assert roles["operator"]["permissions"] == ["config:read"]
        assert len(roles["admin"]["permissions"]) == 10


async def test_protected_admin_seed_regains_missing_permission(db):
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        await session.execute(
            delete(RolePermissionRow).where(
                RolePermissionRow.role_key == "admin",
                RolePermissionRow.permission_key == "security:manage",
            )
        )
        await repo.seed()
        roles = {role["key"]: role for role in await repo.list_roles()}
        assert "security:manage" in roles["admin"]["permissions"]


async def test_active_admin_count_ignores_disabled_users(db):
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        first = await repo.create_user(
            username="admin-one", display_name="one", password=PASSWORD, role_keys=["admin"]
        )
        second = await repo.create_user(
            username="admin-two", display_name="two", password=PASSWORD, role_keys=["admin"]
        )
        assert len(await repo._active_admin_ids()) == 2
        assert await repo.update_user(second["id"], enabled=False)
        assert await repo._active_admin_ids() == {first["id"]}
        with pytest.raises(Exception) as caught:
            await repo.update_user(first["id"], enabled=False)
        assert "不能停用或移除最后一个管理员" in str(caught.value)


async def test_last_admin_cannot_be_demoted(db):
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        admin = await repo.create_user(
            username="solo-admin", display_name="solo", password=PASSWORD, role_keys=["admin"]
        )
        with pytest.raises(LubanError) as caught:
            await repo.update_user(admin["id"], role_keys=["viewer"])
        assert "不能停用或移除最后一个管理员" in str(caught.value)


async def test_concurrent_admin_disable_cannot_empty_admin_set(db):
    """SQLite 条件 UPDATE 兜底并发：两个事务不能同时删掉最后一个活跃管理员。"""
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        first = await repo.create_user(
            username="race-admin-one",
            display_name="one",
            password=PASSWORD,
            role_keys=["admin"],
        )
        second = await repo.create_user(
            username="race-admin-two",
            display_name="two",
            password=PASSWORD,
            role_keys=["admin"],
        )
    database_url = str(db.engine.url)

    async def disable(user_id: str) -> None:
        independent = Database(database_url)
        try:
            async with independent.session() as session:
                await SecurityRepository(session).update_user(user_id, enabled=False)
        finally:
            await independent.close()

    outcomes = await asyncio.gather(
        disable(first["id"]), disable(second["id"]), return_exceptions=True
    )
    successes = [result for result in outcomes if not isinstance(result, BaseException)]
    assert len(successes) == 1
    async with db.session() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(UserRow)
            .join(UserRoleRow, UserRoleRow.user_id == UserRow.id)
            .where(UserRoleRow.role_key == "admin", UserRow.enabled.is_(True))
        )
    assert count == 1


async def test_concurrent_demotion_and_disable_keep_one_admin(db):
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        first = await repo.create_user(
            username="mixed-admin-one",
            display_name="one",
            password=PASSWORD,
            role_keys=["admin"],
        )
        second = await repo.create_user(
            username="mixed-admin-two",
            display_name="two",
            password=PASSWORD,
            role_keys=["admin"],
        )
    database_url = str(db.engine.url)

    async def run(operation) -> None:
        independent = Database(database_url)
        try:
            async with independent.session() as session:
                await operation(SecurityRepository(session))
        finally:
            await independent.close()

    async def demote(repo: SecurityRepository) -> None:
        await repo.update_user(first["id"], role_keys=["viewer"])

    async def disable(repo: SecurityRepository) -> None:
        await repo.update_user(second["id"], enabled=False)

    outcomes = await asyncio.gather(run(demote), run(disable), return_exceptions=True)
    successes = [result for result in outcomes if not isinstance(result, BaseException)]
    assert len(successes) >= 1
    async with db.session() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(UserRow)
            .join(UserRoleRow, UserRoleRow.user_id == UserRow.id)
            .where(UserRoleRow.role_key == "admin", UserRow.enabled.is_(True))
        )
    assert count == 1


async def test_user_and_role_input_boundaries(db):
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        with pytest.raises(LubanError) as first:
            await repo.create_user(
                username="-bad", display_name="bad", password=PASSWORD, role_keys=[]
            )
        assert first.value.code == ErrorCode.VALIDATION_ERROR
        with pytest.raises(LubanError) as second:
            await repo.create_user(
                username="short-password", display_name="x", password="short", role_keys=[]
            )
        assert second.value.code == ErrorCode.VALIDATION_ERROR
        with pytest.raises(LubanError) as third:
            await repo.create_user(
                username="bad-role",
                display_name="x",
                password=PASSWORD,
                role_keys=["Bad Role"],
            )
        assert third.value.code == ErrorCode.VALIDATION_ERROR
        with pytest.raises(LubanError) as fourth:
            await repo.create_role(key="Bad Role", name="bad", permissions=[])
        assert fourth.value.code == ErrorCode.VALIDATION_ERROR


def test_execution_endpoints_map_to_run_execute():
    assert required_permission("POST", "/v1/runs/run-1/resume") == "run:execute"
    assert required_permission("POST", "/v1/tools/code/test") == "run:execute"
    assert required_permission("POST", "/v1/models/smart/test") == "run:execute"
    assert required_permission("POST", "/v1/models/compare") == "run:execute"
    assert required_permission("POST", "/v1/knowledge/query") == "run:execute"
    assert required_permission("HEAD", "/v1/agents") == "config:read"


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/v1/security/users", "security:manage"),
        ("POST", "/v1/security/roles", "security:manage"),
        ("DELETE", "/v1/security/roles/custom", "security:manage"),
        ("GET", "/v1/logs", "diagnostics:read"),
        ("GET", "/v1/diagnostics/runs", "diagnostics:read"),
        ("GET", "/v1/runs/run-1", "run:read"),
        ("GET", "/v1/traces/run-1", "run:read"),
        ("GET", "/v1/approvals", "run:read"),
        ("GET", "/v1/evals", "eval:read"),
        ("POST", "/v1/agents", "config:write"),
        ("POST", "/v1/agents/demo/runs", "run:execute"),
        ("POST", "/v1/teams/cs-team/runs", "run:execute"),
        ("POST", "/v1/runs/run-1/cancel", "run:cancel"),
        ("POST", "/v1/approvals/req-1/decide", "run:approve"),
        ("POST", "/v1/runs/run-1/resume", "run:execute"),
        ("POST", "/v1/evals", "eval:execute"),
        ("PUT", "/v1/prompts/key", "config:write"),
    ],
)
def test_permission_mapping_matrix(method: str, path: str, expected: str):
    assert required_permission(method, path) == expected


async def test_update_user_rejects_mixed_invalid_role_keys(db):
    """携带 admin 的混合非法角色数组也必须整体校验，不能绕过。"""
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        user = await repo.create_user(
            username="mixed-roles",
            display_name="mixed",
            password=PASSWORD,
            role_keys=["viewer"],
        )
        with pytest.raises(LubanError) as missing_role:
            await repo.update_user(user["id"], role_keys=["admin", "missing-role"])
        assert missing_role.value.code == ErrorCode.VALIDATION_ERROR
        with pytest.raises(LubanError) as too_many:
            await repo.update_user(user["id"], role_keys=[f"role-{index}" for index in range(21)])
        assert too_many.value.code == ErrorCode.VALIDATION_ERROR
        assert await repo.get_user(user["id"]) is not None
        refreshed = await repo.get_user(user["id"])
        assert refreshed["role_keys"] == ["viewer"]


async def test_expired_session_token_rejected(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "expiring-user", role_keys=["viewer"])
        token = await login(client, "expiring-user")
        async with db.session() as session:
            row = await session.get(AuthSessionRow, token_hash(token))
            assert row is not None
            # login 默认发 8 小时会话；减 1 小时仍是未来，必须直接设为过去。
            row.expires_at = datetime.now(UTC) - timedelta(hours=1)
            await session.flush()
            await session.commit()
        expired = await client.get("/v1/agents", headers=bearer(token))
        assert expired.status_code == 401
        assert expired.json()["error"]["code"] == "AUTH_FAILED"


async def test_disabled_user_cannot_login_or_use_token(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "disabled-user", role_keys=["viewer"])
        token = await login(client, "disabled-user")
        users = (await client.get("/v1/security/users", headers=bearer(MASTER))).json()
        user_id = users[0]["id"]
        response = await client.patch(
            f"/v1/security/users/{user_id}",
            headers=bearer(MASTER),
            json={"enabled": False},
        )
        assert response.status_code == 200, response.text
        denied = await client.post(
            "/v1/auth/login",
            json={"username": "disabled-user", "password": PASSWORD},
        )
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "AUTH_FAILED"
        stale = await client.get("/v1/agents", headers=bearer(token))
        assert stale.status_code == 401


async def test_unknown_username_uses_dummy_scrypt_hash(db, registry):
    """未知用户名同样跑一遍真实 scrypt，登录耗时不再泄露账号存在性。"""
    from lubanagent.persistence.repositories.security import (
        _timing_safe_dummy_hash,
        verify_password,
    )

    dummy = _timing_safe_dummy_hash()
    assert dummy.startswith("scrypt$")
    assert verify_password("luban-timing-safe-dummy-password", dummy)
    assert not verify_password("wrong-password-123", dummy)
    async with await make_security_client(db, registry) as client:
        response = await client.post(
            "/v1/auth/login",
            json={"username": "no-such-user", "password": "wrong-password-123"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_FAILED"


async def test_auditor_is_read_only_for_diagnostics(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "audit-user", role_keys=["auditor"])
        token = await login(client, "audit-user")
        logs = await client.get("/v1/logs", headers=bearer(token))
        assert logs.status_code == 200
        runs = await client.get("/v1/diagnostics/runs", headers=bearer(token))
        assert runs.status_code == 200
        write = await client.post(
            "/v1/agents",
            headers=bearer(token),
            json={"key": "no", "name": "no", "instructions": "no", "tools": []},
        )
        assert write.status_code == 403
        assert write.json()["error"]["code"] == "PERMISSION_DENIED"
        execute = await client.post("/v1/tools/anything/test", headers=bearer(token))
        assert execute.status_code == 403
        assert execute.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_viewer_cannot_read_logs_but_traces_ok(db, registry):
    async with await make_security_client(db, registry) as client:
        await create_account(client, "view-user", role_keys=["viewer"])
        token = await login(client, "view-user")
        logs = await client.get("/v1/logs", headers=bearer(token))
        assert logs.status_code == 403
        assert logs.json()["error"]["code"] == "PERMISSION_DENIED"
        # run:read 命中权限层；具体 run 不存在由业务层 404 而非 403。
        traces = await client.get("/v1/traces/missing-run", headers=bearer(token))
        assert traces.status_code != 403


async def test_login_race_with_password_change_keeps_revoke(db, registry):
    """改密在 scrypt 验证间隙提交后，login 不得凭旧 hash 发新 token。"""
    async with await make_security_client(db, registry) as client:
        await create_account(client, "race-pw-user", role_keys=["viewer"])
        original_verify = SecurityRepository._verify

        async def racing_verify(self, password, stored_hash):
            valid = await original_verify(self, password, stored_hash)
            # 确定性模拟：验证刚通过、事务提交前，管理员改密+撤销已生效。
            await self._session.execute(
                sa.update(UserRow)
                .where(UserRow.username == "race-pw-user")
                .values(password_hash=hash_password("brand-new-password-9"))
            )
            return valid

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SecurityRepository, "_verify", racing_verify)
            response = await client.post(
                "/v1/auth/login",
                json={"username": "race-pw-user", "password": PASSWORD},
            )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_FAILED"


async def test_login_race_with_disable_keeps_revoke(db, registry):
    """禁用在 scrypt 验证间隙提交后，login 不得发新 token。"""
    async with await make_security_client(db, registry) as client:
        await create_account(client, "race-disable-user", role_keys=["viewer"])
        original_verify = SecurityRepository._verify

        async def racing_verify(self, password, stored_hash):
            valid = await original_verify(self, password, stored_hash)
            await self._session.execute(
                sa.update(UserRow)
                .where(UserRow.username == "race-disable-user")
                .values(enabled=False)
            )
            return valid

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SecurityRepository, "_verify", racing_verify)
            response = await client.post(
                "/v1/auth/login",
                json={"username": "race-disable-user", "password": PASSWORD},
            )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_FAILED"


async def test_authenticated_approval_ignores_forged_decided_by(db, registry):
    application = create_app(
        settings=make_settings(auth_enabled=True, api_key=MASTER, observability_backend="none"),
        registry=registry,
        db=db,
    )
    manager = application.state.ctx.approval_manager
    descriptor = ToolDescriptor(key="refund", name="refund", description="r", args_schema={})
    pending = await manager.create_request("run-approval", descriptor, {})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/approvals/{pending.id}/decide",
            headers=bearer(MASTER),
            json={"approved": True, "decided_by": "forger"},
        )
    assert response.status_code == 200
    assert response.json()["decided_by"] == "api-key-holder"


def test_admin_user_lock_order_statements_compile_for_mysql():
    """锁序语句必须在 MySQL 方言下编译合法：哨兵写不是目标表自读子查询。"""
    from sqlalchemy.dialects import mysql

    user_sentinel = str(
        sa.update(UserRow)
        .where(UserRow.id == "user-x")
        .values(enabled=UserRow.enabled)
        .compile(dialect=mysql.dialect())
    )
    assert "SET" in user_sentinel.upper()
    admin_sentinel = str(
        sa.update(RoleRow)
        .where(RoleRow.key == "admin")
        .values(protected=RoleRow.protected)
        .compile(dialect=mysql.dialect())
    )
    assert "SET" in admin_sentinel.upper()
    admin_scan = str(
        sa.select(UserRoleRow.user_id)
        .join(UserRow, UserRow.id == UserRoleRow.user_id)
        .where(UserRoleRow.role_key == "admin", UserRow.enabled.is_(True))
        .with_for_update()
        .compile(dialect=mysql.dialect())
    )
    assert "FOR UPDATE" in admin_scan.upper()


async def test_concurrent_three_way_admin_race_keeps_single_admin(db):
    """三方并发（停 A、停 B、降 A）独立连接互搏：终态恰一个活跃管理员。"""
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        first = await repo.create_user(
            username="three-admin-a", display_name="a", password=PASSWORD, role_keys=["admin"]
        )
        second = await repo.create_user(
            username="three-admin-b", display_name="b", password=PASSWORD, role_keys=["admin"]
        )
    database_url = str(db.engine.url)

    async def run(operation) -> None:
        independent = Database(database_url)
        try:
            async with independent.session() as session:
                await operation(SecurityRepository(session))
        finally:
            await independent.close()

    outcomes = await asyncio.gather(
        run(lambda repo: repo.update_user(first["id"], enabled=False)),
        run(lambda repo: repo.update_user(second["id"], enabled=False)),
        run(lambda repo: repo.update_user(first["id"], role_keys=["viewer"])),
        return_exceptions=True,
    )
    successes = [result for result in outcomes if not isinstance(result, BaseException)]
    assert len(successes) >= 1
    async with db.session() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(UserRow)
            .join(UserRoleRow, UserRoleRow.user_id == UserRow.id)
            .where(UserRoleRow.role_key == "admin", UserRow.enabled.is_(True))
        )
    assert count == 1


async def test_concurrent_login_and_password_change_never_keeps_stale_token(db):
    """独立连接并发 login×改密：无论交错顺序，改密后旧凭据与新 token 都失效。"""
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        user = await repo.create_user(
            username="race-login-user", display_name="r", password=PASSWORD, role_keys=["viewer"]
        )
    database_url = str(db.engine.url)

    async def do_login():
        independent = Database(database_url)
        try:
            async with independent.session() as session:
                return await SecurityRepository(session).login(
                    username="race-login-user", password=PASSWORD, session_hours=1
                )
        finally:
            await independent.close()

    async def do_change_password():
        independent = Database(database_url)
        try:
            async with independent.session() as session:
                await SecurityRepository(session).update_user(
                    user["id"], password="brand-new-password-9"
                )
        finally:
            await independent.close()

    outcomes = await asyncio.gather(do_login(), do_change_password(), return_exceptions=True)
    async with db.session() as session:
        repo = SecurityRepository(session)
        # 旧密码必拒。
        with pytest.raises(LubanError) as rejected:
            await repo.login(username="race-login-user", password=PASSWORD, session_hours=1)
        assert rejected.value.code == ErrorCode.AUTH_FAILED
        # 若 login 在改密前抢到锁拿到了 token，改密提交时必须已撤销它。
        login_result = outcomes[0]
        if not isinstance(login_result, BaseException):
            stale_token = login_result[0]
            assert await SecurityRepository(session).user_by_token(stale_token) is None


async def test_teams_run_extra_router_maps_to_run_execute(db, registry, monkeypatch):
    """真实 extra router（app/api/teams.py）：团队执行走 run:execute 权限闸门。"""
    from types import SimpleNamespace

    import app.api.teams as teams_module

    class FakeRunner:
        def __init__(self, db, runtime, registry):
            pass

        async def run(self, team, text, thread_id=None):
            return SimpleNamespace(
                status="completed", output="ok", run_id="run-team", thread_id=thread_id
            )

    monkeypatch.setattr(teams_module, "TeamRunner", FakeRunner)
    await seed_defaults(db)
    application = create_app(
        settings=make_settings(auth_enabled=True, api_key=MASTER, observability_backend="none"),
        registry=registry,
        db=db,
        extra_routers=(teams_module.router,),
    )
    application.state.cs_team = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        anonymous = await client.post("/v1/teams/cs-team/runs", json={"input": "hi"})
        assert anonymous.status_code == 401
        assert anonymous.json()["error"]["code"] == "AUTH_FAILED"
        await create_account(client, "team-viewer", role_keys=["viewer"])
        viewer_token = await login(client, "team-viewer")
        forbidden = await client.post(
            "/v1/teams/cs-team/runs", headers=bearer(viewer_token), json={"input": "hi"}
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "PERMISSION_DENIED"
        response = await client.post(
            "/v1/teams/cs-team/runs", headers=bearer(MASTER), json={"input": "hi"}
        )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


async def test_role_shrink_takes_effect_on_log_read_immediately(db, registry):
    """同一 token 的 GET /v1/logs 权限随角色变更实时生效。"""
    async with await make_security_client(db, registry) as client:
        await create_account(client, "admin-live", role_keys=["admin"])
        await create_account(client, "live-operator", role_keys=["operator"])
        admin_token = await login(client, "admin-live")
        operator_token = await login(client, "live-operator")
        assert (await client.get("/v1/logs", headers=bearer(operator_token))).status_code == 200
        shrink = await client.patch(
            "/v1/security/roles/operator",
            headers=bearer(admin_token),
            json={"name": "操作员", "permissions": ["config:read"]},
        )
        assert shrink.status_code == 200
        denied = await client.get("/v1/logs", headers=bearer(operator_token))
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
