"""evals 测试基建：tmp SQLite + fake 模型/brain 映射 seed + runtime 工厂，零网络零密钥。

与 tests/lubanagent/runtime/conftest.py 同思路，按测试包自治原则自建一份（不跨包 import）。
"""

from typing import Any

import pytest

from lubanagent.config import Settings
from lubanagent.core import (
    AgentDefinition,
    BoundaryConfig,
    BrainConfig,
    BrainProfile,
    ModelCapability,
)
from lubanagent.extension import Registry, tool
from lubanagent.model import fake_definition
from lubanagent.persistence import AgentRepository, Base, Database, ModelRepository
from lubanagent.runtime import AgentRuntime


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


def register_query_order(registry: Registry):
    """注册 query_order 工具（订单查询，评测用例的期望工具）。"""

    @tool(registry=registry, description="查询订单物流")
    def query_order(order_id: str) -> str:
        return f"订单 {order_id} 运输中"

    return registry.get_tool("query_order").descriptor


async def seed_fake_brain(
    db: Database,
    *,
    script: list[Any],
    profile: BrainProfile = BrainProfile.SMART,
    key: str = "fake-chat",
    capability: ModelCapability | None = None,
) -> None:
    """注册 fake 模型并把指定大脑槽位映射到它（env=dev）。"""
    definition = fake_definition(key=key, script=script, capability=capability)
    async with db.session() as session:
        repo = ModelRepository(session)
        await repo.upsert_model(definition)
        await repo.set_brain_mapping("dev", profile, key)


async def create_test_agent(
    db: Database,
    *,
    key: str = "test-agent",
    tools: list[str] | None = None,
) -> AgentDefinition:
    definition = AgentDefinition(
        key=key,
        name=key,
        instructions="你是售后客服，先安抚情绪，再给替代方案。",
        tools=tools or [],
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
