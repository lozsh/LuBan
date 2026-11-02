"""工具描述与执行契约: 风险等级、超时重试、审批要求、签名摘要。"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):  # noqa: UP042  # 契约钉死枚举用 str, Enum
    """工具风险等级, 高风险默认要求审批。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolContract(BaseModel):
    """工具执行契约, 由代码强制执行而非 Prompt 声明。"""

    timeout_seconds: float = 30.0
    max_retries: int = 0
    idempotent: bool = False
    approval_required: bool = False
    # sync = 调用方阻塞等结果（唯一存量路径）；async = 立即返回占位、后台执行、
    # 模型用 task_result/task_stop 伴生工具取结果/终止（需运行时开关 TOOL_ASYNC_ENABLED
    # 同时开启；关闭时 async 声明按 sync 路径执行，零行为变化）
    execution_mode: Literal["sync", "async"] = "sync"


class ToolDescriptor(BaseModel):
    """工具注册描述; signature_hash 用于版本兼容治理。"""

    key: str
    name: str
    description: str = ""
    args_schema: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    contract: ToolContract = Field(default_factory=ToolContract)
    signature_hash: str = ""


@dataclass(frozen=True)
class ApprovalRequest:
    """一次高风险工具人工确认请求; 终结状态为 approved/rejected/expired。

    落在 core 供 tool（治理）与 persistence（持久化）双向消费——两个模块
    都只允许依赖 core，不能互相 import（importlinter 分层契约）。
    """

    id: str
    run_id: str
    tool_key: str
    args: dict[str, Any]
    created_at: datetime
    status: Literal["pending", "approved", "rejected", "expired"]
    decided_by: str | None = None
    decided_at: datetime | None = None

    def resolved(
        self,
        status: Literal["approved", "rejected", "expired"],
        *,
        decided_by: str | None = None,
    ) -> "ApprovalRequest":
        """返回终结态副本（decided_at=now）；原对象不变（frozen dataclass 语义）。"""
        return replace(self, status=status, decided_by=decided_by, decided_at=datetime.now(UTC))
