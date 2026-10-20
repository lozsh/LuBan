"""knowledge 测试夹具：tmp_path 隔离 SQLite（含 knowledge_chunks 表）+ KnowledgeStore。"""

import pytest

from lubanagent.knowledge import FakeEmbedding, KnowledgeStore
from lubanagent.persistence import Base, Database
from lubanagent.persistence.models.knowledge import KnowledgeChunkRow  # noqa: F401  # 注册表


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


@pytest.fixture
def store(db: Database) -> KnowledgeStore:
    return KnowledgeStore(db)


@pytest.fixture
def embedder() -> FakeEmbedding:
    return FakeEmbedding()
