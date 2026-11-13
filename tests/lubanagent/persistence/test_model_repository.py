"""ModelRepository：模型注册、大脑槽位目录与并发删除保护。"""

import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    ErrorCode,
    LubanError,
    ModelCapability,
    ModelDefinition,
)
from lubanagent.persistence import Base, Database
from lubanagent.persistence.models import BrainProfileAliasRow, BrainProfileRow
from lubanagent.persistence.repositories import AgentRepository, ModelRepository


def make_model(key: str, *, enabled: bool = True) -> ModelDefinition:
    return ModelDefinition(
        key=key,
        provider="fake",
        model_name=f"model-{key}",
        factory="fake",
        capability=ModelCapability(tool_calling=True, context_window=128000),
        params={"temperature": 0},
        enabled=enabled,
    )


async def test_upsert_and_get_model(session: AsyncSession) -> None:
    repo = ModelRepository(session)
    await repo.upsert_model(make_model("m1"))

    loaded = await repo.get_model("m1")
    assert loaded is not None
    assert loaded.key == "m1"
    assert loaded.model_name == "model-m1"
    assert loaded.capability.tool_calling is True
    assert loaded.capability.context_window == 128000

    # 再 upsert 同 key = 更新
    updated = make_model("m1")
    updated.params = {"temperature": 1}
    await repo.upsert_model(updated)
    reloaded = await repo.get_model("m1")
    assert reloaded is not None and reloaded.params == {"temperature": 1}
    assert await repo.get_model("ghost") is None


async def test_list_models_enabled_only(session: AsyncSession) -> None:
    repo = ModelRepository(session)
    await repo.upsert_model(make_model("on"))
    await repo.upsert_model(make_model("off", enabled=False))

    assert [m.key for m in await repo.list_models()] == ["on"]
    assert [m.key for m in await repo.list_models(enabled_only=False)] == ["on", "off"]


async def test_brain_mapping_insert_update_and_switch(session: AsyncSession) -> None:
    repo = ModelRepository(session)
    await repo.upsert_model(make_model("m1"))
    await repo.upsert_model(make_model("m2"))

    # 不存在则插
    await repo.set_brain_mapping("dev", BrainProfile.SMART, "m1")
    mapped = await repo.get_brain_mapping("dev", BrainProfile.SMART)
    assert mapped is not None and mapped.key == "m1"

    # 存在则改：改指向后 get_brain_mapping 返回新定义
    await repo.set_brain_mapping("dev", BrainProfile.SMART, "m2")
    mapped = await repo.get_brain_mapping("dev", BrainProfile.SMART)
    assert mapped is not None
    assert mapped.key == "m2"
    assert mapped.model_name == "model-m2"

    # env/profile 维度隔离
    assert await repo.get_brain_mapping("prod", BrainProfile.SMART) is None
    assert await repo.get_brain_mapping("dev", BrainProfile.FAST) is None


async def test_brain_mapping_to_missing_model_returns_none(session: AsyncSession) -> None:
    repo = ModelRepository(session)
    await repo.set_brain_mapping("dev", BrainProfile.CHEAP, "ghost")
    assert await repo.get_brain_mapping("dev", BrainProfile.CHEAP) is None


@pytest.fixture(params=["sqlite", "mysql"])
async def catalog_db(request, tmp_path):
    if request.param == "mysql":
        url = os.environ.get("BRAIN_PROFILE_TEST_MYSQL_URL")
        if not url:
            pytest.skip("isolated MySQL test database not configured")
    else:
        url = f"sqlite+aiosqlite:///{tmp_path}/concurrent-brain.db"
    database = Database(url)
    try:
        async with database.engine.begin() as connection:
            if request.param == "mysql":
                # URL 仅用于专用隔离测试库；不使用应用 DATABASE_URL。
                await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            repo = ModelRepository(session)
            await repo.seed_brain_profiles(["smart", "cheap"])
            await repo.create_brain_profile(BrainProfile("reasoning"), name="Reasoning")
        yield database
    finally:
        await database.close()


