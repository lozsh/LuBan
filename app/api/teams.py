"""客服团队自定义路由（用户领地业务路由，架构 §4.1）。

演示业务自定义路由用法：POST /v1/teams/cs-team/runs 触发分流团队。
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from lubanagent.api.auth import get_principal
from lubanagent.api.deps import CtxDep
from lubanagent.multiagent import Team, TeamRunner

router = APIRouter(prefix="/v1/teams", tags=["teams"], dependencies=[Depends(get_principal)])


class TeamRunRequest(BaseModel):
    input: str
    thread_id: str | None = None


class TeamRunResponse(BaseModel):
    status: str
    output: Any
    run_id: str
    thread_id: str


def _get_team(request: Request) -> Team:
    """从 app.state 取 Team（app/main.py 装配期挂载），缺省现场构建。"""
    team = getattr(request.app.state, "cs_team", None)
    if team is not None:
        return team
    from app.multiagents import build_cs_team

    return build_cs_team()


@router.post("/cs-team/runs", response_model=TeamRunResponse)
async def run_cs_team(body: TeamRunRequest, request: Request, ctx: CtxDep) -> TeamRunResponse:
    """触发客服分流团队：supervisor 决策→成员委派→汇总输出。"""
    team = _get_team(request)
    runner = TeamRunner(db=ctx.db, runtime=ctx.runtime, registry=ctx.registry)
    thread_id = body.thread_id or uuid.uuid4().hex
    result = await runner.run(team, body.input, thread_id=thread_id)
    return TeamRunResponse(
        status=result.status,
        output=result.output,
        run_id=result.run_id,
        thread_id=result.thread_id,
    )


__all__ = ["router"]
