"""可编辑大脑槽位目录、旧标识别名及64字符标识。

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRESETS = ("fast", "smart", "cheap", "vision")


def _stored_profile_keys(bind) -> set[str]:
    keys = set(bind.execute(sa.text("SELECT DISTINCT profile FROM brain_mappings")).scalars())
    keys.update(bind.execute(sa.text("SELECT DISTINCT brain_profile FROM runs")).scalars())
    for raw in bind.execute(sa.text("SELECT definition FROM agent_versions")).scalars():
        definition = json.loads(raw) if isinstance(raw, str) else raw
        brain = definition.get("brain") or {}
        keys.add(brain.get("profile", "smart"))
        keys.update(brain.get("fallback_profiles", []))
    return keys


def upgrade() -> None:
    bind = op.get_bind()
    keys = set(PRESETS) | _stored_profile_keys(bind)
    if any(
        not isinstance(key, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) is None
        for key in keys
    ):
        raise RuntimeError("Invalid stored brain profile identifier; reconcile it before upgrading")
    op.create_table(
        "brain_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "brain_profile_aliases",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        sa.ForeignKeyConstraint(["profile_id"], ["brain_profiles.id"]),
    )
    profiles = sa.table(
        "brain_profiles",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("deleted", sa.Boolean),
        sa.column("revision", sa.Integer),
    )
    # 连历史 Agent/Run 中的合法自定义标识一起登记，不改写任何快照。
    op.bulk_insert(
        profiles,
        [
            {"key": key, "name": key, "description": "", "deleted": False, "revision": 0}
            for key in sorted(keys)
        ],
    )
    # 当前 key 也登记，所有新旧标识在同一主键空间中仲裁并发冲突。
    bind.execute(
        sa.text(
            "INSERT INTO brain_profile_aliases (`key`, profile_id) "
            "SELECT `key`, id FROM brain_profiles"
        )
    )
    for table, column in [("brain_mappings", "profile"), ("runs", "brain_profile")]:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, existing_type=sa.String(16), type_=sa.String(64), existing_nullable=False
            )


def downgrade() -> None:
    bind = op.get_bind()
    # 旧版本只识别四个标识；拒绝破坏性降级，不截断或偷偷丢弃自定义槽位。
    aliases = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM brain_profile_aliases a JOIN brain_profiles p "
            "ON p.id = a.profile_id WHERE a.`key` <> p.`key`"
        )
    ).scalar_one()
    rows = bind.execute(
        sa.text("SELECT `key`, name, description, deleted FROM brain_profiles")
    ).all()
    if (
        aliases
        or any(
            key not in PRESETS or name != key or description or deleted
            for key, name, description, deleted in rows
        )
        or not _stored_profile_keys(bind) <= set(PRESETS)
    ):
        raise RuntimeError(
            "Cannot downgrade editable brain profiles with custom, edited, "
            "renamed or deleted slots; "
            "back up and reconcile them first"
        )
    for table, column in [("runs", "brain_profile"), ("brain_mappings", "profile")]:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, existing_type=sa.String(64), type_=sa.String(16), existing_nullable=False
            )
    op.drop_table("brain_profile_aliases")
    op.drop_table("brain_profiles")
