"""长期记忆：agent_key + user_key 维度的跨会话关键事实，显式写入、按最近 N 条注入。"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.config.settings import get_settings
from lubanagent.persistence import Database
from lubanagent.persistence.models.memory import LongTermMemoryRow


class LongTermMemory:
    """跨会话事实的显式读写；与 session_id 无关，同一 agent+user 任意会话可 recall。

    容量控制：每 agent+user 条数上限（默认取 Settings.long_term_memory_capacity=1000），
    写入超限即淘汰同维度最旧条目——长期运行表不再无限增长；存量数据不超上限时零感知。
    """

    def __init__(self, db: Database, *, capacity: int | None = None) -> None:
        # capacity=None 走 Settings（env LONG_TERM_MEMORY_CAPACITY 可调）
        if capacity is None:
            capacity = get_settings().long_term_memory_capacity
        self._capacity = max(1, int(capacity))
        self._db = db

    async def remember(self, agent_key: str, user_key: str, fact: str) -> int:
        """写入一条事实，返回 id；同 agent+user 超过容量上限时淘汰最旧。"""
        async with self._db.session() as session:
            row = LongTermMemoryRow(agent_key=agent_key, user_key=user_key, fact=fact)
            session.add(row)
            await session.flush()
            await self._evict_overflow(session, agent_key, user_key)
            return row.id

    async def _evict_overflow(
        self, session: AsyncSession, agent_key: str, user_key: str
    ) -> int:
        """删除同 agent+user 维度超出容量的最旧条目（created_at、id 正序），返回删除条数。"""
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(LongTermMemoryRow)
                .where(
                    LongTermMemoryRow.agent_key == agent_key,
                    LongTermMemoryRow.user_key == user_key,
                )
            )
            or 0
        )
        overflow = count - self._capacity
        if overflow <= 0:
            return 0
        oldest_ids = (
            select(LongTermMemoryRow.id)
            .where(
                LongTermMemoryRow.agent_key == agent_key,
                LongTermMemoryRow.user_key == user_key,
            )
            .order_by(LongTermMemoryRow.created_at.asc(), LongTermMemoryRow.id.asc())
            .limit(overflow)
        )
        result = await session.execute(
            delete(LongTermMemoryRow).where(LongTermMemoryRow.id.in_(oldest_ids))
        )
        return result.rowcount or 0

    async def recall(self, agent_key: str, user_key: str, *, limit: int = 20) -> list[str]:
        """取最近 limit 条（created_at 倒序、id 倒序决胜），再反转为正序返回。"""
        async with self._db.session() as session:
            rows = list(
                (
                    await session.execute(
                        select(LongTermMemoryRow)
                        .where(
                            LongTermMemoryRow.agent_key == agent_key,
                            LongTermMemoryRow.user_key == user_key,
                        )
                        .order_by(
                            LongTermMemoryRow.created_at.desc(),
                            LongTermMemoryRow.id.desc(),
                        )
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            rows.reverse()
            return [row.fact for row in rows]

    async def forget(self, agent_key: str, user_key: str, fact_id: int) -> bool:
        """按 id 删除（限定 agent+user 防越权），返回是否删到。"""
        async with self._db.session() as session:
            result = await session.execute(
                delete(LongTermMemoryRow).where(
                    LongTermMemoryRow.id == fact_id,
                    LongTermMemoryRow.agent_key == agent_key,
                    LongTermMemoryRow.user_key == user_key,
                )
            )
            return bool(result.rowcount)


def format_facts_for_prompt(facts: list[str]) -> str:
    """格式化为 system prompt 尾部片段；空列表 → ""。"""
    if not facts:
        return ""
    lines = "\n".join(f"- {fact}" for fact in facts)
    return f"已知用户信息：\n{lines}"
