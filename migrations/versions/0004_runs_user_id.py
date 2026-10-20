"""runs 加 user_id（透传观测标签，可空可索引，排查"哪个用户"用）

Revision ID: f1a2b3c4d5e6
Revises: dfee12ad7abc
Create Date: 2026-08-25 17:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: str | None = 'dfee12ad7abc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('user_id', sa.String(length=128), nullable=True))
    op.create_index(op.f('ix_runs_user_id'), 'runs', ['user_id'], unique=False)


def downgrade() -> None:
    # batch 写法兜底 SQLite 的 DROP COLUMN 版本差异
    with op.batch_alter_table('runs') as batch:
        batch.drop_index(op.f('ix_runs_user_id'))
        batch.drop_column('user_id')
