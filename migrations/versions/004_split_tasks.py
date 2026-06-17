"""新增 split_tasks 表:持久化 PDF 拆分任务的元数据与拆分结果。

Revision ID: 004_split_tasks
Revises: 003_template_v2
Create Date: 2026-06-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '004_split_tasks'
down_revision: Union[str, None] = '003_template_v2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'split_tasks',
        sa.Column('task_id', sa.String(length=200), nullable=False, comment='任务ID,对应 output/ 目录名'),
        sa.Column('filename', sa.String(length=500), nullable=False, comment='原始上传文件名'),
        sa.Column('total_pages', sa.Integer(), nullable=True, comment='原 PDF 总页数'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='ocr',
                  comment='ocr|llm|splitting|done|error'),
        sa.Column('error', sa.Text(), nullable=True, comment='error 状态时的失败信息'),
        sa.Column('ranges', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='拆分结果 ranges 数组,每项 {idx,doc_type,page_start,page_end,filename,fields}'),
        sa.Column('duration_sec', sa.Float(), nullable=True, comment='upload→done 总耗时(秒)'),
        sa.Column('files_cleaned', sa.Boolean(), nullable=False, server_default=sa.false(),
                  comment='7 天清理后置 true,文件已删但记录留'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.PrimaryKeyConstraint('task_id'),
    )
    op.create_index('ix_split_tasks_status', 'split_tasks', ['status'])
    op.create_index('ix_split_tasks_created_at', 'split_tasks', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_split_tasks_created_at', table_name='split_tasks')
    op.drop_index('ix_split_tasks_status', table_name='split_tasks')
    op.drop_table('split_tasks')
