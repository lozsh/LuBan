"""api：REST/SSE 接入层——鉴权限流、依赖注入、错误映射与 /v1 路由。"""

from lubanagent.api import schemas
from lubanagent.api.auth import (
    ApiKeyAuthProvider,
    InMemoryRateLimiter,
    RateLimiter,
    get_principal,
)
from lubanagent.api.deps import (
    AppContext,
    get_approval_manager,
    get_ctx,
    get_db,
    get_registry,
    get_runtime,
)
from lubanagent.api.errors import ERROR_HTTP_STATUS, register_error_handlers
from lubanagent.api.routes import api_router

__all__ = [
    "ERROR_HTTP_STATUS",
    "ApiKeyAuthProvider",
    "AppContext",
    "InMemoryRateLimiter",
    "RateLimiter",
    "api_router",
    "get_approval_manager",
    "get_ctx",
    "get_db",
    "get_principal",
    "get_registry",
    "get_runtime",
    "register_error_handlers",
    "schemas",
]
