"""摘要记忆：消息数超窗口时把窗口外最旧消息压缩成摘要；摘要在前缀、窗口消息 append-only。"""

from typing import Protocol, runtime_checkable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from sqlalchemy import func, select

from lubanagent.persistence import Database
from lubanagent.persistence.models.memory import SessionMessageRow, SessionSummaryRow

from .session import SessionMemory, _fetch_window, row_to_message

_SUMMARY_PROMPT = """\
请把以下对话压缩为简明摘要，必须保留：关键事实、已做出的决定、未完成的待办事项。
{previous_block}
待压缩对话：
{dialogue}
只输出摘要文本本身，不要加任何前后缀。"""


@runtime_checkable
class Summarizer(Protocol):
    """摘要器协议：previous 为既有摘要（首次为 None），返回新摘要全文。"""

    async def summarize(self, messages: list[BaseMessage], *, previous: str | None) -> str: ...


class FakeSummarizer:
    """确定性拼接截断，测试用；记录调用参数便于断言。"""

    def __init__(self, *, max_chars: int = 200) -> None:
        self._max_chars = max_chars
        self.calls: list[tuple[list[BaseMessage], str | None]] = []

    async def summarize(self, messages: list[BaseMessage], *, previous: str | None) -> str:
        self.calls.append((list(messages), previous))
        parts: list[str] = []
        if previous:
            parts.append(previous)
        parts.extend(f"{m.type}:{m.content}" for m in messages)
        return " | ".join(parts)[: self._max_chars]


class LlmSummarizer:
    """经 brain cheap 槽位模型的摘要器；模型由调用方造好注入。"""

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    async def summarize(self, messages: list[BaseMessage], *, previous: str | None) -> str:
        previous_block = f"此前摘要（需并入）：\n{previous}\n" if previous else ""
        dialogue = "\n".join(f"{m.type}: {m.content}" for m in messages)
        prompt = _SUMMARY_PROMPT.format(previous_block=previous_block, dialogue=dialogue)
        response = await self._model.ainvoke([HumanMessage(content=prompt)])
        return str(response.content).strip()


class SummaryMemory:
    """压缩策略：消息数 > window_size 时触发；已压缩消息不删除，按 upto_message_id 跳过。"""

    def __init__(self, db: Database, *, summarizer: Summarizer) -> None:
        self._db = db
        self._summarizer = summarizer
        self._session_memory = SessionMemory(db)

    async def get_summary(self, session_id: str) -> str | None:
        async with self._db.session() as session:
            row = await self._get_row(session, session_id)
            return row.summary if row is not None else None

    async def maybe_compress(self, session_id: str, *, window_size: int) -> bool:
        """未压缩消息数 > window_size → 窗口外最旧消息连同既有摘要交 summarizer，upsert 返回 True。

        已压缩消息（id <= upto_message_id）跳过，不参与计数与重复压缩。
        """
        async with self._db.session() as session:
            summary_row = await self._get_row(session, session_id)
            previous = summary_row.summary if summary_row is not None else None
            old_upto = summary_row.upto_message_id if summary_row is not None else 0
            old_id = summary_row.id if summary_row is not None else None
            total = await session.scalar(
                select(func.count())
                .select_from(SessionMessageRow)
                .where(
                    SessionMessageRow.session_id == session_id,
                    SessionMessageRow.id > old_upto,
                )
            )
            total = int(total or 0)
            if total <= window_size:
                return False
            overflow = total - window_size
            rows = list(
                (
                    await session.execute(
                        select(SessionMessageRow)
                        .where(
                            SessionMessageRow.session_id == session_id,
                            SessionMessageRow.id > old_upto,
                        )
                        .order_by(SessionMessageRow.id)
                        .limit(overflow)
                    )
                )
                .scalars()
                .all()
            )
            upto_message_id = rows[-1].id
            messages = [row_to_message(row) for row in rows]

        # 摘要可能调 LLM，放在库事务外
        new_summary = await self._summarizer.summarize(messages, previous=previous)

        async with self._db.session() as session:
            if old_id is not None:
                row = await session.get(SessionSummaryRow, old_id)
                row.summary = new_summary
                row.upto_message_id = upto_message_id
            else:
                session.add(
                    SessionSummaryRow(
                        session_id=session_id,
                        summary=new_summary,
                        upto_message_id=upto_message_id,
                    )
                )
        return True

    async def context_prefix(self, session_id: str, *, window_size: int) -> list[BaseMessage]:
        """有摘要 → [SystemMessage("此前对话摘要：...")] + upto 之后窗口内消息；无 → 窗口内消息。"""
        async with self._db.session() as session:
            row = await self._get_row(session, session_id)
            if row is not None:
                window = await _fetch_window(
                    session, session_id, window_size=window_size, after_id=row.upto_message_id
                )
                return [
                    SystemMessage(content=f"此前对话摘要：{row.summary}"),
                    *[row_to_message(r) for r in window],
                ]
        return await self._session_memory.history(session_id, window_size=window_size)

    @staticmethod
    async def _get_row(session, session_id: str) -> SessionSummaryRow | None:
        return await session.scalar(
            select(SessionSummaryRow).where(SessionSummaryRow.session_id == session_id)
        )
