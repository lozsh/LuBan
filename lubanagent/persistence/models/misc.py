"""能力/评测表：skills、mcp_servers、eval_datasets、eval_results。"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.persistence.base import Base


class SkillRow(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class McpServerRow(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    transport: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvalDatasetRow(Base):
    __tablename__ = "eval_datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[str] = mapped_column(String(32))
    cases: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvalResultRow(Base):
    """eval_results = EvalReport 全量序列化（SkillRepository 同款）+ 列表页聚合列（R5）。"""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("eval_datasets.id"), index=True)
    agent_key: Mapped[str] = mapped_column(String(128))
    agent_version: Mapped[int] = mapped_column()
    model_key: Mapped[str] = mapped_column(String(128))
    # 聚合列（列表页免解析全量 JSON；由仓储从报告计算写入）
    pass_rate: Mapped[float] = mapped_column()
    passed_cases: Mapped[int] = mapped_column(default=0)
    total_cases: Mapped[int] = mapped_column(default=0)
    wrong_answer_rate: Mapped[float] = mapped_column(default=0.0)
    # 完整报告（逐 case pass/fail、断言明细、judge 结论、run_id/output/error）
    report: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
