"""大脑解析: brain_mappings(env, profile) → ModelDefinition, 每次 resolve 都过 store。"""

from dataclasses import dataclass, field
from typing import Protocol

from lubanagent.core import (
    BrainProfile,
    ErrorCode,
    LubanError,
    ModelDefinition,
    TraceEvent,
)


class BrainMappingStore(Protocol):
    """映射存储协议; persistence 的 ModelRepository 天然满足, 测试可 Fake。"""

    async def get_brain_mapping(self, env: str, profile: BrainProfile) -> ModelDefinition | None:
        ...


@dataclass(frozen=True)
class BrainResolution:
    """一次大脑解析结果; 降级留痕在 fallback_events, 持久化由 Runtime 接线。"""

    profile: BrainProfile
    definition: ModelDefinition
    used_fallback: bool
    resolved_profile: BrainProfile
    fallback_events: list[TraceEvent] = field(default_factory=list)


class BrainResolver:
    """解析大脑槽位到模型; 不缓存映射, 改映射后下一次 resolve 立即生效。"""

    def __init__(self, store: BrainMappingStore, *, env: str = "dev") -> None:
        self._store = store
        self._env = env

    async def resolve(self, profile: BrainProfile) -> ModelDefinition:
        definition = await self._store.get_brain_mapping(self._env, profile)
        if definition is None:
            raise LubanError(
                ErrorCode.BRAIN_NOT_MAPPED,
                f"env={self._env} 下大脑槽位 {profile.value} 未映射模型",
                details={"env": self._env, "profile": profile.value},
            )
        return definition
