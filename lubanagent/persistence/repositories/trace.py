"""Trace 仓储：Trace 头与事件流，返回 core 领域对象。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import ErrorCode, LubanError, RunStatus, Trace, TraceEvent, TraceEventType
from lubanagent.persistence.models import TraceEventRow, TraceRow


class TraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_trace(self, trace: Trace) -> None:
        self._session.add(
            TraceRow(
                id=trace.id,
                run_id=trace.run_id,
                status=trace.status.value,
                started_at=trace.started_at,
                ended_at=trace.ended_at,
            )
        )
        await self._session.flush()

    async def get_trace_by_run(self, run_id: str) -> Trace | None:
        row = await self._session.scalar(select(TraceRow).where(TraceRow.run_id == run_id))
        if row is None:
            return None
        return Trace(
            id=row.id,
            run_id=row.run_id,
            status=RunStatus(row.status),
            started_at=row.started_at,
            ended_at=row.ended_at,
        )

    async def add_event(self, event: TraceEvent) -> None:
        trace_row = await self._session.scalar(
            select(TraceRow).where(TraceRow.run_id == event.run_id)
        )
        if trace_row is None:
            raise LubanError(ErrorCode.RUN_NOT_FOUND, f"Trace 不存在: run {event.run_id}")
        self._session.add(
            TraceEventRow(
                id=event.id,
                trace_id=trace_row.id,
                run_id=event.run_id,
                step_index=event.step_index,
                type=event.type.value,
                payload=event.payload,
                created_at=event.created_at,
            )
        )
        await self._session.flush()

    async def list_events(self, run_id: str) -> list[TraceEvent]:
        rows = (
            await self._session.scalars(
                select(TraceEventRow)
                .where(TraceEventRow.run_id == run_id)
                .order_by(TraceEventRow.created_at, TraceEventRow.id)
            )
        ).all()
        return [
            TraceEvent(
                id=row.id,
                run_id=row.run_id,
                type=TraceEventType(row.type),
                step_index=row.step_index,
                payload=row.payload,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def finish_trace(self, run_id: str, status: RunStatus, ended_at: datetime) -> None:
        row = await self._session.scalar(select(TraceRow).where(TraceRow.run_id == run_id))
        if row is None:
            raise LubanError(ErrorCode.RUN_NOT_FOUND, f"Trace 不存在: run {run_id}")
        row.status = status.value
        row.ended_at = ended_at
        await self._session.flush()
