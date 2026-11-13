"""模型中心表：models 注册记录 + brain_mappings 大脑槽位映射。"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from lubanagent.persistence.base import Base


class ModelRow(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    provider: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(256))
    factory: Mapped[str] = mapped_column(String(64))
    capability: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    params: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key_env: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BrainMappingRow(Base):
    __tablename__ = "brain_mappings"
    __table_args__ = (UniqueConstraint("env", "profile"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    env: Mapped[str] = mapped_column(String(32))
    profile: Mapped[str] = mapped_column(String(64))
    model_key: Mapped[str] = mapped_column(ForeignKey("models.key"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BrainProfileRow(Base):
    """逻辑槽位目录；删除留墓碑，防止启动种子把用户删除的预设复活。"""

    __tablename__ = "brain_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    deleted: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BrainProfileAliasRow(Base):
    """统一预留当前及旧标识，防止跨槽位占用别名；重命名不改历史快照。"""

    __tablename__ = "brain_profile_aliases"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("brain_profiles.id"))
