"""graph 测试基建：tmp SQLite + fake brain seed + runner 工厂，零网络零密钥。"""

from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from lubanagent.config import Settings
from lubanagent.core import (
    AgentDefinition,
    BoundaryConfig,
    BrainConfig,
    BrainProfile,
)
from lubanagent.extension import Registry
from lubanagent.graph import GraphRunner
from lubanagent.model import ScriptedFakeChatModel, fake_definition
from lubanagent.persistence import AgentRepository, Base, Database, ModelRepository
from lubanagent.runtime import AgentRuntime


class UsageFakeChatModel(ScriptedFakeChatModel):
    """每次调用在流尾补一条 usage_metadata chunk，构造带用量的 fake（预算测试用）。"""

    usage: tuple[int, int] = (10, 5)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for chunk in self._chunks_for(self._to_message(self._next())):
            yield chunk
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                usage_metadata={
                    "input_tokens": self.usage[0],
                    "output_tokens": self.usage[1],
                    "total_tokens": self.usage[0] + self.usage[1],
                },
            )
        )


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def settings() -> Settings:
    return Settings(observability_backend="none")


async def seed_fake_brain(
    db: Database,
    *,
    script: list[Any],
    profile: BrainProfile = BrainProfile.SMART,
    key: str = "fake-chat",
    pricing: tuple[float, float] | None = None,
) -> None:
    """注册 fake 模型并把指定大脑槽位映射到它（env=dev）。

    pricing = (input_price_per_1k, output_price_per_1k)，缺省不配价（成本未知语义）。
    """
    definition = fake_definition(key=key, script=script)
    if pricing is not None:
        definition = definition.model_copy(
            update={
                "params": {
                    **definition.params,
                    "input_price_per_1k": pricing[0],
                    "output_price_per_1k": pricing[1],
                }
            }
        )
    async with db.session() as session:
        repo = ModelRepository(session)
        await repo.upsert_model(definition)
        await repo.set_brain_mapping("dev", profile, key)


async def create_test_agent(
    db: Database,
    *,
    key: str = "test-agent",
    instructions: str = "你是测试助手。",
) -> AgentDefinition:
    definition = AgentDefinition(
        key=key,
        name=key,
        instructions=instructions,
        tools=[],
        tool_signatures={},
        boundary=BoundaryConfig(),
        brain=BrainConfig(),
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)
    return definition


@pytest.fixture
def make_runtime(db: Database, settings: Settings, registry: Registry):
    def factory(**kwargs: Any) -> AgentRuntime:
        return AgentRuntime(db=db, settings=settings, registry=registry, **kwargs)

    return factory


@pytest.fixture
def make_runner(db: Database, registry: Registry):
    def factory(**kwargs: Any) -> GraphRunner:
        return GraphRunner(db=db, registry=registry, **kwargs)

    return factory
