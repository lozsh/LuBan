"""统一错误体系: ErrorCode 与 LubanError, 错误可序列化进 Run/Trace。"""

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):  # noqa: UP042  # 契约钉死枚举用 str, Enum
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_VERSION_NOT_FOUND = "AGENT_VERSION_NOT_FOUND"
    AGENT_VERSION_UNRUNNABLE = "AGENT_VERSION_UNRUNNABLE"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    BRAIN_NOT_MAPPED = "BRAIN_NOT_MAPPED"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    NO_FALLBACK_AVAILABLE = "NO_FALLBACK_AVAILABLE"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    TOOL_APPROVAL_REQUIRED = "TOOL_APPROVAL_REQUIRED"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    STEP_LIMIT_EXCEEDED = "STEP_LIMIT_EXCEEDED"
    RUN_TIMEOUT = "RUN_TIMEOUT"
    RUN_CANCELLED = "RUN_CANCELLED"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    PROMPT_NOT_FOUND = "PROMPT_NOT_FOUND"
    PROMPT_VERSION_NOT_FOUND = "PROMPT_VERSION_NOT_FOUND"
    KNOWLEDGE_SOURCE_NOT_FOUND = "KNOWLEDGE_SOURCE_NOT_FOUND"
    GRAPH_NOT_FOUND = "GRAPH_NOT_FOUND"
    GRAPH_STRUCTURE_UNAVAILABLE = "GRAPH_STRUCTURE_UNAVAILABLE"
    AVATAR_NOT_FOUND = "AVATAR_NOT_FOUND"
    EVAL_RESULT_NOT_FOUND = "EVAL_RESULT_NOT_FOUND"


class LubanError(Exception):
    """框架统一异常, to_dict() 结构可整体序列化存储。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details if details is not None else {}
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": str(self.code.value),
                "message": self.message,
                "details": self.details,
                "retryable": self.retryable,
            }
        }

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"


class CapabilityMismatchError(LubanError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.CAPABILITY_MISMATCH, message, details=details)


class BudgetExceededError(LubanError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.BUDGET_EXCEEDED, message, details=details)


class StepLimitExceededError(LubanError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.STEP_LIMIT_EXCEEDED, message, details=details)


class RunTimeoutError(LubanError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.RUN_TIMEOUT, message, details=details)


class RunCancelledError(LubanError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.RUN_CANCELLED, message, details=details)


class ToolApprovalRequiredError(LubanError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.TOOL_APPROVAL_REQUIRED, message, details=details)
