"""026_archive_detect_prompts

文件留底检测提示词库 + 批次总体判定2：

1. 建表 archive_detect_prompts——按（项目名称+项目编码+项目详情+项目详情编码+进展名称）
   五元组沉淀判定提示词：prompt1=批次总判模板(默认=代码内置模板,可编辑生效)、
   prompt2=AI 生成的项目专属留底标准(首次 finalize 自动生成)。五元组 NOT NULL DEFAULT ''
   （空值归一化为 ''，规避 PG 唯一索引对 NULL 放行的语义坑）。
2. archive_detect_batches 加 overall_verdict2/overall_score2/overall_reason2——
   提示词库驱动的第二次总体判定结果，与 overall1 并行互不干扰。

Revision ID: 026_archive_detect_prompts
Revises: 025_customer_files_sha256
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '026_archive_detect_prompts'
down_revision: Union[str, None] = '025_customer_files_sha256'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'archive_detect_prompts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_name', sa.String(length=200), nullable=False, server_default='',
                  comment='项目名称'),
        sa.Column('project_code', sa.String(length=100), nullable=False, server_default='',
                  comment='项目编码'),
        sa.Column('project_detail_name', sa.String(length=200), nullable=False, server_default='',
                  comment='项目详情名称'),
        sa.Column('project_detail_code', sa.String(length=100), nullable=False, server_default='',
                  comment='项目详情编码'),
        sa.Column('progress_name', sa.String(length=200), nullable=False, server_default='',
                  comment='进展名称'),
        sa.Column('prompt1', sa.Text(), nullable=True,
                  comment='提示词1：批次总体判定模板（含 {user_prompt}/{files_detail} 等占位 token）'),
        sa.Column('prompt2', sa.Text(), nullable=True,
                  comment='提示词2：项目专属留底标准（AI 生成，可手改）'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ux_archive_detect_prompts_key', 'archive_detect_prompts',
                    ['project_name', 'project_code', 'project_detail_name',
                     'project_detail_code', 'progress_name'], unique=True)
    op.create_index('ix_archive_detect_prompts_project_name', 'archive_detect_prompts',
                    ['project_name'])
    op.create_index('ix_archive_detect_prompts_progress_name', 'archive_detect_prompts',
                    ['progress_name'])

    op.add_column('archive_detect_batches',
                  sa.Column('overall_verdict2', sa.String(length=20), nullable=True,
                            comment='总体判定2(提示词库项目标准) match|partial|mismatch'))
    op.add_column('archive_detect_batches',
                  sa.Column('overall_score2', sa.Integer(), nullable=True,
                            comment='总体判定2 符合程度 0-100'))
    op.add_column('archive_detect_batches',
                  sa.Column('overall_reason2', sa.Text(), nullable=True,
                            comment='总体判定2 判断依据（脱敏后）'))


def downgrade() -> None:
    op.drop_column('archive_detect_batches', 'overall_reason2')
    op.drop_column('archive_detect_batches', 'overall_score2')
    op.drop_column('archive_detect_batches', 'overall_verdict2')

    op.drop_index('ix_archive_detect_prompts_progress_name', table_name='archive_detect_prompts')
    op.drop_index('ix_archive_detect_prompts_project_name', table_name='archive_detect_prompts')
    op.drop_index('ux_archive_detect_prompts_key', table_name='archive_detect_prompts')
    op.drop_table('archive_detect_prompts')
