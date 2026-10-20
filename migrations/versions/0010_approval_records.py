"""approvals 审批记录表（R11 遗留项：审批落库，进程重启不丢决定、可审计）

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-10-15 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f5a6b7c8d9e0'
down_revision: str | None = 'e4f5a6b7c8d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'approvals',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('run_id', sa.String(length=36), nullable=False),
        sa.Column('tool_key', sa.String(length=128), nullable=False),
        sa.Column('args', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('decided_by', sa.String(length=128), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_approvals_run_id', 'approvals', ['run_id'])
    op.create_index('ix_approvals_status', 'approvals', ['status'])


def downgrade() -> None:
    op.drop_index('ix_approvals_status', table_name='approvals')
    op.drop_index('ix_approvals_run_id', table_name='approvals')
    op.drop_table('approvals')
