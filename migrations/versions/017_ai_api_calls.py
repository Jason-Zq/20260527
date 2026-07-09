"""add ai_api_calls table

Revision ID: 4617b534a2d2
Revises: 016_external_api_logs
Create Date: 2026-07-07 09:35:53.348856

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4617b534a2d2'
down_revision: Union[str, None] = '016_external_api_logs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ai_api_calls',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='调用时间'),
        sa.Column('operation', sa.String(length=64), nullable=True, comment='LLM wrapper/操作名,如 detect_archival'),
        sa.Column('model', sa.String(length=64), nullable=True, comment='模型 ID'),
        sa.Column('prompt', sa.Text(), nullable=True, comment='prompt 全文'),
        sa.Column('response_raw', sa.Text(), nullable=True, comment='原始返回文本'),
        sa.Column('status', sa.String(length=10), nullable=False, comment='ok | error'),
        sa.Column('error_msg', sa.Text(), nullable=True, comment='错误信息'),
        sa.Column('elapsed_ms', sa.Integer(), nullable=True, comment='耗时毫秒'),
        sa.Column('batch_id', sa.String(length=40), nullable=True, comment='关联批次'),
        sa.Column('file_id', sa.String(length=64), nullable=True, comment='关联业务文件编码'),
        sa.Column('client_code', sa.String(length=40), nullable=True, comment='关联客户编码'),
        sa.Column('task_id', sa.String(length=64), nullable=True, comment='关联任务/摘要 ID'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ai_api_calls_batch_id_created', 'ai_api_calls', ['batch_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_ai_api_calls_client_code_created', 'ai_api_calls', ['client_code', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_ai_api_calls_created', 'ai_api_calls', [sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_ai_api_calls_file_id_created', 'ai_api_calls', ['file_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_ai_api_calls_model_created', 'ai_api_calls', ['model', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_ai_api_calls_operation_created', 'ai_api_calls', ['operation', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_ai_api_calls_status_created', 'ai_api_calls', ['status', sa.literal_column('created_at DESC')], unique=False)


def downgrade() -> None:
    op.drop_index('ix_ai_api_calls_status_created', table_name='ai_api_calls')
    op.drop_index('ix_ai_api_calls_operation_created', table_name='ai_api_calls')
    op.drop_index('ix_ai_api_calls_model_created', table_name='ai_api_calls')
    op.drop_index('ix_ai_api_calls_file_id_created', table_name='ai_api_calls')
    op.drop_index('ix_ai_api_calls_created', table_name='ai_api_calls')
    op.drop_index('ix_ai_api_calls_client_code_created', table_name='ai_api_calls')
    op.drop_index('ix_ai_api_calls_batch_id_created', table_name='ai_api_calls')
    op.drop_table('ai_api_calls')
