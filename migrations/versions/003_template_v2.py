"""模板表结构改造：fields→placeholders，field_values→placeholder_values（无业务数据，drop+recreate）

Revision ID: 003_template_v2
Revises: 002_templates
Create Date: 2026-06-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '003_template_v2'
down_revision: Union[str, None] = '002_templates'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 旧表无业务数据，直接 drop + recreate（语义变化大，比 ALTER 更干净）
    op.drop_index('ix_template_fills_client', table_name='template_fills')
    op.drop_index('ix_template_fills_template', table_name='template_fills')
    op.drop_table('template_fills')
    op.drop_table('templates')

    op.create_table(
        'templates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False, comment='模板名称（用户命名）'),
        sa.Column('filename', sa.String(length=500), nullable=True, comment='原始上传文件名'),
        sa.Column('file_path', sa.String(length=500), nullable=True, comment='docx 模板存储相对路径'),
        sa.Column('placeholders', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='占位符列表 [{id,description,original_text}]'),
        sa.Column('created_by', sa.String(length=100), nullable=True, comment='上传人（预留）'),
        sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'template_fills',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False, comment='关联模板'),
        sa.Column('client_id', sa.Integer(), nullable=True, comment='关联客户（手动填写时为空）'),
        sa.Column('placeholder_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='占位符值快照 {strN: value}'),
        sa.Column('output_pdf', sa.String(length=500), nullable=True, comment='生成的 PDF 路径'),
        sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['template_id'], ['templates.id']),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
    )
    op.create_index('ix_template_fills_template', 'template_fills', ['template_id'])
    op.create_index('ix_template_fills_client', 'template_fills', ['client_id'])


def downgrade() -> None:
    op.drop_index('ix_template_fills_client', table_name='template_fills')
    op.drop_index('ix_template_fills_template', table_name='template_fills')
    op.drop_table('template_fills')
    op.drop_table('templates')
