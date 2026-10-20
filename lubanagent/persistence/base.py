"""ORM 声明基类：Alembic target_metadata 的唯一来源。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """全部表的声明基类；metadata 供 Alembic autogenerate 使用。"""
