"""SSE 映射单元测试：事件名映射、帧格式与内部粒度事件过滤。"""

import json
from datetime import UTC, datetime

from lubanagent.core import TraceEvent, TraceEventType
from lubanagent.runtime import SSE_EVENT_MAP, format_sse, sse_stream


def _event(event_type: TraceEventType, payload: dict | None = None) -> TraceEvent:
    return TraceEvent(
        id="evt-1",
        run_id="run-1",
        type=event_type,
        payload=payload or {},
        created_at=datetime.now(UTC),
    )


def test_event_map_covers_public_types() -> None:
    assert SSE_EVENT_MAP[TraceEventType.RUN_STARTED] == "run.started"
    assert SSE_EVENT_MAP[TraceEventType.MODEL_TOKEN_DELTA] == "token.delta"
    assert SSE_EVENT_MAP[TraceEventType.TOOL_CALL_COMPLETED] == "tool.finished"
    assert SSE_EVENT_MAP[TraceEventType.BUDGET_EXCEEDED] == "budget.exceeded"
    # 内部 Trace 粒度不外发
    assert TraceEventType.STEP_STARTED not in SSE_EVENT_MAP
    assert TraceEventType.MODEL_CALL_STARTED not in SSE_EVENT_MAP
    assert TraceEventType.MODEL_CALL_COMPLETED not in SSE_EVENT_MAP


def test_format_sse_frame() -> None:
    frame = format_sse("run.started", {"run_id": "r", "备注": "中文"})
    assert frame.startswith("event: run.started\n")
    assert frame.endswith("\n\n")
    body = frame.split("data: ", 1)[1].strip()
    assert json.loads(body) == {"run_id": "r", "备注": "中文"}
    assert "中文" in frame  # ensure_ascii=False


async def test_sse_stream_filters_and_shapes_data() -> None:
    async def events():
        yield _event(TraceEventType.RUN_STARTED)
        yield _event(TraceEventType.STEP_STARTED)  # 不外发
        yield _event(TraceEventType.MODEL_TOKEN_DELTA, {"text": "你"})

    frames = [frame async for frame in sse_stream(events())]
    assert len(frames) == 2
    assert frames[0].startswith("event: run.started\n")
    assert frames[1].startswith("event: token.delta\n")
    data = json.loads(frames[1].split("data: ", 1)[1].strip())
    assert data == {"run_id": "run-1", "type": "model_token_delta", "text": "你"}


async def test_sse_stream_redacts_secret_fields_and_credential_strings() -> None:
    async def events():
        yield _event(
            TraceEventType.TOOL_CALL_STARTED,
            {
                "tool": "refund",
                "args": {"order_id": "A-1", "api_key": "sk-synthetic-secret"},
                "upstream": "https://user:pw@host.invalid/v1",
                "note": "正常业务字段保留",
            },
        )

    frames = [frame async for frame in sse_stream(events())]
    assert len(frames) == 1
    assert "sk-synthetic-secret" not in frames[0]
    assert "user:pw" not in frames[0]
    data = json.loads(frames[0].split("data: ", 1)[1].strip())
    assert data["args"] == {"order_id": "A-1", "api_key": "[REDACTED]"}
    assert data["upstream"] == "[REDACTED]"
    assert data["note"] == "正常业务字段保留"
