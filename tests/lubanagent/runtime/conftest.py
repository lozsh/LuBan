"""runtime 测试基建：tmp SQLite + fake 模型/brain 映射 seed + runtime 工厂，零网络零密钥。"""

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from lubanagent.config import Settings
from lubanagent.core import (
    AgentDefinition,
    BoundaryConfig,
    BrainConfig,
    BrainProfile,
    Budget,
    ModelCapability,
)
from lubanagent.extension import Registry, tool
from lubanagent.model import ScriptedFakeChatModel, fake_definition
from lubanagent.persistence import AgentRepository, Base, Database, ModelRepository
from lubanagent.runtime import AgentRuntime


class RuntimeFakeChatModel(ScriptedFakeChatModel):
    """扩展 script 条目：{"content": str, "tool_calls": [...]} 文本与工具调用同条返回。"""

    @staticmethod
    def _to_message(item):
        if isinstance(item, dict) and "content" in item:
            return AIMessage(
                content=item["content"],
                tool_calls=[
                    {
                        "name": call["name"],
                        "args": call.get("args", {}),
                        "id": call.get("id") or f"call_{index}",
                        "type": "tool_call",
                    }
                    for index, call in enumerate(item.get("tool_calls") or [])
                ],
            )
        return ScriptedFakeChatModel._to_message(item)

    @staticmethod
    def _chunks_for(message):
        for position, word in enumerate(str(message.content or "").split(" ")):
            if not word:
                continue
            text = word if position == 0 else f" {word}"
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        for index, call in enumerate(message.tool_calls or []):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"], ensure_ascii=False),
                            "id": call["id"],
                            "index": index,
                        }
                    ],
                )
            )


class UsageFakeChatModel(RuntimeFakeChatModel):
    """每次调用在流尾补一条 usage_metadata chunk，供预算/成本熔断测试构造带用量的 fake。"""

    usage: tuple[int, int] = (10, 5)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        item = self._next()
        message = self._to_message(item)
        for chunk in self._chunks_for(message):
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


def register_echo(registry: Registry, **tool_kwargs: Any):
    """注册 echo 工具并返回 descriptor（signature_hash 供版本冻结校验）。"""

    @tool(registry=registry, description="回显文本", **tool_kwargs)
    def echo(text: str) -> str:
        return f"echo:{text}"

    return registry.get_tool("echo").descriptor


async def seed_fake_brain(
    db: Database,
    *,
    script: list[Any],
    profile: BrainProfile = BrainProfile.SMART,
    key: str = "fake-chat",
    capability: ModelCapability | None = None,
    pricing: tuple[float, float] | None = None,
) -> None:
    """注册 fake 模型并把指定大脑槽位映射到它（env=dev）。"""
    definition = fake_definition(key=key, script=script, capability=capability)
    if pricing is not None:
        definition.params["input_price_per_1k"] = pricing[0]
        definition.params["output_price_per_1k"] = pricing[1]
    async with db.session() as session:
        repo = ModelRepository(session)
        await repo.upsert_model(definition)
        await repo.set_brain_mapping("dev", profile, key)


async def create_test_agent(
    db: Database,
    *,
    key: str = "test-agent",
    tools: list[str] | None = None,
    tool_signatures: dict[str, str] | None = None,
    boundary: BoundaryConfig | None = None,
    brain: BrainConfig | None = None,
    instructions: str = "你是测试助手。",
) -> AgentDefinition:
    definition = AgentDefinition(
        key=key,
        name=key,
        instructions=instructions,
        tools=tools or [],
        tool_signatures=tool_signatures or {},
        boundary=boundary or BoundaryConfig(),
        brain=brain or BrainConfig(),
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)
    return definition


@pytest.fixture
def make_runtime(db: Database, settings: Settings, registry: Registry):
    def factory(**kwargs: Any) -> AgentRuntime:
        return AgentRuntime(db=db, settings=settings, registry=registry, **kwargs)

    return factory


def budget_boundary(max_tokens: int | None = None, **kwargs: Any) -> BoundaryConfig:
    return BoundaryConfig(budget=Budget(max_tokens=max_tokens, **kwargs))
