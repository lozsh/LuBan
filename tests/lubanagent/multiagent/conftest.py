"""multiagent 测试基建：tmp SQLite + 多槽位 fake brain seed + runner 工厂，零网络零密钥。

关键约束：Runtime 每次子 Run 都按模型定义新建 fake 实例（script cursor 归零），
而编排测试需要 supervisor 的第 N 次 decide 消费 script 第 N 条（跨子 Run 顺序）。
故本包统一用 autouse fixture 把 create_chat_model 换成"按模型 key 单例"的
SequentialFakeChatModel（bind_tools 不拷贝，cursor 跨子 Run 持续推进）。
"""

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
from lubanagent.model import ScriptedFakeChatModel, fake_definition
from lubanagent.multiagent import Team, TeamBuilder, TeamRunner
from lubanagent.persistence import AgentRepository, Base, Database, ModelRepository
from lubanagent.runtime import AgentRuntime


class SequentialFakeChatModel(ScriptedFakeChatModel):
    """script 顺序回放的 fake：bind_tools 返回自身（cursor 不被 model_copy 冻结）。

    usage 非空时流尾补 usage_metadata chunk（预算熔断测试用）。
    """

    usage: tuple[int, int] | None = None

    def bind_tools(self, tools: Any, **kwargs: Any) -> "SequentialFakeChatModel":
        self.bound_tools = list(tools)
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for chunk in self._chunks_for(self._to_message(self._next())):
            yield chunk
        if self.usage is not None:
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


@pytest.fixture(autouse=True)
def sequential_models(monkeypatch):
    """create_chat_model 替换为按模型 key 单例的 SequentialFakeChatModel 工厂。

    跨子 Run 持续推进同一 script cursor（编排多轮 decide 的顺序脚本）。
    测试自身再 monkeypatch create_chat_model 时以后者为准（如中途取消场景）。
    """

    class _SequentialRegistry:
        def __init__(self) -> None:
            self._instances: dict[str, SequentialFakeChatModel] = {}
            self._usage: dict[str, tuple[int, int]] = {}

        def set_usage(self, model_key: str, usage: tuple[int, int]) -> None:
            self._usage[model_key] = usage
            instance = self._instances.get(model_key)
            if instance is not None:
                instance.usage = usage

        def factory(self, definition):
            instance = self._instances.get(definition.key)
            if instance is None:
                instance = SequentialFakeChatModel(
                    script=list(definition.params.get("script", [])),
                    usage=self._usage.get(definition.key),
                )
                self._instances[definition.key] = instance
            return instance

    sequential = _SequentialRegistry()
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model", sequential.factory
    )
    return sequential


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
    profile: BrainProfile,
    key: str,
    pricing: tuple[float, float] | None = None,
) -> None:
    """注册 fake 模型并把指定大脑槽位映射到它（env=dev）；不同 Agent 用不同槽位隔离 script。

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
    key: str,
    description: str = "",
    instructions: str = "你是测试助手。",
    brain: BrainConfig | None = None,
    tools: list[str] | None = None,
) -> AgentDefinition:
    definition = AgentDefinition(
        key=key,
        name=key,
        description=description,
        instructions=instructions,
        tools=tools or [],
        tool_signatures={},
        boundary=BoundaryConfig(),
        brain=brain or BrainConfig(),
    )
    async with db.session() as session:
        await AgentRepository(session).create_agent(definition)
    return definition


# 角色 → 大脑槽位：triage/faq/refund 各自绑独立 fake 模型，script 互不干扰
_ROLE_PROFILE = {
    "triage": (BrainProfile.SMART, "fake-smart"),
    "faq": (BrainProfile.FAST, "fake-fast"),
    "refund": (BrainProfile.CHEAP, "fake-cheap"),
}


async def seed_triage_team(
    db: Database,
    *,
    triage_script: list[Any],
    faq_script: list[Any] | None = None,
    refund_script: list[Any] | None = None,
    max_delegations: int = 8,
    faq_tools: list[str] | None = None,
    entry_member: str | None = None,
    fanout: bool = False,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> Team:
    """分流团队：triage（supervisor）+ faq/refund 成员（按需），全 fake 落库。

    triage_script 跨子 Run 顺序消费（第 N 次 decide 用第 N 条，见 sequential_models）。
    entry_member / fanout 透传 TeamBuilder.route（路由策略声明）。
    pricing 按模型 key 配价（如 {"fake-smart": (0.002, 0.001)}），缺省不配价。
    """
    scripts = {"triage": triage_script, "faq": faq_script, "refund": refund_script}
    descriptions = {
        "triage": "客服请求分流",
        "faq": "退货政策咨询",
        "refund": "退款执行",
    }
    pricing = pricing or {}
    builder = TeamBuilder("triage-team", description="客服分流团队")
    for role, script in scripts.items():
        if script is None:
            continue
        profile, model_key = _ROLE_PROFILE[role]
        await seed_fake_brain(
            db, script=script, profile=profile, key=model_key, pricing=pricing.get(model_key)
        )
        await create_test_agent(
            db,
            key=role,
            description=descriptions[role],
            brain=BrainConfig(profile=profile),
            tools=faq_tools if role == "faq" else None,
        )
        if role == "triage":
            builder.supervisor(role)
        else:
            builder.agent(role, description=descriptions[role])
    return builder.route(
        max_delegations=max_delegations, entry_member=entry_member, fanout=fanout
    ).build()


@pytest.fixture
def make_team_runner(db: Database, settings: Settings, registry: Registry):
    def factory() -> TeamRunner:
        return TeamRunner(
            db=db,
            runtime=AgentRuntime(db=db, settings=settings, registry=registry),
            registry=registry,
        )

    return factory
