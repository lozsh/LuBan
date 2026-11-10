"""账号登录与用户/角色管理；后端仍是唯一权限裁决点。"""

import logging
import re
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from lubanagent.api.auth import bearer_token, get_principal
from lubanagent.api.deps import CtxDep
from lubanagent.core import ErrorCode, LubanError
from lubanagent.core.log_context import update_log_context
from lubanagent.extension import Principal
from lubanagent.persistence import SecurityRepository

audit_logger = logging.getLogger("lubanagent.api.security")
_USERNAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_ROLE_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PERMISSION = re.compile(r"^[a-z]+:[a-z_]+$")

router = APIRouter(prefix="/v1", tags=["security"])
managed_router = APIRouter(dependencies=[Depends(get_principal)], tags=["security"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    # 与创建用户一致：12..256，缺省先在输入层拒绝不可能合法的密码。
    password: str = Field(min_length=12, max_length=256)


class LogoutResponse(BaseModel):
    logged_out: bool


class UserPublic(BaseModel):
    id: str
    username: str
    display_name: str
    enabled: bool
    bootstrap: bool = False
    role_keys: list[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserCreateRequest(BaseModel):
    username: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$"
    )
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)
    role_keys: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True


class UserPatchRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    role_keys: list[str] | None = Field(default=None, max_length=20)
    password: str | None = Field(default=None, min_length=12, max_length=256)


class RolePublic(BaseModel):
    key: str
    name: str
    protected: bool = False
    permissions: list[str]


class RoleUpsertRequest(BaseModel):
    key: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    name: str = Field(min_length=1, max_length=128)
    permissions: list[str] = Field(default_factory=list, max_length=20)


class SessionUserOut(UserPublic):
    subject: str
    roles: list[str]
    permissions: list[str]
    auth_enabled: bool
    is_bootstrap: bool


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_at: datetime
    user: SessionUserOut


class PermissionPublic(BaseModel):
    key: str
    name: str


class RoleDeleteResponse(BaseModel):
    deleted: bool


def _validate_role_keys(role_keys: list[str] | None) -> None:
    for key in role_keys or []:
        if not _ROLE_KEY.fullmatch(key):
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"角色 key 不合法: {key}")


def _validate_permissions(permissions: list[str]) -> None:
    for key in permissions:
        if not _PERMISSION.fullmatch(key):
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"权限 key 不合法: {key}")


def _user_public(user: dict[str, Any]) -> UserPublic:
    return UserPublic(
        id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        enabled=user["enabled"],
        bootstrap=user.get("bootstrap", False),
        role_keys=list(user.get("role_keys", [])),
        created_at=user.get("created_at"),
        updated_at=user.get("updated_at"),
    )


def _session_user(
    user: dict[str, Any], permissions: list[str], auth_enabled: bool
) -> dict[str, Any]:
    return SessionUserOut(
        subject=user.get("subject", f"user:{user.get('id', '')}"),
        id=user.get("id", ""),
        username=user["username"],
        display_name=user["display_name"],
        enabled=user.get("enabled", True),
        bootstrap=user.get("bootstrap", False),
        role_keys=list(user.get("roles", user.get("role_keys", []))),
        roles=list(user.get("roles", user.get("role_keys", []))),
        permissions=list(permissions),
        auth_enabled=auth_enabled,
        is_bootstrap=bool(user.get("is_bootstrap", user.get("bootstrap", False))),
    ).model_dump(mode="json")


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, ctx: CtxDep) -> dict[str, Any]:
    client_host = request.client.host if request.client else "unknown"
    # 双桶限流：host 桶防单点刷接口，username 桶防分布式撞库同一账号；
    # 都只取真实连接来源，不信任 X-Forwarded-For。
    if not await ctx.rate_limiter.allow(f"login:host:{client_host}"):
        raise LubanError(ErrorCode.RATE_LIMITED, "登录尝试过多", retryable=True)
    if not await ctx.rate_limiter.allow(f"login:user:{body.username}"):
        raise LubanError(ErrorCode.RATE_LIMITED, "登录尝试过多", retryable=True)
    if not _USERNAME.fullmatch(body.username):
        raise LubanError(ErrorCode.VALIDATION_ERROR, "用户名格式不合法")
    async with ctx.db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        try:
            token, expires_at, user = await repo.login(
                username=body.username,
                password=body.password,
                session_hours=ctx.settings.auth_session_hours,
            )
        except LubanError:
            update_log_context(subject=f"username:{body.username}")
            audit_logger.warning(
                "security.login failed actor=anonymous action=login "
                "target=username:%s client=%s",
                body.username,
                client_host,
            )
            raise
    update_log_context(subject=f"user:{user['id']}", user_id=user["id"])
    audit_logger.info(
        "security.login success actor=%s action=login target=%s client=%s",
        user["id"],
        user["username"],
        client_host,
    )
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_at=expires_at,
        user=_session_user(user, user["permissions"], True),
    ).model_dump(mode="json")


