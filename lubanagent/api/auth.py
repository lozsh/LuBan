"""接入鉴权与平台权限裁决：API Key / 自定义 Provider / 数据库账号统一入口。"""

import hmac
import time
from collections import deque
from typing import Annotated, Protocol

from fastapi import Depends, Request

from lubanagent.api.deps import CtxDep
from lubanagent.core import ErrorCode, LubanError
from lubanagent.core.log_context import update_log_context
from lubanagent.extension import AuthProvider, AuthRequest, Principal
from lubanagent.persistence import SecurityRepository


def constant_time_eq(left: str, right: str) -> bool:
    """字节级常数时间比较；非 ASCII 输入稳定返回 False 而非 TypeError。"""
    try:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
    except UnicodeEncodeError:
        return False


class ApiKeyAuthProvider:
    """框架内置 AuthProvider：Authorization: Bearer <key> 与配置的 api_key 比对。"""

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    async def verify(self, request: AuthRequest) -> Principal:
        if not self._api_key:
            # 纯数据库模式（AUTH_ENABLED + AUTH_DATABASE_ENABLED，无 API_KEY）：
            # 缺失凭据是客户端问题，按 401 AUTH_FAILED 拒绝，不应 500。
            raise LubanError(ErrorCode.AUTH_FAILED, "缺少凭据")
        token = bearer_token(request.headers)
        if token is None or not constant_time_eq(token, self._api_key):
            raise LubanError(ErrorCode.AUTH_FAILED, "API Key 缺失或不匹配")
        return Principal(
            subject="api-key-holder",
            scopes=["*"],
            claims={"auth": "api_key", "permissions": ["*"]},
        )


def bearer_token(headers: dict[str, str]) -> str | None:
    """从小写化 headers 中取 Bearer token；非 Bearer/缺失返回 None。"""
    value = headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _bootstrap_principal() -> Principal:
    """Master API Key 是显式 bootstrap 管理员，始终拥有全部权限。"""
    user = {
        "subject": "api-key-holder",
        "username": "api-key-holder",
        "display_name": "Bootstrap Admin",
        "roles": ["admin"],
        "permissions": ["*"],
        "is_bootstrap": True,
    }
    return Principal(
        subject="api-key-holder",
        scopes=["*"],
        claims={"auth": "api_key", "permissions": ["*"], "user": user},
    )


def _anonymous_principal() -> Principal:
    user = {
        "subject": "anonymous",
        "username": "anonymous",
        "display_name": "Development User",
        "roles": ["anonymous"],
        "permissions": ["*"],
        "is_bootstrap": False,
    }
    return Principal(
        subject="anonymous",
        scopes=["*"],
        claims={"permissions": ["*"], "user": user},
    )


def _database_principal(user: dict) -> Principal:
    permissions = list(dict.fromkeys(user.get("permissions", [])))
    session_user = {
        key: value
        for key, value in user.items()
        if key not in {"password_hash", "permissions"}
    }
    session_user.update(
        {
            "subject": f"user:{user['id']}",
            "username": user["username"],
            "display_name": user["display_name"],
            "roles": list(user.get("role_keys", [])),
            "permissions": permissions,
            "is_bootstrap": bool(user.get("bootstrap", False)),
        }
    )
    return Principal(
        subject=f"user:{user['id']}",
        scopes=permissions,
        claims={
            "auth": "database",
            "permissions": permissions,
            "user": session_user,
        },
    )


def _external_principal(principal: Principal) -> Principal:
    """自定义 Provider 的 scopes 是唯一可信权限来源；空 scopes 保持只读兼容。"""
    permissions = (
        ["*"]
        if "*" in principal.scopes
        else list(dict.fromkeys(principal.scopes)) or ["config:read"]
    )
    roles = principal.claims.get("roles", [])
    user = {
        "subject": principal.subject,
        "username": principal.claims.get("username", principal.subject),
        "display_name": principal.claims.get("display_name", principal.subject),
        "roles": list(roles) if isinstance(roles, list) else [],
        "permissions": permissions,
        "is_bootstrap": False,
    }
    return principal.model_copy(
        update={
            "scopes": permissions,
            "claims": {
                **principal.claims,
                "permissions": permissions,
                "user": user,
            },
        }
    )


class RateLimiter(Protocol):
    async def allow(self, key: str) -> bool: ...


