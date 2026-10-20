"""Run 表：一次运行的完整记录，agent 与版本以外键锚定。"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.core import RunStatus
from lubanagent.persistence.base import Base


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_version_id: Mapped[int] = mapped_column(ForeignKey("agent_versions.id"))
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.PENDING.value)
    input: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    brain_profile: Mapped[str] = mapped_column(String(16))
    model_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    token_usage: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    cost: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
