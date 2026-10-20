"""defaults 常量契约：种子模型定义必须是合法 ModelDefinition 形状。"""

from lubanagent.config.defaults import (
    FAKE_MODEL_KEY,
    SEED_BRAIN_MAPPINGS,
    SEED_MODEL_DEFINITIONS,
    USER_EXTENSION_PACKAGES,
)
from lubanagent.core import BrainProfile, ModelDefinition


def test_seed_model_definitions_validate_roundtrip() -> None:
    """种子 dict 可被 ModelDefinition.model_validate 消费且往返无损（D1-8 种子落库前置）。"""
    assert len(SEED_MODEL_DEFINITIONS) == 1
    seed = SEED_MODEL_DEFINITIONS[0]
    definition = ModelDefinition.model_validate(seed)
    assert definition.key == FAKE_MODEL_KEY
    assert definition.provider == "fake"
    assert definition.factory == "fake"
    assert definition.capability.tool_calling is True
    assert definition.capability.context_window == 128_000
    assert definition.params["script"]
    assert definition.model_dump(mode="json") == seed


def test_seed_brain_mappings_cover_all_profiles() -> None:
    """四个逻辑大脑槽位全部有映射（零密钥兜底）。"""
    assert set(SEED_BRAIN_MAPPINGS) == {p.value for p in BrainProfile}
    assert all(v == FAKE_MODEL_KEY for v in SEED_BRAIN_MAPPINGS.values())


def test_user_extension_packages_never_imported_at_config_time() -> None:
    """扩展包名只是约定常量，config import 期不触发用户包加载。"""
    assert USER_EXTENSION_PACKAGES == ("app.tools", "app.graphs")
