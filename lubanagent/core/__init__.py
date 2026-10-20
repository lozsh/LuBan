"""core: 纯数据层领域模型与错误体系, 仅依赖 pydantic 与标准库。"""

from .brain import BrainProfile
from .budget import Budget, TokenEstimate
from .definition import (
    AgentDefinition,
    AgentStatus,
    AgentVersion,
    BoundaryConfig,
    BrainConfig,
    IOContract,
    KnowledgeConfig,
    MemoryConfig,
)
from .errors import (
    BudgetExceededError,
    CapabilityMismatchError,
    ErrorCode,
    LubanError,
    RunCancelledError,
    RunTimeoutError,
    StepLimitExceededError,
    ToolApprovalRequiredError,
)
from .evaluation import (
    AssertionVerdict,
    CaseResult,
    ComparisonReport,
    EvalCase,
    EvalDataset,
    EvalReport,
    EvalReportDetail,
    EvalReportSummary,
    ExpectOutput,
    JudgeSpec,
    compare_reports,
)
from .mcp import McpServer
from .model import ModelCapability, ModelDefinition
from .prompt import PromptDefinition, PromptVersion
from .run import DepsT, EndReason, Run, RunContext, RunStatus, TokenUsage
from .skill import SkillMeta, SkillPackage
from .tool import ApprovalRequest, RiskLevel, ToolContract, ToolDescriptor
from .trace import Step, Trace, TraceEvent, TraceEventType

__all__ = [
    "AgentDefinition",
    "AgentStatus",
    "AgentVersion",
    "ApprovalRequest",
    "AssertionVerdict",
    "BoundaryConfig",
    "BrainConfig",
    "BrainProfile",
    "Budget",
    "BudgetExceededError",
    "CapabilityMismatchError",
    "CaseResult",
    "ComparisonReport",
    "DepsT",
    "EndReason",
    "ErrorCode",
    "EvalCase",
    "EvalDataset",
    "EvalReport",
    "EvalReportDetail",
    "EvalReportSummary",
    "ExpectOutput",
    "IOContract",
    "JudgeSpec",
    "KnowledgeConfig",
    "LubanError",
    "McpServer",
    "MemoryConfig",
    "ModelCapability",
    "ModelDefinition",
    "PromptDefinition",
    "PromptVersion",
    "RiskLevel",
    "Run",
    "RunCancelledError",
    "RunContext",
    "RunStatus",
    "RunTimeoutError",
    "SkillMeta",
    "SkillPackage",
    "Step",
    "StepLimitExceededError",
    "TokenEstimate",
    "TokenUsage",
    "ToolApprovalRequiredError",
    "ToolContract",
    "ToolDescriptor",
    "Trace",
    "TraceEvent",
    "TraceEventType",
    "compare_reports",
]
