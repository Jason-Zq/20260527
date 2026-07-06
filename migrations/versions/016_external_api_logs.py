"""新增 external_api_logs 表:记录出站外部接口调用(URL 刷新 / LLM)。

记录地址、请求参数、返回结果、耗时、成功/失败,供 /external-api-logs 页排查。
GC 保留 30 天,与 api_request_logs / system_events 一致。

Revision ID: 016_external_api_logs
Revises: 015_recheck_to_queue
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '016_external_api_logs'
down_revision: Union[str, None] = '015_recheck_to_queue'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'external_api_logs',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('service', sa.String(length=20), nullable=False,
                  comment='refresh_url | llm'),
        sa.Column('url', sa.Text(), nullable=True, comment='请求地址'),
        sa.Column('request_params', JSONB, nullable=True, comment='请求参数(LLM 存 prompt 全文)'),
        sa.Column('response_summary', JSONB, nullable=True, comment='返回结果(LLM 存返回全文)'),
        sa.Column('status', sa.String(length=10), nullable=False,
                  comment='ok | error'),
        sa.Column('error_msg', sa.Text(), nullable=True),
        sa.Column('elapsed_ms', sa.Integer(), nullable=True, comment='耗时毫秒'),
        sa.Column('batch_id', sa.String(length=40), nullable=True, comment='关联批次(如有)'),
        sa.Column('file_id', sa.String(length=64), nullable=True, comment='关联业务文件编码(如有)'),
    )
    op.create_index('ix_external_api_logs_created', 'external_api_logs',
                    [sa.text('created_at DESC')])
    op.create_index('ix_external_api_logs_service_created', 'external_api_logs',
                    ['service', sa.text('created_at DESC')])
    op.create_index('ix_external_api_logs_status_created', 'external_api_logs',
                    ['status', sa.text('created_at DESC')])


def downgrade() -> None:
    op.drop_index('ix_external_api_logs_status_created', table_name='external_api_logs')
    op.drop_index('ix_external_api_logs_service_created', table_name='external_api_logs')
    op.drop_index('ix_external_api_logs_created', table_name='external_api_logs')
    op.drop_table('external_api_logs')
