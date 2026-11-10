"""平台排查：分页会话、执行树和已落库程序日志。"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from lubanagent.api.deps import CtxDep
from lubanagent.api.schemas import TraceOut
from lubanagent.core import ErrorCode, LubanError, Run, RunStatus
from lubanagent.core.redact import redact_value
from lubanagent.persistence import RunRepository, TraceRepository
from lubanagent.persistence.repositories.log import ApplicationLogRepository

router = APIRouter(tags=["diagnostics"])


class ApplicationLogOut(BaseModel):
    id: str
    created_at: datetime
    level: str
    logger: str
    message: str
    exception: str | None = None
    pathname: str
    lineno: int
    request_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    parent_run_id: str | None = None
    root_run_id: str | None = None
    subject: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class LogPage(BaseModel):
    items: list[ApplicationLogOut]
    total: int
    limit: int
    offset: int
    dropped_count: int = 0
    write_failures: int = 0


class DiagnosticRunPage(BaseModel):
    items: list[Run]
    total: int
    limit: int
    offset: int


class DiagnosticDetail(BaseModel):
    run: Run
    runs: list[Run]
    traces: list[TraceOut]
    log_count: int


def _display_run(run: Run) -> Run:
    """排查界面是展示边界，不能把旧 Run 的凭证输入从数据库原样返回。"""
    return run.model_copy(
        update={
            "input": redact_value(run.input),
            "output": redact_value(run.output),
            "error": redact_value(run.error),
        }
    )


@router.get("/logs", response_model=LogPage)
async def list_logs(
    ctx: CtxDep,
    request: Request,
    session_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=64),
    trace_id: str | None = Query(default=None, max_length=128),
    request_id: str | None = Query(default=None, max_length=128),
    root_run_id: str | None = Query(default=None, max_length=64),
    level: str | None = Query(default=None, max_length=16),
    logger: str | None = Query(default=None, max_length=255),
    q: str | None = Query(default=None, max_length=1000),
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LogPage:
    if from_time:
        from_time = from_time.replace(tzinfo=from_time.tzinfo or UTC).astimezone(UTC)
    if to_time:
        to_time = to_time.replace(tzinfo=to_time.tzinfo or UTC).astimezone(UTC)
    if from_time and to_time and from_time > to_time:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "开始时间不能晚于结束时间")
    writer = getattr(request.app.state, "log_writer", None)
    if writer is not None:
        await writer.flush()
    async with ctx.db.session() as session:
        items, total = await ApplicationLogRepository(session).list_logs(
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            request_id=request_id,
            root_run_id=root_run_id,
            level=level,
            logger=logger,
            q=q,
            from_time=from_time,
            to_time=to_time,
            limit=limit,
            offset=offset,
        )
    return LogPage(
        items=[ApplicationLogOut.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
        dropped_count=writer.dropped_count if writer else 0,
        write_failures=writer.write_failures if writer else 0,
    )


@router.get("/diagnostics/runs", response_model=DiagnosticRunPage)
async def diagnostic_runs(
    ctx: CtxDep,
    session_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=64),
    trace_id: str | None = Query(default=None, max_length=128),
    user_id: str | None = Query(default=None, max_length=128),
    status: RunStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DiagnosticRunPage:
    async with ctx.db.session() as session:
        items, total = await RunRepository(session).diagnostic_page(
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            user_id=user_id,
            status=status.value if status else None,
            limit=limit,
            offset=offset,
        )
    return DiagnosticRunPage(
        items=[_display_run(run) for run in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/diagnostics/runs/{identifier}", response_model=DiagnosticDetail)
async def diagnostic_detail(identifier: str, ctx: CtxDep, request: Request) -> DiagnosticDetail:
    writer = getattr(request.app.state, "log_writer", None)
    if writer is not None:
        await writer.flush()
    async with ctx.db.session() as session:
        traces = TraceRepository(session)
        trace = await traces.get_trace(identifier)
        if trace is None:
            raise LubanError(ErrorCode.RUN_NOT_FOUND, "执行记录不存在")
        repository = RunRepository(session)
        run = await repository.get_run(trace.run_id)
        if run is None:
            raise LubanError(ErrorCode.RUN_NOT_FOUND, "执行记录不存在")
        root = await repository.root_run(run.id)
        tree = await repository.run_tree(run.id)
        tree_traces = []
        for item in tree:
            header = await traces.get_trace_by_run(item.id)
            if header is not None:
                tree_traces.append(TraceOut(trace=header, events=await traces.list_events(item.id)))
        _, count = await ApplicationLogRepository(session).list_logs(
            root_run_id=root.id if root else run.id,
            limit=1,
        )
    return DiagnosticDetail(
        run=_display_run(run),
        runs=[_display_run(item) for item in tree],
        traces=tree_traces,
        log_count=count,
    )
