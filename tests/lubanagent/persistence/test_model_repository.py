"""ModelRepository：模型注册 upsert 与大脑槽位映射切换。"""

from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import BrainProfile, ModelCapability, ModelDefinition
from lubanagent.persistence.repositories import ModelRepository


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
