"""Alembic 环境：URL 取 Settings().sync_database_url（DATABASE_URL env 可覆盖）。"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lubanagent.config import Settings
from lubanagent.persistence import models  # noqa: F401  # 注册全部表到 metadata
from lubanagent.persistence.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Settings 是 BaseSettings：DATABASE_URL 环境变量天然覆盖默认值；
# sync_database_url 把 async 驱动（aiosqlite/asyncmy）映射为同步驱动供 Alembic 使用。
config.set_main_option("sqlalchemy.url", Settings().sync_database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
