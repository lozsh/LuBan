"""会话记忆：session_messages 表读写，history 取最新 window_size 条转 langchain 消息。"""

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy import delete, func, select

from lubanagent.core import ErrorCode, LubanError
from lubanagent.persistence import Database
from lubanagent.persistence.models.memory import SessionMessageRow


def row_to_message(row: SessionMessageRow) -> BaseMessage:
    """role → langchain 消息类型；tool 消息 v1 以空串占位 tool_call_id（回放待 Run Trace 重建）。"""
    if row.role == "user":
        return HumanMessage(content=row.content)
    if row.role == "assistant":
        return AIMessage(content=row.content)
    if row.role == "system":
        return SystemMessage(content=row.content)
    if row.role == "tool":
        return ToolMessage(content=row.content, tool_call_id="")
    raise LubanError(
        ErrorCode.INTERNAL_ERROR,
        f"未知会话消息角色: {row.role}",
        details={"message_id": row.id, "role": row.role},
    )


class SessionMemory:
    """会话记忆的读写入口；跨轮保上下文（run1 写入 → run2 读回）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(
        self, session_id: str, role: str, content: str, *, run_id: str | None = None
    ) -> None:
        async with self._db.session() as session:
            session.add(
                SessionMessageRow(
                    session_id=session_id, run_id=run_id, role=role, content=content
                )
            )

    async def history(self, session_id: str, *, window_size: int) -> list[BaseMessage]:
        """最新 window_size 条，按时间升序返回。"""
        async with self._db.session() as session:
            rows = await _fetch_window(session, session_id, window_size=window_size)
            return [row_to_message(row) for row in rows]

    async def count(self, session_id: str) -> int:
        async with self._db.session() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(SessionMessageRow)
                .where(SessionMessageRow.session_id == session_id)
            )
            return int(total or 0)

    async def clear(self, session_id: str) -> int:
        """删除该会话全部消息，返回删除条数。"""
        async with self._db.session() as session:
            result = await session.execute(
                delete(SessionMessageRow).where(SessionMessageRow.session_id == session_id)
            )
            return int(result.rowcount or 0)


async def _fetch_window(session, session_id: str, *, window_size: int, after_id: int = 0):
    """取 id > after_id 的最新 window_size 条，升序返回（供 SummaryMemory 复用）。"""
    stmt = (
        select(SessionMessageRow)
        .where(
            SessionMessageRow.session_id == session_id,
            SessionMessageRow.id > after_id,
        )
        .order_by(SessionMessageRow.id.desc())
        .limit(window_size)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    rows.reverse()
    return rows
