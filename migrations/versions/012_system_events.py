"""业务事件流表 system_events。

Revision ID: 012_system_events
Revises: 011_client_profile_generation
Create Date: 2026-06-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '012_system_events'
down_revision: Union[str, None] = '011_client_profile_generation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'system_events',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('occurred_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='事件发生时间'),
        sa.Column('severity', sa.String(length=10), nullable=False, comment='info | warn | error | critical'),
        sa.Column('category', sa.String(length=40), nullable=False, comment='事件类别'),
        sa.Column('message', sa.String(length=500), nullable=False, comment='中文人话描述,不含堆栈'),
        sa.Column('context', JSONB(), nullable=True, comment='结构化字段 batch_id/file_id/error_class 等'),
        sa.CheckConstraint(
            "severity IN ('info','warn','error','critical')",
            name='system_events_severity_check',
        ),
    )
    op.create_index(
        'ix_system_events_occurred',
        'system_events',
        [sa.text('occurred_at DESC')],
    )
    op.create_index(
        'ix_system_events_severity_occurred',
        'system_events',
        ['severity', sa.text('occurred_at DESC')],
    )
    op.create_index(
        'ix_system_events_category_occurred',
        'system_events',
        ['category', sa.text('occurred_at DESC')],
    )
    # 业务方常按 batch_id 查相关事件,索引 JSONB 表达式
    op.execute("""
        CREATE INDEX ix_system_events_batch_id
        ON system_events ((context->>'batch_id'))
        WHERE context ? 'batch_id'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_system_events_batch_id")
    op.drop_index('ix_system_events_category_occurred', table_name='system_events')
    op.drop_index('ix_system_events_severity_occurred', table_name='system_events')
    op.drop_index('ix_system_events_occurred', table_name='system_events')
    op.drop_table('system_events')
