"""审批持久化仓储：ApprovalStore 协议的 DB 实现（tool 层经 Protocol 解耦，不反向依赖）。"""

from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import ApprovalRequest
from lubanagent.persistence.models.approval import ApprovalRow
from lubanagent.persistence.session import Database

_TERMINAL_STATUSES = ("approved", "rejected", "expired")


def _to_domain(row: ApprovalRow) -> ApprovalRequest:
    return ApprovalRequest(
        id=row.id,
        run_id=row.run_id,
        tool_key=row.tool_key,
        args=dict(row.args or {}),
        created_at=row.created_at,
        status=row.status,  # type: ignore[arg-type]
        decided_by=row.decided_by,
        decided_at=row.decided_at,
    )


class ApprovalRepository:
    """会话级 CRUD：approval 的增查与条件终结（返回 core/tool 领域对象）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, request: ApprovalRequest) -> None:
        self._session.add(
            ApprovalRow(
                id=request.id,
                run_id=request.run_id,
                tool_key=request.tool_key,
                args=request.args,
                status=request.status,
                decided_by=request.decided_by,
                decided_at=request.decided_at,
                # 显式落应用侧 created_at（微秒精度）：server_default 的 DB now() 在
                # SQLite 下仅秒级精度，同秒记录会退化到随机 uuid4 tiebreak，排序不可复现
                created_at=request.created_at,
            )
        )
        await self._session.flush()

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        row = await self._session.get(ApprovalRow, approval_id)
        return _to_domain(row) if row is not None else None

    async def list_pending(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        stmt = sa.select(ApprovalRow).where(ApprovalRow.status == "pending")
        if run_id is not None:
            stmt = stmt.where(ApprovalRow.run_id == run_id)
        stmt = stmt.order_by(ApprovalRow.created_at, ApprovalRow.id)
        rows = (await self._session.scalars(stmt)).all()
        return [_to_domain(row) for row in rows]

    async def list_all(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        stmt = sa.select(ApprovalRow)
        if run_id is not None:
            stmt = stmt.where(ApprovalRow.run_id == run_id)
        stmt = stmt.order_by(ApprovalRow.created_at, ApprovalRow.id)
        rows = (await self._session.scalars(stmt)).all()
        return [_to_domain(row) for row in rows]

    async def try_resolve(
        self,
        approval_id: str,
        status: Literal["approved", "rejected", "expired"],
        *,
        decided_by: str | None = None,
    ) -> bool:
        """条件更新 pending→terminal；受影响行数=0 表示不存在或已被并发决定。"""
        result = await self._session.execute(
            sa.update(ApprovalRow)
            .where(ApprovalRow.id == approval_id, ApprovalRow.status == "pending")
            .values(status=status, decided_by=decided_by, decided_at=datetime.now(UTC))
        )
        await self._session.flush()
        return result.rowcount == 1


class DbApprovalStore:
    """ApprovalStore 协议实现：每个操作独立短 session（同 TraceWriter 风格）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, request: ApprovalRequest) -> None:
        async with self._db.session() as session:
            await ApprovalRepository(session).create(request)

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        async with self._db.session() as session:
            return await ApprovalRepository(session).get(approval_id)

    async def list_pending(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        async with self._db.session() as session:
            return await ApprovalRepository(session).list_pending(run_id=run_id)

    async def list_all(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        async with self._db.session() as session:
            return await ApprovalRepository(session).list_all(run_id=run_id)

    async def try_resolve(
        self,
        approval_id: str,
        status: Literal["approved", "rejected", "expired"],
        *,
        decided_by: str | None = None,
    ) -> bool:
        async with self._db.session() as session:
            return await ApprovalRepository(session).try_resolve(
                approval_id, status, decided_by=decided_by
            )


__all__ = ["ApprovalRepository", "DbApprovalStore"]