@router.get("/auth/me", response_model=SessionUserOut, dependencies=[Depends(get_principal)])
async def me(principal: PrincipalDep, ctx: CtxDep) -> dict[str, Any]:
    user = principal.claims.get("user")
    if not isinstance(user, dict):
        raise LubanError(ErrorCode.AUTH_FAILED, "当前凭据不是账号会话")
    permissions = principal.claims.get("permissions", [])
    return _session_user(user, permissions, ctx.settings.auth_enabled)


@router.post("/auth/logout", response_model=LogoutResponse, dependencies=[Depends(get_principal)])
async def logout(
    principal: PrincipalDep, request: Request, ctx: CtxDep
) -> LogoutResponse:
    token = bearer_token({key.lower(): value for key, value in request.headers.items()})
    revoked = False
    if token:
        async with ctx.db.session() as session:
            revoked = await SecurityRepository(session).revoke_token(token)
    audit_logger.info(
        "security.logout actor=%s action=logout target=session revoked=%s",
        principal.subject,
        revoked,
    )
    return LogoutResponse(logged_out=True)


@managed_router.get("/security/users", response_model=list[UserPublic])
async def list_users(ctx: CtxDep, principal: PrincipalDep) -> list[UserPublic]:
    async with ctx.db.session() as session:
        users = [
            _user_public(user) for user in await SecurityRepository(session).list_users()
        ]
    audit_logger.info(
        "security.users.list actor=%s action=view target=users count=%s",
        principal.subject,
        len(users),
    )
    return users


@managed_router.post("/security/users", response_model=UserPublic)
async def create_user(
    body: UserCreateRequest, ctx: CtxDep, principal: PrincipalDep
) -> UserPublic:
    _validate_role_keys(body.role_keys)
    async with ctx.db.session() as session:
        user = await SecurityRepository(session).create_user(
            username=body.username,
            display_name=body.display_name,
            password=body.password,
            role_keys=body.role_keys,
            enabled=body.enabled,
        )
    audit_logger.info(
        "security.users.create actor=%s action=create target=%s",
        principal.subject,
        user["id"],
    )
    return _user_public(user)


@managed_router.patch("/security/users/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: str,
    body: UserPatchRequest,
    ctx: CtxDep,
    principal: PrincipalDep,
) -> UserPublic:
    _validate_role_keys(body.role_keys)
    async with ctx.db.session() as session:
        user = await SecurityRepository(session).update_user(
            user_id,
            display_name=body.display_name,
            enabled=body.enabled,
            role_keys=body.role_keys,
            password=body.password,
        )
    audit_logger.info(
        "security.users.update actor=%s action=update target=%s",
        principal.subject,
        user_id,
    )
    return _user_public(user)


@managed_router.get("/security/roles", response_model=list[RolePublic])
async def list_roles(ctx: CtxDep, principal: PrincipalDep) -> list[RolePublic]:
    async with ctx.db.session() as session:
        roles = [
            RolePublic(**role) for role in await SecurityRepository(session).list_roles()
        ]
    audit_logger.info(
        "security.roles.list actor=%s action=view target=roles count=%s",
        principal.subject,
        len(roles),
    )
    return roles


@managed_router.post("/security/roles", response_model=RolePublic)
async def create_role(
    body: RoleUpsertRequest, ctx: CtxDep, principal: PrincipalDep
) -> RolePublic:
    if not body.key:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "缺少角色 key")
    _validate_permissions(body.permissions)
    async with ctx.db.session() as session:
        role = RolePublic(
            **await SecurityRepository(session).create_role(
                key=body.key, name=body.name, permissions=body.permissions
            )
        )
    audit_logger.info(
        "security.roles.create actor=%s action=create target=%s",
        principal.subject,
        role.key,
    )
    return role


@managed_router.patch("/security/roles/{key}", response_model=RolePublic)
async def update_role(
    key: str, body: RoleUpsertRequest, ctx: CtxDep, principal: PrincipalDep
) -> RolePublic:
    _validate_permissions(body.permissions)
    async with ctx.db.session() as session:
        role = RolePublic(
            **await SecurityRepository(session).update_role(
                key, name=body.name, permissions=body.permissions
            )
        )
    audit_logger.info(
        "security.roles.update actor=%s action=update target=%s",
        principal.subject,
        key,
    )
    return role


@managed_router.delete("/security/roles/{key}", response_model=RoleDeleteResponse)
async def delete_role(key: str, ctx: CtxDep, principal: PrincipalDep) -> RoleDeleteResponse:
    async with ctx.db.session() as session:
        await SecurityRepository(session).delete_role(key)
    audit_logger.info(
        "security.roles.delete actor=%s action=delete target=%s", principal.subject, key
    )
    return RoleDeleteResponse(deleted=True)


@managed_router.get("/security/permissions", response_model=list[PermissionPublic])
async def list_permissions(
    ctx: CtxDep, principal: PrincipalDep
) -> list[PermissionPublic]:
    async with ctx.db.session() as session:
        permissions = await SecurityRepository(session).list_permissions()
    audit_logger.info(
        "security.permissions.list actor=%s action=view target=permissions count=%s",
        principal.subject,
        len(permissions),
    )
    return [PermissionPublic(**item) for item in permissions]


router.include_router(managed_router)
