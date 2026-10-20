"""审批表：高风险工具人工确认记录，process 内 manager 的持久化底座（R11 遗留项）。

一次审批一条；status 状态机 pending → approved/rejected/expired，终态不可逆。
decide 的并发安全靠 try_resolve 的条件更新（UPDATE ... WHERE status='pending'），
跨进程双 decide 恰一个成功。
"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.persistence.base import Base


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_key: Mapped[str] = mapped_column(String(128))
    args: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
