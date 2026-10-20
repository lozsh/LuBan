"""Trace 模型: 运行事件流、步骤与 Trace 头, 回答 Trace 七问。"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .run import RunStatus, TokenUsage


class TraceEventType(str, Enum):  # noqa: UP042  # 契约钉死枚举用 str, Enum
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    MODEL_CALL_STARTED = "model_call_started"
    MODEL_TOKEN_DELTA = "model_token_delta"
    MODEL_CALL_COMPLETED = "model_call_completed"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"
    TOOL_APPROVAL_REQUESTED = "tool_approval_requested"
    TOOL_APPROVAL_RESOLVED = "tool_approval_resolved"
    BUDGET_WARNING = "budget_warning"
    BUDGET_EXCEEDED = "budget_exceeded"
    MODEL_FALLBACK = "model_fallback"
    KNOWLEDGE_NO_HIT = "knowledge_no_hit"


class TraceEvent(BaseModel):
    id: str
    run_id: str
    type: TraceEventType
    step_index: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class Step(BaseModel):
    """一次执行步骤 (模型调用 / 工具调用 / 图节点) 。"""

    index: int
    kind: Literal["model", "tool", "graph_node"]
    name: str
    started_at: datetime
    ended_at: datetime | None = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    error: dict[str, Any] | None = None


class Trace(BaseModel):
    id: str
    run_id: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime | None = None
