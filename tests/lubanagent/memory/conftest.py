"""memory 测试夹具：tmp_path 隔离的 SQLite，import 模型后建全表。"""

import pytest

from lubanagent.persistence import Base, Database
from lubanagent.persistence.models.memory import (  # noqa: F401  # 注册三张记忆表
    LongTermMemoryRow,
    SessionMessageRow,
    SessionSummaryRow,
)


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()
