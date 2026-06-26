"""客户资料结构化生成任务表。

Revision ID: 011_client_profile_generation
Revises: 010_archive_ocr_text
Create Date: 2026-06-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '011_client_profile_generation'
down_revision: Union[str, None] = '010_archive_ocr_text'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'client_profile_generation_tasks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('client_id', sa.Integer(), nullable=False, comment='客户 ID'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='running', comment='running|done|error'),
        sa.Column('source_file_ids', JSONB(), nullable=True, comment='本次使用的 archive_detect_files.id 数组'),
        sa.Column('source_files_snapshot', JSONB(), nullable=True, comment='本次使用文件的摘要快照'),
        sa.Column('source_file_count', sa.Integer(), nullable=False, server_default='0', comment='本次使用文件数'),
        sa.Column('extracted_summary', JSONB(), nullable=True, comment='AI 抽取汇总结果'),
        sa.Column('created_count', JSONB(), nullable=True, comment='写入数量统计'),
        sa.Column('error', sa.Text(), nullable=True, comment='错误信息'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_client_profile_generation_client', 'client_profile_generation_tasks', ['client_id'])
    op.create_index('ix_client_profile_generation_status', 'client_profile_generation_tasks', ['status'])
    op.create_index('ix_client_profile_generation_created', 'client_profile_generation_tasks', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_client_profile_generation_created', table_name='client_profile_generation_tasks')
    op.drop_index('ix_client_profile_generation_status', table_name='client_profile_generation_tasks')
    op.drop_index('ix_client_profile_generation_client', table_name='client_profile_generation_tasks')
    op.drop_table('client_profile_generation_tasks')
