"""export_failures 加 retry_count（重试消费端的尝试计数）

Revision ID: c1d2e3f4a5b6
Revises: b8c9d0e1f2a3
Create Date: 2026-09-09 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'b8c9d0e1f2a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 已有行回填 0（新行由 ORM default 兜底），失败记录初始均为「未尝试重试」
    op.add_column(
        'export_failures',
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )


def downgrade() -> None:
    # batch 写法兜底 SQLite 的 DROP COLUMN 版本差异（同 0004 惯例）
    with op.batch_alter_table('export_failures') as batch:
        batch.drop_column('retry_count')
