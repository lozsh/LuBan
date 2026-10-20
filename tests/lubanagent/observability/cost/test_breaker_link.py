"""熔断复验（集成）：max_tokens 极小的 Run 撞预算 → DB trace_events 有 BUDGET_EXCEEDED。

D1-4 BudgetGuard 事中熔断的产出复验，测试基建复用 runtime conftest 的 fake 模型族。
"""

import pytest

from lubanagent.config import Settings
from lubanagent.core import EndReason, RunStatus, TraceEventType
from lubanagent.extension import Registry
from lubanagent.persistence import TraceRepository
from lubanagent.runtime import AgentRuntime, RunRequest
from tests.lubanagent.runtime.conftest import (
    UsageFakeChatModel,
    budget_boundary,
    create_test_agent,
    seed_fake_brain,
)


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def settings() -> Settings:
    return Settings(observability_backend="none")


async def test_budget_breaker_writes_budget_exceeded_event(
    db, registry, settings, monkeypatch
) -> None:
    await seed_fake_brain(db, script=["占位"])  # brain 映射就位，实际模型由 monkeypatch 接管
    await create_test_agent(db, boundary=budget_boundary(max_tokens=1))
    monkeypatch.setattr(
        "lubanagent.runtime.agent_loop.create_chat_model",
        lambda definition: UsageFakeChatModel(script=["超长回答"], usage=(10, 5)),
    )
    runtime = AgentRuntime(db=db, settings=settings, registry=registry)

    run = await runtime.run(RunRequest(agent_key="test-agent", user_input="烧钱"))

    assert run.status is RunStatus.FAILED
    assert run.end_reason is EndReason.BUDGET_EXCEEDED
    async with db.session() as session:
        events = await TraceRepository(session).list_events(run.id)
    types = [event.type for event in events]
    assert TraceEventType.BUDGET_EXCEEDED in types
    assert types[-1] is TraceEventType.RUN_FAILED
