"""/v1 路由聚合：统一前缀与鉴权依赖。"""

from fastapi import APIRouter, Depends

from lubanagent.api.auth import get_principal
from lubanagent.api.routes import (
    agents,
    approvals,
    avatars,
    evals,
    graphs,
    knowledge,
    models,
    prompts,
    runs,
    skills,
    tools,
    traces,
)

api_router = APIRouter(prefix="/v1", dependencies=[Depends(get_principal)])
api_router.include_router(agents.router)
api_router.include_router(runs.router)
api_router.include_router(models.router)
api_router.include_router(tools.router)
api_router.include_router(traces.router)
api_router.include_router(approvals.router)
api_router.include_router(evals.router)
api_router.include_router(skills.router)
api_router.include_router(prompts.router)
api_router.include_router(knowledge.router)
api_router.include_router(graphs.router)
api_router.include_router(avatars.router)

__all__ = ["api_router"]
