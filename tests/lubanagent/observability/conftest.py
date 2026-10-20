"""observability 测试夹具：tmp_path 隔离的 SQLite + aiosqlite。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.persistence import Base, Database


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


@pytest.fixture
async def session(db: Database) -> AsyncSession:
    async with db.session() as session:
        yield session
