"""API 请求记录表 api_request_logs。

Revision ID: 014_api_request_logs
Revises: 013_worker_lease
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '014_api_request_logs'
down_revision: Union[str, None] = '013_worker_lease'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'api_request_logs',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('method', sa.String(length=10), nullable=False, comment='GET/POST'),
        sa.Column('path', sa.String(length=300), nullable=False, comment='请求路径'),
        sa.Column('client_ip', sa.String(length=45), nullable=True),
        sa.Column('request_body', JSONB(), nullable=True, comment='传参 JSON(multipart 只存元数据不存文件)'),
        sa.Column('response_status', sa.Integer(), nullable=True, comment='HTTP 状态码'),
        sa.Column('elapsed_ms', sa.Integer(), nullable=True, comment='请求耗时毫秒'),
    )
    op.create_index('ix_request_logs_created', 'api_request_logs', [sa.text('created_at DESC')])
    op.create_index('ix_request_logs_path', 'api_request_logs', ['path', sa.text('created_at DESC')])


def downgrade() -> None:
    op.drop_index('ix_request_logs_path', table_name='api_request_logs')
    op.drop_index('ix_request_logs_created', table_name='api_request_logs')
    op.drop_table('api_request_logs')
