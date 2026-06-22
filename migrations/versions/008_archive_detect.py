"""文件留底检测：新增 archive_detect_batches + archive_detect_files 两张表。

Revision ID: 008_archive_detect
Revises: 007_summary_relevance
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '008_archive_detect'
down_revision: Union[str, None] = '007_summary_relevance'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'archive_detect_batches',
        sa.Column('batch_id', sa.String(length=40), primary_key=True, comment='任务批次ID'),
        sa.Column('user_prompt', sa.Text(), nullable=False, comment='用户输入的留底判定标准（多行）'),
        sa.Column('source_kind', sa.String(length=10), nullable=False, comment='upload | url'),
        sa.Column('total_files', sa.Integer(), nullable=False, comment='文件总数（1-20）'),
        sa.Column('done_files', sa.Integer(), nullable=False, server_default='0', comment='已完成（含成功+失败）'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='running',
                  comment='running|done|error'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_archive_detect_batches_created_at',
                    'archive_detect_batches', ['created_at'])

    op.create_table(
        'archive_detect_files',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('batch_id', sa.String(length=40), nullable=False),
        sa.Column('idx', sa.Integer(), nullable=False, comment='文件顺序（0-based）'),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('filename', sa.String(length=500), nullable=True),
        sa.Column('mime_type', sa.String(length=100), nullable=True),
        sa.Column('page_count', sa.Integer(), nullable=True),
        sa.Column('char_count', sa.Integer(), nullable=True),
        sa.Column('is_archival', sa.Boolean(), nullable=True),
        sa.Column('confidence', sa.Integer(), nullable=True, comment='0-100'),
        sa.Column('reason', sa.Text(), nullable=True, comment='判定依据（已脱敏）'),
        sa.Column('key_points', JSONB(), nullable=True, comment='要点列表（已脱敏）'),
        sa.Column('doc_category', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending',
                  comment='pending|fetching|ocr|llm|done|error'),
        sa.Column('error_msg', sa.Text(), nullable=True),
        sa.Column('elapsed_sec', sa.Numeric(8, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['batch_id'], ['archive_detect_batches.batch_id'],
            ondelete='CASCADE',
        ),
    )
    op.create_index('ix_archive_detect_files_batch_id',
                    'archive_detect_files', ['batch_id'])
    op.create_index('ux_archive_detect_files_batch_idx',
                    'archive_detect_files', ['batch_id', 'idx'], unique=True)


def downgrade() -> None:
    op.drop_index('ux_archive_detect_files_batch_idx', table_name='archive_detect_files')
    op.drop_index('ix_archive_detect_files_batch_id', table_name='archive_detect_files')
    op.drop_table('archive_detect_files')
    op.drop_index('ix_archive_detect_batches_created_at', table_name='archive_detect_batches')
    op.drop_table('archive_detect_batches')