@pytest.mark.parametrize("save_first", [True, False])
async def test_brain_slot_delete_and_agent_save_are_serialized(catalog_db, save_first):
    db = catalog_db
    locked = asyncio.Event()
    release = asyncio.Event()
    contender_started = asyncio.Event()
    definition = AgentDefinition(key="race-agent", name="Race", brain={"profile": "reasoning"})

    async def save():
        try:
            async with db.session() as session:
                await AgentRepository(session).create_agent(definition)
                if save_first:
                    locked.set()
                    await release.wait()
            return "saved"
        except LubanError as error:
            return error.code.value

    async def remove():
        try:
            async with db.session() as session:
                await ModelRepository(session).delete_brain_profile(BrainProfile("reasoning"))
                if not save_first:
                    locked.set()
                    await release.wait()
            return "deleted"
        except LubanError as error:
            return error.code.value

    async def contender():
        contender_started.set()
        return await (remove() if save_first else save())

    owner = asyncio.create_task(save() if save_first else remove())
    await asyncio.wait_for(locked.wait(), timeout=10)
    waiting = asyncio.create_task(contender())
    try:
        await asyncio.wait_for(contender_started.wait(), timeout=10)
        await asyncio.sleep(0.1)
        assert not waiting.done(), "second writer must wait for the slot transaction"
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(owner, waiting), timeout=15)
    if save_first:
        assert results == ["saved", ErrorCode.BRAIN_PROFILE_IN_USE.value]
    else:
        assert results == ["deleted", ErrorCode.BRAIN_PROFILE_NOT_FOUND.value]
    async with db.session() as session:
        slot = await session.scalar(
            select(BrainProfileRow).where(BrainProfileRow.key == "reasoning")
        )
        agent = await AgentRepository(session).get_agent("race-agent")
        assert slot.deleted is (not save_first)
        assert (agent is not None) is save_first


@pytest.mark.parametrize("rename_first", [True, False])
async def test_brain_slot_new_key_and_rename_share_unique_namespace(catalog_db, rename_first):
    db = catalog_db
    locked = asyncio.Event()
    release = asyncio.Event()

    async def write(rename, hold):
        try:
            async with db.session() as session:
                repo = ModelRepository(session)
                if rename:
                    await repo.update_brain_profile(
                        BrainProfile("reasoning"), key=BrainProfile("collision")
                    )
                else:
                    await repo.create_brain_profile(BrainProfile("collision"), name="Other")
                if hold:
                    locked.set()
                    await release.wait()
            return "renamed" if rename else "created"
        except LubanError as error:
            return error.code.value

    first = asyncio.create_task(write(rename_first, True))
    await asyncio.wait_for(locked.wait(), timeout=10)
    second = asyncio.create_task(write(not rename_first, False))
    try:
        await asyncio.sleep(0.1)
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), timeout=15)
    assert results == [
        "renamed" if rename_first else "created",
        ErrorCode.BRAIN_PROFILE_CONFLICT.value,
    ]
    async with db.session() as session:
        canonical = await session.scalar(
            select(BrainProfileRow).where(BrainProfileRow.key == "collision")
        )
        reserved = await session.get(BrainProfileAliasRow, "collision")
        assert canonical.id == reserved.profile_id
        assert canonical.name == ("Reasoning" if rename_first else "Other")


async def test_brain_slot_save_uses_latest_canonical_key_after_waiting(catalog_db):
    db = catalog_db
    locked = asyncio.Event()
    release = asyncio.Event()

    async def rename():
        async with db.session() as session:
            await ModelRepository(session).update_brain_profile(
                BrainProfile("reasoning"), key=BrainProfile("deep_reasoning")
            )
            locked.set()
            await release.wait()

    async def save():
        async with db.session() as session:
            return await AgentRepository(session).create_agent(
                AgentDefinition(key="legacy-race", name="Legacy", brain={"profile": "reasoning"})
            )

    owner = asyncio.create_task(rename())
    await asyncio.wait_for(locked.wait(), timeout=10)
    waiting = asyncio.create_task(save())
    try:
        await asyncio.sleep(0.1)
    finally:
        release.set()
    _, definition = await asyncio.wait_for(asyncio.gather(owner, waiting), timeout=15)
    assert definition.brain.profile == BrainProfile("deep_reasoning")
    async with db.session() as session:
        slot = await ModelRepository(session).require_brain_profile(BrainProfile("reasoning"))
        refs = await ModelRepository(session).brain_profile_references(slot)
        assert refs[0]["agent_key"] == "legacy-race"
