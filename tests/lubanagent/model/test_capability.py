"""能力校验与受控降级: 启动前挡下不匹配, 降级留 MODEL_FALLBACK 事件。"""

import pytest

from lubanagent.core import (
    AgentDefinition,
    BrainConfig,
    BrainProfile,
    CapabilityMismatchError,
    IOContract,
    ModelCapability,
    ModelDefinition,
    TraceEventType,
)
from lubanagent.model import (
    BrainResolver,
    check_capability,
    required_capabilities,
    resolve_brain_for_agent,
)
from lubanagent.model.fakes import fake_definition

from .test_brain_resolver import FakeStore


def _agent(**overrides: object) -> AgentDefinition:
    base: dict[str, object] = {"key": "cs", "name": "客服"}
    base.update(overrides)
    return AgentDefinition(**base)  # type: ignore[arg-type]


def _definition(key: str, **capability: bool) -> ModelDefinition:
    return fake_definition(key=key, capability=ModelCapability(**capability))


def test_required_capabilities() -> None:
    assert required_capabilities(_agent()) == ["chat"]
    assert required_capabilities(_agent(tools=["search"])) == ["chat", "tool_calling"]
    assert "structured_output" in required_capabilities(
        _agent(io=IOContract(output_schema={"type": "object"}))
    )
    assert "vision" in required_capabilities(_agent(io=IOContract(input_type="multimodal")))


def test_check_capability_missing_tool_calling() -> None:
    agent = _agent(tools=["search"])
    model = _definition("m", tool_calling=False)
    report = check_capability(agent, model)
    assert not report.ok
    assert "tool_calling" in report.missing


def test_check_capability_ok() -> None:
    agent = _agent(tools=["search"])
    model = _definition("m", tool_calling=True)
    report = check_capability(agent, model)
    assert report.ok
    assert report.missing == []


async def test_resolve_primary_ok() -> None:
    agent = _agent()
    definition = _definition("smart-model")
    resolver = BrainResolver(FakeStore({("dev", BrainProfile.SMART): definition}), env="dev")
    resolution = await resolve_brain_for_agent(agent, resolver)
    assert resolution.definition is definition
    assert not resolution.used_fallback
    assert resolution.resolved_profile == BrainProfile.SMART
    assert resolution.fallback_events == []


async def test_fallback_hit_leaves_trace_event() -> None:
    agent = _agent(
        tools=["search"],
        brain=BrainConfig(
            profile=BrainProfile.SMART, fallback_profiles=[BrainProfile.FAST]
        ),
    )
    store = FakeStore(
        {
            ("dev", BrainProfile.SMART): _definition("no-tools", tool_calling=False),
            ("dev", BrainProfile.FAST): _definition("with-tools", tool_calling=True),
        }
    )
    resolution = await resolve_brain_for_agent(agent, BrainResolver(store, env="dev"))
    assert resolution.used_fallback
    assert resolution.resolved_profile == BrainProfile.FAST
    assert resolution.definition.key == "with-tools"
    assert len(resolution.fallback_events) == 1
    event = resolution.fallback_events[0]
    assert event.type == TraceEventType.MODEL_FALLBACK
    assert event.payload["from_profile"] == "smart"
    assert event.payload["to_profile"] == "fast"
    assert event.payload["model_key"] == "with-tools"
    assert "tool_calling" in event.payload["reason"]
    assert event.created_at.tzinfo is not None


async def test_primary_not_mapped_enters_fallback_chain() -> None:
    agent = _agent(brain=BrainConfig(fallback_profiles=[BrainProfile.CHEAP]))
    store = FakeStore({("dev", BrainProfile.CHEAP): _definition("cheap-model")})
    resolution = await resolve_brain_for_agent(agent, BrainResolver(store, env="dev"))
    assert resolution.used_fallback
    assert resolution.resolved_profile == BrainProfile.CHEAP
    assert resolution.fallback_events[0].payload["reason"].startswith("brain not mapped")


async def test_all_candidates_fail_raises_mismatch() -> None:
    agent = _agent(
        tools=["search"],
        brain=BrainConfig(fallback_profiles=[BrainProfile.FAST, BrainProfile.CHEAP]),
    )
    store = FakeStore(
        {
            ("dev", BrainProfile.SMART): _definition("a", tool_calling=False),
            ("dev", BrainProfile.FAST): _definition("b", tool_calling=False),
        }
    )
    with pytest.raises(CapabilityMismatchError) as exc_info:
        await resolve_brain_for_agent(agent, BrainResolver(store, env="dev"))
    assert exc_info.value.details["missing"] == ["tool_calling"]
    assert exc_info.value.details["tried"] == ["smart", "fast", "cheap"]
