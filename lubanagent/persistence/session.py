"""异步会话工厂：Database.session() 正常退出 commit、异常 rollback。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from lubanagent.config import Settings


class Database:
    """AsyncEngine 持有者与会话入口；sqlite 时放开线程检查。"""

    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self._engine = create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        await self._engine.dispose()


def create_database(settings: Settings) -> Database:
    """从 settings.database_url 建 Database。"""
    return Database(settings.database_url)
