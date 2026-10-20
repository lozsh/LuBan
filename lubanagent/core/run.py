"""运行模型: Run 状态机、结束原因、token 用量与运行上下文。"""

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from .brain import BrainProfile
from .budget import Budget


class RunStatus(str, Enum):  # noqa: UP042  # 契约钉死枚举用 str, Enum
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EndReason(str, Enum):  # noqa: UP042  # 契约钉死枚举用 str, Enum
    FINAL_ANSWER = "final_answer"
    STEP_LIMIT = "step_limit"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"
    HUMAN_HANDOFF = "human_handoff"
    # 检索全 miss 短路拒答（Run 仍 COMPLETED，output = no_hit_fallback，未调模型）
    KNOWLEDGE_NO_HIT = "knowledge_no_hit"


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class Run(BaseModel):
    """一次运行的完整记录; error 存 LubanError.to_dict()["error"]。"""

    id: str
    agent_key: str
    agent_version: int
    session_id: str | None = None
    user_id: str | None = None  # 透传观测标签（无用户管理语义）
    parent_run_id: str | None = None
    status: RunStatus = RunStatus.PENDING
    input: dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    brain_profile: BrainProfile
    model_key: str | None = None
    end_reason: EndReason | None = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: float = 0.0
    error: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


DepsT = TypeVar("DepsT")


class RunContext(BaseModel, Generic[DepsT]):
    """运行时传给工具/能力的上下文, deps 由调用方注入。"""

    run_id: str
    agent_key: str
    agent_version: int
    session_id: str | None = None
    user_id: str | None = None  # 透传观测标签，子 Run 继承
    brain_profile: BrainProfile
    model_key: str | None = None
    budget: Budget | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    deps: DepsT | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
