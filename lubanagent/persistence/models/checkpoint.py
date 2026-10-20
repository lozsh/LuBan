"""checkpoints 表：LangGraph checkpointer 落库布局（实现在 D3-4）。"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.persistence.base import Base


class CheckpointRow(Base):
    __tablename__ = "checkpoints"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    type: Mapped[str] = mapped_column(String(64))
    checkpoint: Mapped[bytes] = mapped_column(sa.LargeBinary)
    # metadata 是 Declarative 保留名，属性名加下划线、列名保持 metadata
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", sa.JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
