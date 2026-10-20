"""FastAPI 依赖注入：AppContext 挂 app.state.ctx，路由经 Depends 取用。"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from lubanagent.config import Settings
from lubanagent.extension import Registry
from lubanagent.persistence import Database
from lubanagent.runtime import AgentRuntime
from lubanagent.tool import ApprovalManager

if TYPE_CHECKING:
    from lubanagent.api.auth import RateLimiter


@dataclass
class AppContext:
    """应用装配产物；测试可整体替换或经 dependency_overrides 注入替身。"""

    settings: Settings
    db: Database
    registry: Registry
    runtime: AgentRuntime
    approval_manager: ApprovalManager
    rate_limiter: "RateLimiter"


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


CtxDep = Annotated[AppContext, Depends(get_ctx)]


def get_db(ctx: CtxDep) -> Database:
    return ctx.db


def get_runtime(ctx: CtxDep) -> AgentRuntime:
    return ctx.runtime


def get_registry(ctx: CtxDep) -> Registry:
    return ctx.registry


def get_approval_manager(ctx: CtxDep) -> ApprovalManager:
    return ctx.approval_manager


ApprovalManagerDep = Annotated[ApprovalManager, Depends(get_approval_manager)]
