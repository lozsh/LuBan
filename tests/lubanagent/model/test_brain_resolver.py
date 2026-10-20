"""大脑解析: Fake store 命中、无映射报错、改映射下一次 resolve 立即生效。"""

import pytest

from lubanagent.core import BrainProfile, ErrorCode, LubanError, ModelDefinition
from lubanagent.model import BrainResolver
from lubanagent.model.fakes import fake_definition


class FakeStore:
    def __init__(self, mapping: dict[tuple[str, BrainProfile], ModelDefinition]) -> None:
        self.mapping = mapping

    async def get_brain_mapping(self, env: str, profile: BrainProfile) -> ModelDefinition | None:
        return self.mapping.get((env, profile))


async def test_resolve_hit() -> None:
    definition = fake_definition(key="smart-model")
    store = FakeStore({("dev", BrainProfile.SMART): definition})
    resolver = BrainResolver(store, env="dev")
    assert await resolver.resolve(BrainProfile.SMART) is definition


async def test_resolve_not_mapped() -> None:
    resolver = BrainResolver(FakeStore({}), env="dev")
    with pytest.raises(LubanError) as exc_info:
        await resolver.resolve(BrainProfile.FAST)
    assert exc_info.value.code == ErrorCode.BRAIN_NOT_MAPPED
    assert exc_info.value.details == {"env": "dev", "profile": "fast"}


async def test_env_isolation() -> None:
    definition = fake_definition(key="prod-model")
    store = FakeStore({("prod", BrainProfile.SMART): definition})
    resolver = BrainResolver(store, env="dev")
    with pytest.raises(LubanError):
        await resolver.resolve(BrainProfile.SMART)


async def test_mapping_change_takes_effect_immediately() -> None:
    old = fake_definition(key="old-model")
    new = fake_definition(key="new-model")
    store = FakeStore({("dev", BrainProfile.SMART): old})
    resolver = BrainResolver(store, env="dev")
    assert (await resolver.resolve(BrainProfile.SMART)).key == "old-model"
    store.mapping[("dev", BrainProfile.SMART)] = new
    assert (await resolver.resolve(BrainProfile.SMART)).key == "new-model"
