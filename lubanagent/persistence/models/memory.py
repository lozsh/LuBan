"""记忆三表：session_messages / session_summaries / long_term_memories。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.persistence.base import Base


class SessionMessageRow(Base):
    """会话消息：不寄生 runs 表——runs 是执行记录，消息是对话事实。"""

    __tablename__ = "session_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant / system / tool
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_session_messages_session_id_id", "session_id", "id"),)


class SessionSummaryRow(Base):
    """会话摘要：每会话一行，upto_message_id 标记已压缩到的消息 id（消息不物理删除）。"""

    __tablename__ = "session_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), unique=True)
    summary: Mapped[str] = mapped_column(Text)
    upto_message_id: Mapped[int] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LongTermMemoryRow(Base):
    """长期记忆：agent_key + user_key 维度的跨会话关键事实，显式写入。"""

    __tablename__ = "long_term_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_key: Mapped[str] = mapped_column(String(128), index=True)
    user_key: Mapped[str] = mapped_column(String(128), index=True)
    fact: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
