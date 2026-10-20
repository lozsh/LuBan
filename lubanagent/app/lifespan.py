"""lifespan：种子落库与应用生命周期。

seed_defaults 幂等 upsert 缺省模型与四槽位映射（luban init 与装配期共用）。
"""

from lubanagent.config import defaults, get_settings
from lubanagent.core import BrainProfile, ModelDefinition
from lubanagent.persistence import Database, ModelRepository


async def seed_defaults(db: Database) -> None:
    """幂等 upsert SEED_MODEL_DEFINITIONS + SEED_BRAIN_MAPPINGS（fake 模型 + 四槽位）。

    供 `luban init`（D1-8）与测试调用；映射按当前 Settings.env 落库。
    槽位播种只填空位：已有映射（用户经工作台/API 配过）不覆盖——否则每次
    `luban init`（如 dev.sh 重启）都会把真实模型打回 fake-chat。
    """
    env = get_settings().env
    async with db.session() as session:
        repo = ModelRepository(session)
        for payload in defaults.SEED_MODEL_DEFINITIONS:
            # 只填不存在：用户经工作台/API 改过的同 key 定义不被种子打回（H08）
            if await repo.get_model(payload["key"]) is not None:
                continue
            await repo.upsert_model(ModelDefinition.model_validate(payload))
        for profile, model_key in defaults.SEED_BRAIN_MAPPINGS.items():
            if await repo.has_brain_mapping(env, BrainProfile(profile)):
                continue
            await repo.set_brain_mapping(env, BrainProfile(profile), model_key)