class InMemoryRateLimiter:
    """进程内滑动窗口：每主体每分钟 N 次，闲置桶回收且新桶容量有界。"""

    def __init__(self, per_minute: int, *, max_keys: int = 4096) -> None:
        if per_minute <= 0 or max_keys <= 0:
            raise ValueError("限流次数和桶容量必须大于零")
        self._per_minute = per_minute
        self._max_keys = max_keys
        self._window_seconds = 60.0
        self._hits: dict[str, deque[float]] = {}

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        for bucket_key, bucket in list(self._hits.items()):
            while bucket and now - bucket[0] >= self._window_seconds:
                bucket.popleft()
            if not bucket:
                del self._hits[bucket_key]
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self._max_keys:
                return False
            hits = deque()
            self._hits[key] = hits
        if len(hits) >= self._per_minute:
            return False
        hits.append(now)
        return True


async def get_principal(request: Request, ctx: CtxDep) -> Principal:
    """鉴权依赖：auth_enabled=False → anonymous；registry.get_auth() 优先于内置 API Key。

    Request → AuthRequest 适配（headers/query_params 小写化）；通过后过速率限制
    （按已验证主体计数，关闭鉴权时所有请求共用匿名桶），超限 → RATE_LIMITED(429)。
    """
    path = request.url.path
    if path == "/v1/auth/login":
        return Principal(subject="anonymous")
    auth_request = AuthRequest(
        headers={key.lower(): value for key, value in request.headers.items()},
        query_params={key.lower(): value for key, value in request.query_params.items()},
    )
    if not ctx.settings.auth_enabled:
        principal = _anonymous_principal()
    else:
        custom_provider = ctx.registry.get_auth()
        if custom_provider is not None:
            # 既有契约：自定义 AuthProvider 优先，可完整接管默认 API Key。
            provider: AuthProvider = ctx.registry.get_auth()  # type: ignore[assignment]
            principal = _external_principal(await provider.verify(auth_request))
        elif (
            (token := bearer_token(auth_request.headers))
            and ctx.settings.api_key
            and constant_time_eq(token, ctx.settings.api_key)
        ):
            principal = _bootstrap_principal()
        elif token:
            async with ctx.db.session() as session:
                user = await SecurityRepository(session).user_by_token(token)
            if user is None:
                raise LubanError(ErrorCode.AUTH_FAILED, "账号会话缺失或已失效")
            principal = _database_principal(user)
        else:
            provider: AuthProvider = ApiKeyAuthProvider(ctx.settings.api_key)
            principal = await provider.verify(auth_request)
    update_log_context(subject=principal.subject)
    rate_key = principal.subject if ctx.settings.auth_enabled else "anonymous"
    if not await ctx.rate_limiter.allow(rate_key):
        raise LubanError(ErrorCode.RATE_LIMITED, "请求超出速率限制", retryable=True)
    request.state.principal = principal
    if path.startswith("/v1/auth/"):
        return principal
    required = required_permission(request.method, path)
    if required is not None and not has_permission(principal, required):
        raise LubanError(
            ErrorCode.PERMISSION_DENIED,
            f"缺少权限: {required}",
            details={"required_permission": required},
        )
    return principal


def has_permission(principal: Principal, permission: str) -> bool:
    permissions = principal.claims.get("permissions")
    if not isinstance(permissions, list):
        permissions = principal.scopes
    return "*" in permissions or permission in permissions


def require_permission(permission: str):
    async def dependency(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if not has_permission(principal, permission):
            raise LubanError(
                ErrorCode.PERMISSION_DENIED,
                f"缺少权限: {permission}",
                details={"required_permission": permission},
            )
        return principal

    return dependency


def required_permission(method: str, path: str) -> str | None:
    """已有路由统一映射；未知写路径/extra router 默认 config:write。"""
    normalized_method = "GET" if method.upper() in {"GET", "HEAD"} else method.upper()
    if path.startswith("/v1/auth/"):
        return None
    if path.startswith("/v1/security/"):
        return "security:manage"
    if path.startswith(("/v1/logs", "/v1/diagnostics/")):
        return "diagnostics:read"
    if normalized_method == "GET":
        if path.startswith(("/v1/runs", "/v1/traces", "/v1/approvals")):
            return "run:read"
        if path.startswith("/v1/evals"):
            return "eval:read"
        return "config:read"
    if path.startswith("/v1/agents") and path.endswith("/runs"):
        return "run:execute"
    if path.startswith("/v1/teams") and path.endswith("/runs"):
        # 团队执行与 Agent 执行同级（extra router 也要 run:execute）。
        return "run:execute"
    if path.startswith("/v1/runs") and path.endswith("/cancel"):
        return "run:cancel"
    if path.startswith("/v1/approvals") and path.endswith("/decide"):
        return "run:approve"
    if path.endswith(("/resume", "/test", "/compare")) or path.endswith("/knowledge/query"):
        return "run:execute"
    if path.startswith("/v1/evals"):
        return "eval:execute"
    return "config:write"
