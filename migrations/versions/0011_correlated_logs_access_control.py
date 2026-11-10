"""统一关联日志表与平台访问控制表。

Revision ID: a1b2c3d4e5f6
Revises: f5a6b7c8d9e0
Create Date: 2026-11-10 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("logger", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("exception", sa.Text(), nullable=True),
        sa.Column("pathname", sa.String(length=1024), nullable=False),
        sa.Column("lineno", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("parent_run_id", sa.String(length=64), nullable=True),
        sa.Column("root_run_id", sa.String(length=64), nullable=True),
        sa.Column("subject", sa.String(length=128), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_application_logs_session_time", ["session_id", "created_at", "id"]),
        ("ix_application_logs_run_time", ["run_id", "created_at", "id"]),
        ("ix_application_logs_root_time", ["root_run_id", "created_at", "id"]),
        ("ix_application_logs_trace_time", ["trace_id", "created_at", "id"]),
        ("ix_application_logs_request_time", ["request_id", "created_at", "id"]),
        ("ix_application_logs_created_at", ["created_at"]),
    ):
        op.create_index(name, "application_logs", columns)

    op.create_table(
        "permissions",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "roles",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("protected", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_key", sa.String(length=64), nullable=False),
        sa.Column("permission_key", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["permission_key"], ["permissions.key"]),
        sa.ForeignKeyConstraint(["role_key"], ["roles.key"]),
        sa.PrimaryKeyConstraint("role_key", "permission_key"),
        sa.UniqueConstraint("role_key", "permission_key"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("bootstrap", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role_key", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["role_key"], ["roles.key"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "role_key"),
        sa.UniqueConstraint("user_id", "role_key"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])


def downgrade() -> None:
    # MySQL needs this user_id index while its FK exists; table deletion removes
    # both atomically, so do not drop the supporting index separately.
    op.drop_table("auth_sessions")
    op.drop_table("user_roles")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("permissions")
    for name in (
        "ix_application_logs_created_at",
        "ix_application_logs_request_time",
        "ix_application_logs_trace_time",
        "ix_application_logs_root_time",
        "ix_application_logs_run_time",
        "ix_application_logs_session_time",
    ):
        op.drop_index(name, table_name="application_logs")
    op.drop_table("application_logs")
