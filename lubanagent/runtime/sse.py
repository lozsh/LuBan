"""SSE 外发映射：内部 Trace 事件流 -> 对外 SSE 帧（内部粒度事件默认不外发）。"""

import json
from collections.abc import AsyncIterator
from typing import Any

from lubanagent.core import TraceEvent, TraceEventType
from lubanagent.core.redact import redact_value

SSE_EVENT_MAP: dict[TraceEventType, str] = {
    TraceEventType.RUN_STARTED: "run.started",
    TraceEventType.RUN_RESUMED: "run.resumed",
    TraceEventType.MODEL_TOKEN_DELTA: "token.delta",
    TraceEventType.TOOL_CALL_STARTED: "tool.started",
    TraceEventType.TOOL_CALL_COMPLETED: "tool.finished",
    TraceEventType.TOOL_CALL_FAILED: "tool.failed",
    TraceEventType.RUN_COMPLETED: "run.completed",
    TraceEventType.RUN_FAILED: "run.failed",
    TraceEventType.RUN_CANCELLED: "run.cancelled",
    TraceEventType.TOOL_APPROVAL_REQUESTED: "tool.approval.requested",
    TraceEventType.TOOL_APPROVAL_RESOLVED: "tool.approval.resolved",
    TraceEventType.MODEL_FALLBACK: "model.fallback",
    TraceEventType.BUDGET_WARNING: "budget.warning",
    TraceEventType.BUDGET_EXCEEDED: "budget.exceeded",
    TraceEventType.KNOWLEDGE_NO_HIT: "knowledge.no_hit",
    # STEP_/MODEL_CALL_ 等内部 Trace 粒度事件默认不外发
}


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_stream(events: AsyncIterator[TraceEvent]) -> AsyncIterator[str]:
    """只外发 SSE_EVENT_MAP 覆盖的类型；data = {run_id, type, **payload}（payload 脱敏副本）。"""
    async for event in events:
        name = SSE_EVENT_MAP.get(event.type)
        if name is None:
            continue
        data = {"run_id": event.run_id, "type": event.type.value, **redact_value(event.payload)}
        yield format_sse(name, data)
