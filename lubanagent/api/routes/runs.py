"""Run 路由：SSE 流式创建、查询与取消。"""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from lubanagent.api.deps import CtxDep
from lubanagent.api.schemas import RunCreateRequest
from lubanagent.core import ErrorCode, LubanError, Run, TraceEvent
from lubanagent.persistence import RunRepository
from lubanagent.runtime import RunRequest, sse_stream

router = APIRouter(tags=["runs"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _to_messages(history: list[dict[str, str]] | None) -> list[BaseMessage] | None:
    """OpenAI 风格 history → BaseMessage；未知 role 按 user 处理。"""
    if history is None:
        return None
    messages: list[BaseMessage] = []
    for item in history:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role == "assistant":
            messages.append(AIMessage(content=content))
        elif role == "system":
            messages.append(SystemMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


@router.post("/agents/{key}/runs")
async def create_run(key: str, body: RunCreateRequest, ctx: CtxDep):
    request = RunRequest(
        agent_key=key,
        user_input=body.input,
        session_id=body.session_id,
        user_id=body.user_id,
        version=body.version,
        variables=body.variables,
        history=_to_messages(body.history),
        instructions_override=body.instructions_override,
    )
    stream = ctx.runtime.stream(request)
    # 预热首事件：定义加载/可运行性失败（404/422）在流开始前抛出，走统一错误映射
    first: TraceEvent | None = None
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        pass

    async def events() -> AsyncIterator[TraceEvent]:
        if first is not None:
            yield first
        async for event in stream:
            yield event

    return StreamingResponse(
        sse_stream(events()), media_type="text/event-stream", headers=_SSE_HEADERS
    )


@router.get("/runs", response_model=list[Run])
async def list_runs(
    ctx: CtxDep,
    agent_key: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[Run]:
    async with ctx.db.session() as session:
        return await RunRepository(session).list_runs(
            agent_key, session_id=session_id, user_id=user_id, limit=limit
        )


@router.get("/runs/{run_id}", response_model=Run)
async def get_run(run_id: str, ctx: CtxDep) -> Run:
    async with ctx.db.session() as session:
        run = await RunRepository(session).get_run(run_id)
    if run is None:
        raise LubanError(
            ErrorCode.RUN_NOT_FOUND, f"Run 不存在: {run_id}", details={"run_id": run_id}
        )
    return run


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, ctx: CtxDep) -> dict[str, bool]:
    async with ctx.db.session() as session:
        run = await RunRepository(session).get_run(run_id)
    if run is None:
        raise LubanError(
            ErrorCode.RUN_NOT_FOUND, f"Run 不存在: {run_id}", details={"run_id": run_id}
        )
    cancelled = await ctx.runtime.cancel(run_id)
    return {"cancelled": cancelled}


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, ctx: CtxDep):
    """从最近 checkpoint 续跑非终态 Run（SSE，帧格式同创建 Run）。

    错误在流开始前抛出（Run/checkpoint 缺失 → 404 RUN_NOT_FOUND；
    终态/活跃冲突/子 Run → 422 VALIDATION_ERROR；未启用 checkpoint →
    500 CONFIG_ERROR），走统一错误映射。
    """
    stream = ctx.runtime.stream_resume(run_id)
    # 预热首事件：校验失败（404/422/500）在流开始前抛出，走统一错误映射
    first: TraceEvent | None = None
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        pass

    async def events() -> AsyncIterator[TraceEvent]:
        if first is not None:
            yield first
        async for event in stream:
            yield event

    return StreamingResponse(
        sse_stream(events()), media_type="text/event-stream", headers=_SSE_HEADERS
    )
