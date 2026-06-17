"""summaries 加进展名称 + 相关性判断 4 列。

Revision ID: 007_summary_relevance
Revises: 006_summaries
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '007_summary_relevance'
down_revision: Union[str, None] = '006_summaries'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('summaries', sa.Column('progress_name', sa.String(length=200), nullable=True,
                                          comment='用户输入的进展名称（如：美国EB5-资金来源证明）'))
    op.add_column('summaries', sa.Column('relevance', sa.String(length=20), nullable=True,
                                          comment='strong/weak/unrelated'))
    op.add_column('summaries', sa.Column('relevance_score', sa.Integer(), nullable=True,
                                          comment='0-100'))
    op.add_column('summaries', sa.Column('relevance_reason', sa.Text(), nullable=True))
    op.create_index('ix_summaries_progress_name', 'summaries', ['progress_name'])


def downgrade() -> None:
    op.drop_index('ix_summaries_progress_name', table_name='summaries')
    op.drop_column('summaries', 'relevance_reason')
    op.drop_column('summaries', 'relevance_score')
    op.drop_column('summaries', 'relevance')
    op.drop_column('summaries', 'progress_name')
