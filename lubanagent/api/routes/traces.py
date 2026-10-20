"""Trace 路由：按 run_id 查 Trace 头 + 事件流。"""

from fastapi import APIRouter

from lubanagent.api.deps import CtxDep
from lubanagent.api.schemas import TraceOut
from lubanagent.core import ErrorCode, LubanError
from lubanagent.persistence import TraceRepository

router = APIRouter(tags=["traces"])


@router.get("/traces/{run_id}", response_model=TraceOut)
async def get_trace(run_id: str, ctx: CtxDep) -> TraceOut:
    async with ctx.db.session() as session:
        repo = TraceRepository(session)
        trace = await repo.get_trace_by_run(run_id)
        if trace is None:
            raise LubanError(
                ErrorCode.RUN_NOT_FOUND,
                f"Trace 不存在: run {run_id}",
                details={"run_id": run_id},
            )
        events = await repo.list_events(run_id)
    return TraceOut(trace=trace, events=events)
