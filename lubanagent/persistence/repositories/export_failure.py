"""外发失败留痕仓储：export_failures 的记录、待重试查询、尝试计数与删除。"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.persistence.models import ExportFailureRow

DEFAULT_MAX_ATTEMPTS = 3
"""重试尝试上限：retry_count 达到该值即放弃（保留行供运维查询，不再自动重试）。"""


class ExportFailureRepository:
    """观测外发失败留痕的完整闭环：
    record 落表 → list_pending 取待重试 → mark_retry_failed 计失败尝试 /
    delete 清成功回放 → list_given_up 查已放弃（可观测途径）。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        backend: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        error: str,
    ) -> None:
        self._session.add(
            ExportFailureRow(
                backend=backend,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                error=error,
            )
        )
        await self._session.flush()

    async def list_pending(
        self,
        backend: str | None = None,
        limit: int = 100,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> list[dict[str, Any]]:
        """仍可重试（retry_count < max_attempts）的失败记录，按 id 升序。"""
        stmt = select(ExportFailureRow).order_by(ExportFailureRow.id).limit(limit)
        stmt = stmt.where(ExportFailureRow.retry_count < max_attempts)
        if backend is not None:
            stmt = stmt.where(ExportFailureRow.backend == backend)
        rows = (await self._session.scalars(stmt)).all()
        return [self._to_dict(row) for row in rows]

    async def list_given_up(
        self,
        backend: str | None = None,
        limit: int = 100,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> list[dict[str, Any]]:
        """已放弃（尝试次数达上限）的失败记录：保留行不再自动重试，供运维排查。"""
        stmt = select(ExportFailureRow).order_by(ExportFailureRow.id.desc()).limit(limit)
        stmt = stmt.where(ExportFailureRow.retry_count >= max_attempts)
        if backend is not None:
            stmt = stmt.where(ExportFailureRow.backend == backend)
        rows = (await self._session.scalars(stmt)).all()
        return [self._to_dict(row) for row in rows]

    async def mark_retry_failed(self, ids: list[int]) -> None:
        """一次重试失败：retry_count + 1，retried_at 记本次尝试时间。"""
        if not ids:
            return
        now = datetime.now(UTC)
        rows = (
            await self._session.scalars(
                select(ExportFailureRow).where(ExportFailureRow.id.in_(ids))
            )
        ).all()
        for row in rows:
            row.retry_count += 1
            row.retried_at = now
        await self._session.flush()

    async def delete(self, ids: list[int]) -> None:
        """回放成功后删除留痕（消费闭环终点）。"""
        if not ids:
            return
        await self._session.execute(
            delete(ExportFailureRow).where(ExportFailureRow.id.in_(ids))
        )
        await self._session.flush()

    @staticmethod
    def _to_dict(row: ExportFailureRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "backend": row.backend,
            "run_id": row.run_id,
            "event_type": row.event_type,
            "payload": row.payload,
            "error": row.error,
            "created_at": row.created_at,
            "retried_at": row.retried_at,
            "retry_count": row.retry_count,
        }
