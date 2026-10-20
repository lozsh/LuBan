"""接入鉴权与速率限制：默认 API Key AuthProvider + 内存滑动窗口限流。"""

import hmac
import time
from collections import deque
from typing import Protocol

from fastapi import Request

from lubanagent.api.deps import CtxDep
from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import AuthProvider, AuthRequest, Principal


class ApiKeyAuthProvider:
    """框架内置 AuthProvider：Authorization: Bearer <key> 与配置的 api_key 比对。"""

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    async def verify(self, request: AuthRequest) -> Principal:
        if not self._api_key:
            # 开启鉴权却未配置 key：拒绝所有（CONFIG_ERROR 语义）。
            # 该组合已在 create_app 装配期 fail fast 拦下（R6），此处仅为
            # 绕过工厂直接构造 ApiKeyAuthProvider(None) 时的兜底。
            raise LubanError(ErrorCode.CONFIG_ERROR, "服务端已开启鉴权但未配置 API Key")
        token = bearer_token(request.headers)
        if token is None or not hmac.compare_digest(token, self._api_key):
            raise LubanError(ErrorCode.AUTH_FAILED, "API Key 缺失或不匹配")
        return Principal(subject="api-key-holder", claims={"auth": "api_key"})


def bearer_token(headers: dict[str, str]) -> str | None:
    """从小写化 headers 中取 Bearer token；非 Bearer/缺失返回 None。"""
    value = headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


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
    auth_request = AuthRequest(
        headers={key.lower(): value for key, value in request.headers.items()},
        query_params={key.lower(): value for key, value in request.query_params.items()},
    )
    if not ctx.settings.auth_enabled:
        principal = Principal(subject="anonymous")
    else:
        provider: AuthProvider = ctx.registry.get_auth() or ApiKeyAuthProvider(ctx.settings.api_key)
        principal = await provider.verify(auth_request)
    rate_key = principal.subject if ctx.settings.auth_enabled else "anonymous"
    if not await ctx.rate_limiter.allow(rate_key):
        raise LubanError(ErrorCode.RATE_LIMITED, "请求超出速率限制", retryable=True)
    return principal
