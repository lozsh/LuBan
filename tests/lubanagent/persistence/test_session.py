"""Database.session()：正常退出 commit、异常 rollback、engine/close 语义。"""

import pytest
from sqlalchemy import select, text

from lubanagent.core import AgentDefinition
from lubanagent.persistence import Database
from lubanagent.persistence.models import AgentRow
from lubanagent.persistence.repositories import AgentRepository


async def test_session_commit_on_success(db: Database) -> None:
    async with db.session() as session:
        await AgentRepository(session).create_agent(AgentDefinition(key="demo", name="demo"))

    async with db.session() as session:
        row = await session.scalar(select(AgentRow).where(AgentRow.key == "demo"))
        assert row is not None


async def test_session_rollback_on_exception(db: Database) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with db.session() as session:
            await AgentRepository(session).create_agent(AgentDefinition(key="demo", name="demo"))
            raise RuntimeError("boom")

    async with db.session() as session:
        row = await session.scalar(select(AgentRow).where(AgentRow.key == "demo"))
        assert row is None


async def test_engine_property_and_close(db: Database) -> None:
    async with db.engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
    await db.close()
