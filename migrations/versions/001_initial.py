"""初始建表：clients, documents, client_info

Revision ID: 001_initial
Revises: 
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 创建 clients 表
    op.create_table(
        'clients',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False, comment='客户姓名'),
        sa.Column('id_number', sa.String(length=50), nullable=True, comment='证件号，用于去重匹配'),
        sa.Column('gender', sa.String(length=10), nullable=True, comment='性别'),
        sa.Column('birth_date', sa.Date(), nullable=True, comment='出生日期'),
        sa.Column('nationality', sa.String(length=50), nullable=True, comment='国籍'),
        sa.Column('consultant', sa.String(length=100), nullable=True, comment='所属顾问'),
        sa.Column('notes', sa.Text(), nullable=True, comment='备注'),
        sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_number'),
    )

    # 创建 documents 表
    op.create_table(
        'documents',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=True, comment='关联客户（可为空）'),
        sa.Column('task_id', sa.String(length=200), nullable=False, comment='任务ID，对应 output/ 目录名'),
        sa.Column('filename', sa.String(length=500), nullable=False, comment='原始文件名'),
        sa.Column('doc_type', sa.String(length=50), nullable=True, comment='证件类型'),
        sa.Column('file_path', sa.String(length=500), nullable=True, comment='文件存储相对路径'),
        sa.Column('ocr_text', sa.Text(), nullable=True, comment='OCR 全文'),
        sa.Column('extracted_fields', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='AI 提取的结构化字段'),
        sa.Column('confidence_avg', sa.Float(), nullable=True, comment='平均置信度'),
        sa.Column('reviewed', sa.Boolean(), nullable=True, comment='是否已人工复核'),
        sa.Column('status', sa.String(length=20), nullable=True, comment='状态: ocr/llm/done/error'),
        sa.Column('error_msg', sa.Text(), nullable=True, comment='错误信息'),
        sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.UniqueConstraint('task_id'),
    )
    # documents 索引
    op.create_index('ix_documents_extracted_fields', 'documents', ['extracted_fields'], postgresql_using='gin')
    op.create_index('ix_documents_status', 'documents', ['status'])
    op.create_index('ix_documents_created_at', 'documents', ['created_at'])

    # 创建 client_info 表
    op.create_table(
        'client_info',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False, comment='关联客户'),
        sa.Column('info_key', sa.String(length=100), nullable=False, comment='字段名'),
        sa.Column('info_value', sa.Text(), nullable=True, comment='字段值'),
        sa.Column('source_doc_id', sa.Integer(), nullable=True, comment='来源文档'),
        sa.Column('valid_from', sa.Date(), nullable=True, comment='生效日期'),
        sa.Column('valid_until', sa.Date(), nullable=True, comment='到期日期'),
        sa.Column('confirmed', sa.Boolean(), nullable=True, comment='是否人工确认'),
        sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['source_doc_id'], ['documents.id']),
    )
    # client_info 索引
    op.create_index('ix_client_info_valid_until', 'client_info', ['valid_until'])
    op.create_index('ix_client_info_client_key', 'client_info', ['client_id', 'info_key'])


def downgrade() -> None:
    op.drop_table('client_info')
    op.drop_table('documents')
    op.drop_table('clients')
