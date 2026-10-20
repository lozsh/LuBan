"""token_usages 用量列改可空（H10：模型未回报 usage 时显式存 NULL，不用 0 冒充零消费）

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-14 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e4f5a6b7c8d9'
down_revision: str | None = 'd3e4f5a6b7c8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('token_usages') as batch:
        batch.alter_column('input_tokens', existing_type=sa.Integer(), nullable=True)
        batch.alter_column('output_tokens', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # 回填历史 NULL 为 0 后恢复 NOT NULL（降级语义：未知用量视为 0）
    op.execute('UPDATE token_usages SET input_tokens = 0 WHERE input_tokens IS NULL')
    op.execute('UPDATE token_usages SET output_tokens = 0 WHERE output_tokens IS NULL')
    with op.batch_alter_table('token_usages') as batch:
        batch.alter_column(
            'input_tokens', existing_type=sa.Integer(), nullable=False, server_default=sa.text('0')
        )
        batch.alter_column(
            'output_tokens', existing_type=sa.Integer(), nullable=False, server_default=sa.text('0')
        )
