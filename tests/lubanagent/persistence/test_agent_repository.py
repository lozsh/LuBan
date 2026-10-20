"""AgentRepository：定义落库、版本快照不可变、状态更新。"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import AgentDefinition, AgentStatus, ErrorCode, LubanError
from lubanagent.persistence.models import AgentVersionRow
from lubanagent.persistence.repositories import AgentRepository


def make_definition(key: str = "demo", version: int = 1) -> AgentDefinition:
    return AgentDefinition(key=key, name=f"Demo {key}", description="演示", version=version)


async def test_create_and_get_agent(session: AsyncSession) -> None:
    repo = AgentRepository(session)
    definition = make_definition()
    await repo.create_agent(definition)

    loaded = await repo.get_agent("demo")
    assert loaded is not None
    assert loaded.key == "demo"
    assert loaded.name == "Demo demo"
    assert loaded.version == 1
    assert loaded.status == AgentStatus.DRAFT
    assert loaded.model_dump(mode="json") == definition.model_dump(mode="json")


async def test_get_agent_missing_returns_none(session: AsyncSession) -> None:
    assert await AgentRepository(session).get_agent("nope") is None


async def test_create_version_and_get_version(session: AsyncSession) -> None:
    repo = AgentRepository(session)
    await repo.create_agent(make_definition(version=1))
    await repo.create_version("demo", make_definition(version=2))

    # 版本化字段断言：agent_versions.version 落库为 1 和 2
    versions = (
        (await session.scalars(select(AgentVersionRow).order_by(AgentVersionRow.version))).all()
    )
    assert [row.version for row in versions] == [1, 2]

    snapshot = await repo.get_version("demo", 1)
    assert snapshot is not None
    assert snapshot.agent_key == "demo"
    assert snapshot.version == 1
    assert snapshot.definition.version == 1
    assert snapshot.published_at is None

    # 新版本推进 current_version，get_agent 返回 v2
    current = await repo.get_agent("demo")
    assert current is not None and current.version == 2


async def test_create_version_duplicate_raises_validation_error(
    session: AsyncSession,
) -> None:
    repo = AgentRepository(session)
    await repo.create_agent(make_definition(version=1))
    with pytest.raises(LubanError) as exc_info:
        await repo.create_version("demo", make_definition(version=1))
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_create_version_missing_agent_raises(session: AsyncSession) -> None:
    with pytest.raises(LubanError) as exc_info:
        await AgentRepository(session).create_version("ghost", make_definition(key="ghost"))
    assert exc_info.value.code == ErrorCode.AGENT_NOT_FOUND


async def test_list_agents(session: AsyncSession) -> None:
    repo = AgentRepository(session)
    await repo.create_agent(make_definition(key="a"))
    await repo.create_agent(make_definition(key="b"))
    keys = [d.key for d in await repo.list_agents()]
    assert keys == ["a", "b"]


async def test_update_status(session: AsyncSession) -> None:
    repo = AgentRepository(session)
    await repo.create_agent(make_definition())
    await repo.update_status("demo", AgentStatus.PUBLISHED)
    loaded = await repo.get_agent("demo")
    assert loaded is not None and loaded.status == AgentStatus.PUBLISHED
