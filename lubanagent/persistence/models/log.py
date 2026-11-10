"""程序日志审计表：关联字段独立索引，正文在写入前脱敏。"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.persistence.base import Base


class ApplicationLogRow(Base):
    __tablename__ = "application_logs"
    __table_args__ = (
        sa.Index("ix_application_logs_session_time", "session_id", "created_at", "id"),
        sa.Index("ix_application_logs_run_time", "run_id", "created_at", "id"),
        sa.Index("ix_application_logs_root_time", "root_run_id", "created_at", "id"),
        sa.Index("ix_application_logs_trace_time", "trace_id", "created_at", "id"),
        sa.Index("ix_application_logs_request_time", "request_id", "created_at", "id"),
        sa.Index("ix_application_logs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    level: Mapped[str] = mapped_column(sa.String(16))
    logger: Mapped[str] = mapped_column(sa.String(255))
    message: Mapped[str] = mapped_column(sa.Text)
    exception: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    pathname: Mapped[str] = mapped_column(sa.String(1024))
    lineno: Mapped[int] = mapped_column(sa.Integer)
    request_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    session_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    run_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    root_run_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    subject: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
