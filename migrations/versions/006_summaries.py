"""新增 summaries 表：通用文件摘要历史。

Revision ID: 006_summaries
Revises: 005_immigration_schema
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '006_summaries'
down_revision: Union[str, None] = '005_immigration_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'summaries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('url', sa.Text(), nullable=False, comment='原始文件 URL'),
        sa.Column('filename', sa.String(length=500), nullable=True),
        sa.Column('mime_type', sa.String(length=100), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=True,
                  comment='pdf_text/pdf_ocr/image_ocr/docx_text'),
        sa.Column('page_count', sa.Integer(), nullable=True),
        sa.Column('char_count', sa.Integer(), nullable=True),
        sa.Column('extracted_text', sa.Text(), nullable=True, comment='OCR/抽取的全文'),
        sa.Column('title', sa.String(length=300), nullable=True, comment='LLM 生成的一句话定性'),
        sa.Column('summary', sa.Text(), nullable=True, comment='LLM 摘要正文'),
        sa.Column('key_points', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('doc_category', sa.String(length=50), nullable=True),
        sa.Column('elapsed_sec', sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='done'),
        sa.Column('error_msg', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_summaries_created_at', 'summaries', ['created_at'])
    op.create_index('ix_summaries_doc_category', 'summaries', ['doc_category'])


def downgrade() -> None:
    op.drop_index('ix_summaries_doc_category', table_name='summaries')
    op.drop_index('ix_summaries_created_at', table_name='summaries')
    op.drop_table('summaries')
