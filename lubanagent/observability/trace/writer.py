"""TraceWriter：Run/Trace/事件/用量/费用同步落内部 DB，评测/成本/工作台的唯一数据源。"""

import uuid
from datetime import UTC, datetime

from lubanagent.core import (
    ErrorCode,
    LubanError,
    Run,
    RunStatus,
    Trace,
    TraceEvent,
    TraceEventType,
)
from lubanagent.core.redact import redact_value
from lubanagent.persistence import Database, RunRepository, TraceRepository

_TERMINAL_EVENT: dict[RunStatus, TraceEventType] = {
    RunStatus.COMPLETED: TraceEventType.RUN_COMPLETED,
    RunStatus.FAILED: TraceEventType.RUN_FAILED,
    RunStatus.CANCELLED: TraceEventType.RUN_CANCELLED,
}


class TraceWriter:
    """内部 DB 双写主笔（架构 §6.1）：调用方 await，不排队，不依赖外部后端。"""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._trace_ids: dict[str, str] = {}  # run_id -> trace_id 缓存，查库为兜底

    async def start_run(self, run: Run) -> Trace:
        """落 runs 行 + traces 行 + RUN_STARTED 事件。"""
        trace = Trace(
            id=f"trace-{run.id}",
            run_id=run.id,
            status=run.status,
            started_at=run.started_at or datetime.now(UTC),
        )
        async with self._db.session() as session:
            await RunRepository(session).create_run(run)
            trace_repo = TraceRepository(session)
            await trace_repo.create_trace(trace)
            await trace_repo.add_event(self._event(run.id, TraceEventType.RUN_STARTED))
        self._trace_ids[run.id] = trace.id
        return trace

    async def resume_run(self, run: Run) -> None:
        """续跑登记（缺口 #2）：runs 状态置 RUNNING（行已存在，不重建）+ RUN_RESUMED 事件。

        trace 沿用原 trace 行（事件按 run_id 追加，同一 trace 连续，无第二条 RUN_STARTED）。
        """
        async with self._db.session() as session:
            await RunRepository(session).update_run(run)
            trace_repo = TraceRepository(session)
            trace = await trace_repo.get_trace_by_run(run.id)
            if trace is None:
                raise LubanError(ErrorCode.RUN_NOT_FOUND, f"Trace 不存在: run {run.id}")
            self._trace_ids[run.id] = trace.id
            await trace_repo.add_event(
                self._event(run.id, TraceEventType.RUN_RESUMED, {"resumed": True})
            )

    async def emit(self, event: TraceEvent) -> None:
        """落 trace_events 行（trace_id 经 run_id 查：缓存优先，查库兜底）。

        入库 payload 为脱敏审计副本：source of truth 保留可排查业务文本，
        秘密命名字段/凭证形态字符串整体隐藏；安全重放遇脱敏记录须显式拒绝（H10）。
        """
        event = TraceEvent(
            id=event.id,
            run_id=event.run_id,
            type=event.type,
            step_index=event.step_index,
            payload=redact_value(event.payload),
            created_at=event.created_at,
        )
        async with self._db.session() as session:
            trace_repo = TraceRepository(session)
            if event.run_id not in self._trace_ids:
                trace = await trace_repo.get_trace_by_run(event.run_id)
                if trace is None:
                    raise LubanError(
                        ErrorCode.RUN_NOT_FOUND, f"Trace 不存在: run {event.run_id}"
                    )
                self._trace_ids[event.run_id] = trace.id
            await trace_repo.add_event(event)

    async def record_model_usage(
        self,
        run_id: str,
        *,
        step_index: int | None,
        model_key: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """落一条用量明细；None = 模型未回报 usage，账本存 NULL 显式未知（H10）。"""
        async with self._db.session() as session:
            await RunRepository(session).add_token_usage(
                run_id, step_index, model_key, input_tokens, output_tokens
            )

    async def record_cost(
        self, run_id: str, *, model_key: str, cost: float, currency: str = "CNY"
    ) -> None:
        async with self._db.session() as session:
            await RunRepository(session).add_cost_record(
                run_id, model_key, cost, currency
            )

    async def finish_run(self, run: Run) -> None:
        """更新 runs 终态字段 + traces 终态 + 终态事件（按 run.status 选）。"""
        try:
            event_type = _TERMINAL_EVENT[run.status]
        except KeyError:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"finish_run 要求终态 Run，实际: {run.status.value}",
            ) from None
        ended_at = run.ended_at or datetime.now(UTC)
        async with self._db.session() as session:
            await RunRepository(session).update_run(run)
            trace_repo = TraceRepository(session)
            await trace_repo.finish_trace(run.id, run.status, ended_at)
            payload: dict[str, object] = {}
            if run.end_reason is not None:
                payload["end_reason"] = run.end_reason.value
            if run.error is not None:
                payload["error"] = run.error
            await trace_repo.add_event(
                self._event(run.id, event_type, payload=payload)
            )

    @staticmethod
    def _event(
        run_id: str, event_type: TraceEventType, payload: dict[str, object] | None = None
    ) -> TraceEvent:
        return TraceEvent(
            id=f"evt-{uuid.uuid4().hex}",
            run_id=run_id,
            type=event_type,
            payload=dict(payload or {}),
            created_at=datetime.now(UTC),
        )
