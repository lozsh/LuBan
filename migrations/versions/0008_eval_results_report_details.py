"""eval_results 评测报告明细列（R5 评测体系打通）

Revision ID: d3e4f5a6b7c8
Revises: c1d2e3f4a5b6
Create Date: 2026-09-09 12:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd3e4f5a6b7c8'
down_revision: str | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # score → pass_rate（语义钉死为通过率）；details → report（完整 EvalReport 序列化）；
    # 追加列表页聚合列。旧行 report 即原 details（CLI 已写全量报告 JSON），
    # 聚合列 server_default 0 兜底（历史行可在下次评测覆盖刷新）。
    # batch 写法兜底 SQLite 的列变更版本差异（同 0004/0007 惯例）
    with op.batch_alter_table('eval_results') as batch:
        batch.alter_column('score', new_column_name='pass_rate', existing_type=sa.Float())
        batch.alter_column('details', new_column_name='report', existing_type=sa.JSON())
        batch.add_column(
            sa.Column('passed_cases', sa.Integer(), nullable=False, server_default=sa.text('0'))
        )
        batch.add_column(
            sa.Column('total_cases', sa.Integer(), nullable=False, server_default=sa.text('0'))
        )
        batch.add_column(
            sa.Column(
                'wrong_answer_rate', sa.Float(), nullable=False, server_default=sa.text('0.0')
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('eval_results') as batch:
        batch.drop_column('wrong_answer_rate')
        batch.drop_column('total_cases')
        batch.drop_column('passed_cases')
        batch.alter_column('report', new_column_name='details', existing_type=sa.JSON())
        batch.alter_column('pass_rate', new_column_name='score', existing_type=sa.Float())
