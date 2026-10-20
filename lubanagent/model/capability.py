"""能力校验与受控降级: Run 启动前挡下不匹配, 降级显式留痕。"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lubanagent.core import (
    AgentDefinition,
    BrainProfile,
    CapabilityMismatchError,
    ErrorCode,
    LubanError,
    ModelDefinition,
    TraceEvent,
    TraceEventType,
)

from .brain_resolver import BrainResolution, BrainResolver


@dataclass(frozen=True)
class CapabilityReport:
    ok: bool
    missing: list[str] = field(default_factory=list)


def required_capabilities(agent: AgentDefinition) -> list[str]:
    """从 Agent 定义推导所需能力; "chat" 恒需。"""
    required = ["chat"]
    if agent.tools:
        required.append("tool_calling")
    if agent.io.output_schema:
        required.append("structured_output")
    if agent.io.input_type == "multimodal":
        required.append("vision")
    return required


def check_capability(agent: AgentDefinition, model: ModelDefinition) -> CapabilityReport:
    """逐项对照 required vs model.capability。"""
    capability = model.capability
    missing = [name for name in required_capabilities(agent) if not getattr(capability, name)]
    return CapabilityReport(ok=not missing, missing=missing)


def _fallback_event(
    *, from_profile: BrainProfile, to_profile: BrainProfile, model_key: str, reason: str
) -> TraceEvent:
    return TraceEvent(
        id=uuid.uuid4().hex,
        run_id="",  # 由 Runtime 接线时回填
        type=TraceEventType.MODEL_FALLBACK,
        payload={
            "from_profile": from_profile.value,
            "to_profile": to_profile.value,
            "model_key": model_key,
            "reason": reason,
        },
        created_at=datetime.now(UTC),
    )


async def resolve_brain_for_agent(
    agent: AgentDefinition, resolver: BrainResolver
) -> BrainResolution:
    """受控降级: 主 profile 不满足或映射缺失时按 fallback_profiles 顺序找首个可用。"""
    primary = agent.brain.profile
    tried: list[str] = [primary.value]
    last_missing: list[str] = []
    reason = ""

    try:
        definition = await resolver.resolve(primary)
    except LubanError as exc:
        if exc.code != ErrorCode.BRAIN_NOT_MAPPED:
            raise
        reason = f"brain not mapped: {primary.value}"
    else:
        report = check_capability(agent, definition)
        if report.ok:
            return BrainResolution(
                profile=primary,
                definition=definition,
                used_fallback=False,
                resolved_profile=primary,
                fallback_events=[],
            )
        last_missing = report.missing
        reason = f"capability mismatch, missing: {', '.join(report.missing)}"

    for fallback in agent.brain.fallback_profiles:
        tried.append(fallback.value)
        try:
            fallback_definition = await resolver.resolve(fallback)
        except LubanError as exc:
            if exc.code != ErrorCode.BRAIN_NOT_MAPPED:
                raise
            continue
        report = check_capability(agent, fallback_definition)
        if report.ok:
            return BrainResolution(
                profile=primary,
                definition=fallback_definition,
                used_fallback=True,
                resolved_profile=fallback,
                fallback_events=[
                    _fallback_event(
                        from_profile=primary,
                        to_profile=fallback,
                        model_key=fallback_definition.key,
                        reason=reason,
                    )
                ],
            )
        last_missing = report.missing

    raise CapabilityMismatchError(
        f"Agent {agent.key} 大脑能力不足且无可用降级",
        details={"missing": last_missing, "tried": tried},
    )
