"""drop doc_extract_rules table (rules moved to code constant backend/extract_rules.py)

提取规则已从 DB 迁至代码常量维护:改规则=改 backend/extract_rules.py + 重启 worker。
原 draft/activate/disable 生命周期撤销;提取结果 doc_extract_results 表及复核闭环不受影响。

Revision ID: 020_drop_extract_rules
Revises: 019_profile_domain
Create Date: 2026-07-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '020_drop_extract_rules'
down_revision: Union[str, None] = '019_profile_domain'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ux_doc_extract_rules_active', table_name='doc_extract_rules')
    op.drop_index('ix_doc_extract_rules_type_status', table_name='doc_extract_rules')
    op.drop_table('doc_extract_rules')


def downgrade() -> None:
    op.create_table('doc_extract_rules',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('doc_type', sa.String(length=32), nullable=False, comment='证件类型'),
        sa.Column('version', sa.Integer(), nullable=False, comment='同 doc_type 内递增'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='draft', comment='draft/active/disabled'),
        sa.Column('fields', postgresql.JSONB(), nullable=False, comment='字段定义数组'),
        sa.Column('prompt_extra', sa.Text(), nullable=True, comment='类型级注意事项'),
        sa.Column('drafted_by', sa.String(length=16), nullable=False, server_default='ai', comment='ai/human'),
        sa.Column('reviewed_by', sa.String(length=64), nullable=True, comment='审核人'),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True, comment='审核时间'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_doc_extract_rules_type_status', 'doc_extract_rules', ['doc_type', 'status'], unique=False)
    op.create_index('ux_doc_extract_rules_active', 'doc_extract_rules', ['doc_type'], unique=True,
                    postgresql_where=sa.text("status = 'active'"))