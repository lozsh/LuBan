"""程序日志读写：稳定排序、分页、时间过滤与保留期清理。"""

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.persistence.models.log import ApplicationLogRow


class ApplicationLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_batch(self, records: list[dict[str, Any]]) -> None:
        if records:
            await self._session.execute(sa.insert(ApplicationLogRow), records)

    async def list_logs(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
        level: str | None = None,
        logger: str | None = None,
        q: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        root_run_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = []
        for key, value in (
            ("session_id", session_id),
            ("run_id", run_id),
            ("trace_id", trace_id),
            ("request_id", request_id),
            ("level", level.upper() if level else None),
            ("root_run_id", root_run_id),
        ):
            if value is not None:
                conditions.append(getattr(ApplicationLogRow, key) == value)
        if logger:
            conditions.append(ApplicationLogRow.logger.contains(logger, autoescape=True))
        if q:
            conditions.append(
                sa.or_(
                    ApplicationLogRow.message.contains(q, autoescape=True),
                    ApplicationLogRow.exception.contains(q, autoescape=True),
                )
            )
        if from_time:
            conditions.append(
                ApplicationLogRow.created_at
                >= from_time.replace(tzinfo=from_time.tzinfo or UTC).astimezone(UTC)
            )
        if to_time:
            conditions.append(
                ApplicationLogRow.created_at
                <= to_time.replace(tzinfo=to_time.tzinfo or UTC).astimezone(UTC)
            )
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(ApplicationLogRow).where(*conditions)
        )
        rows = (
            await self._session.scalars(
                sa.select(ApplicationLogRow)
                .where(*conditions)
                .order_by(ApplicationLogRow.created_at.desc(), ApplicationLogRow.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [self._to_dict(row) for row in rows], int(total or 0)

    async def delete_before(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            sa.delete(ApplicationLogRow).where(ApplicationLogRow.created_at < cutoff)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _to_dict(row: ApplicationLogRow) -> dict[str, Any]:
        values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        # SQLite and MySQL return UTC storage timestamps without a tzinfo; make
        # the API's timezone explicit so browsers do not interpret them locally.
        created = values["created_at"]
        values["created_at"] = created.replace(tzinfo=created.tzinfo or UTC).astimezone(UTC)
        return values
