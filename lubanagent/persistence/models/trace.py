"""Trace 表：traces 一运行一条，trace_events/token_usages/cost_records 明细，
export_failures 外发失败留痕。"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.persistence.base import Base


class TraceRow(Base):
    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TraceEventRow(Base):
    __tablename__ = "trace_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("traces.id"))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    step_index: Mapped[int | None] = mapped_column(nullable=True)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TokenUsageRow(Base):
    __tablename__ = "token_usages"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    step_index: Mapped[int | None] = mapped_column(nullable=True)
    model_key: Mapped[str] = mapped_column(String(128))
    # H10：模型未回报 usage 时存 NULL（显式未知），不用 0 冒充零消费
    input_tokens: Mapped[int | None] = mapped_column(nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CostRecordRow(Base):
    __tablename__ = "cost_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    model_key: Mapped[str] = mapped_column(String(128))
    cost: Mapped[float] = mapped_column(default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExportFailureRow(Base):
    """外发观测后端失败留痕：供重试器/运维脚本回放（架构 §6.4）。

    retry_count = 重试尝试失败次数，达上限后放弃（保留行供运维查询，不再自动重试）；
    retried_at = 最后一次重试尝试时间。
    """

    __tablename__ = "export_failures"
    __table_args__ = (Index("ix_export_failures_backend", "backend"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    backend: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    error: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    retried_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0, server_default=sa.text("0"))
