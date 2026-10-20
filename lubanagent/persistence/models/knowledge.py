"""knowledge_chunks / knowledge_sources 表：知识切块向量与工作台知识源元数据。"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.persistence.base import Base


class KnowledgeChunkRow(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_knowledge_chunks_source", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(128))
    chunk_index: Mapped[int] = mapped_column()
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(sa.JSON)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", sa.JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeSourceRow(Base):
    """工作台创建的知识源（managed）；代码种子源不入此表。"""

    __tablename__ = "knowledge_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    # name 即 Agent YAML knowledge.sources 里引用的知识源 key
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    chunk_size: Mapped[int] = mapped_column(default=500)
    overlap: Mapped[int] = mapped_column(default=50)
    # [{"filename": str, "size": int}] 落盘文件清单（重新切块按目录重读）
    files: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list)
    chunk_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
