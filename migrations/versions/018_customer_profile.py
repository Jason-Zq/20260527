"""add customer profile tables (profile_import_tasks / customer_files / doc_extract_rules / doc_extract_results)

客户画像:Excel 文件清单导入 → 全量 OCR 入客户文件库 → 筛 4 类证件 → 按库中规则提取 → 归因写入客户档案。
方案见 docs/09-客户画像-Excel导入方案.md。

Revision ID: 018_customer_profile
Revises: 4617b534a2d2
Create Date: 2026-07-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '018_customer_profile'
down_revision: Union[str, None] = '4617b534a2d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 导入任务 ----
    op.create_table('profile_import_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('filename', sa.String(length=500), nullable=False, comment='上传的 Excel 文件名'),
        sa.Column('client_name', sa.String(length=100), nullable=False, comment='主客户姓名(客户姓名列众数)'),
        sa.Column('client_id', sa.Integer(), nullable=True, comment='归属客户'),
        sa.Column('status', sa.String(length=20), nullable=False, comment='running/done/error'),
        sa.Column('total_files', sa.Integer(), nullable=False, comment='文件总数'),
        sa.Column('processed_files', sa.Integer(), nullable=False, comment='已处理数(含成败)'),
        sa.Column('reused_count', sa.Integer(), nullable=False, comment='复用 archive_detect 脱敏 OCR 的文件数'),
        sa.Column('relinked_count', sa.Integer(), nullable=False, comment='命中本库已有 done 行直接复用的文件数'),
        sa.Column('fresh_ocr_count', sa.Integer(), nullable=False, comment='新下载+OCR 的文件数'),
        sa.Column('failed_count', sa.Integer(), nullable=False, comment='失败文件数'),
        sa.Column('extracted_count', sa.Integer(), nullable=False, comment='完成提取的文件数'),
        sa.Column('id_card_count', sa.Integer(), nullable=False, comment='筛出身份证数'),
        sa.Column('hukou_count', sa.Integer(), nullable=False, comment='筛出户口本数'),
        sa.Column('degree_cert_count', sa.Integer(), nullable=False, comment='筛出学位证数'),
        sa.Column('birth_cert_count', sa.Integer(), nullable=False, comment='筛出出生证明数'),
        sa.Column('current_file', sa.String(length=500), nullable=True, comment='正在处理的文件名'),
        sa.Column('error', sa.Text(), nullable=True, comment='任务级错误信息'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_profile_import_tasks_status', 'profile_import_tasks', ['status'], unique=False)
    op.create_index('ix_profile_import_tasks_client', 'profile_import_tasks', ['client_id'], unique=False)
    op.create_index('ix_profile_import_tasks_created', 'profile_import_tasks', [sa.literal_column('created_at DESC')], unique=False)

    # ---- 客户文件库 ----
    op.create_table('customer_files',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('file_code', sa.String(length=200), nullable=False, comment='业务文件编码(全局唯一)'),
        sa.Column('import_task_id', sa.Integer(), nullable=False, comment='最近一次导入它的任务'),
        sa.Column('client_name', sa.String(length=100), nullable=True, comment='Excel 行级客户姓名'),
        sa.Column('client_id', sa.Integer(), nullable=True, comment='归属客户'),
        sa.Column('filename', sa.String(length=500), nullable=True, comment='文件名'),
        sa.Column('folder_name', sa.String(length=300), nullable=True, comment='售后文件夹名称'),
        sa.Column('rel_path', sa.String(length=500), nullable=True, comment='相对路径'),
        sa.Column('status', sa.String(length=20), nullable=False, comment='pending/fetching/ocr/done/error'),
        sa.Column('ocr_source', sa.String(length=10), nullable=False, comment='fresh(原文)/reused(脱敏)/none'),
        sa.Column('ocr_text', sa.Text(), nullable=True, comment='OCR 全文;fresh=未脱敏原文(与 ai_api_calls 存原文的既定业务决策一致),reused=archive_detect 脱敏文本'),
        sa.Column('mime_type', sa.String(length=100), nullable=True, comment='MIME 类型'),
        sa.Column('page_count', sa.Integer(), nullable=True, comment='页数'),
        sa.Column('char_count', sa.Integer(), nullable=True, comment='字符数'),
        sa.Column('doc_type', sa.String(length=32), nullable=True, comment='识别类型:id_card/hukou/degree_cert/birth_cert/other'),
        sa.Column('classify_by', sa.String(length=10), nullable=False, comment='分类方式:keyword/llm/none'),
        sa.Column('classify_score', sa.Integer(), nullable=True, comment='matcher 分数或 LLM confidence'),
        sa.Column('error_msg', sa.Text(), nullable=True, comment='错误信息'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['import_task_id'], ['profile_import_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('file_code', name='ux_customer_files_file_code')
    )
    op.create_index('ix_customer_files_task', 'customer_files', ['import_task_id'], unique=False)
    op.create_index('ix_customer_files_doc_type', 'customer_files', ['doc_type'], unique=False)
    op.create_index('ix_customer_files_client', 'customer_files', ['client_id'], unique=False)
    op.create_index('ix_customer_files_status', 'customer_files', ['status'], unique=False)

    # ---- 提取规则 ----
    op.create_table('doc_extract_rules',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('doc_type', sa.String(length=32), nullable=False, comment='证件类型:id_card/hukou/degree_cert/birth_cert'),
        sa.Column('version', sa.Integer(), nullable=False, comment='同 doc_type 内递增,从 1 开始'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='draft/active/disabled;每 doc_type 至多一条 active'),
        sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='字段定义数组 [{key,label,description,required,target:{entity,column},example}]'),
        sa.Column('prompt_extra', sa.Text(), nullable=True, comment='追加到抽取 prompt 的类型级注意事项'),
        sa.Column('drafted_by', sa.String(length=16), nullable=False, comment='ai/human'),
        sa.Column('reviewed_by', sa.String(length=64), nullable=True, comment='审核人'),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True, comment='审核时间'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_doc_extract_rules_type_status', 'doc_extract_rules', ['doc_type', 'status'], unique=False)
    # 每 doc_type 至多一条 active(DB 层兜底)
    op.create_index('ux_doc_extract_rules_active', 'doc_extract_rules', ['doc_type'], unique=True,
                    postgresql_where=sa.text("status = 'active'"))

    # ---- 提取结果 ----
    op.create_table('doc_extract_results',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('customer_file_id', sa.BigInteger(), nullable=False, comment='来源客户文件行'),
        sa.Column('import_task_id', sa.Integer(), nullable=False, comment='所属导入任务'),
        sa.Column('file_id', sa.String(length=200), nullable=True, comment='业务文件编码'),
        sa.Column('client_id', sa.Integer(), nullable=True, comment='归属客户'),
        sa.Column('doc_type', sa.String(length=32), nullable=False, comment='识别出的证件类型'),
        sa.Column('rule_id', sa.BigInteger(), nullable=True, comment='使用的规则;无 active 规则时 NULL'),
        sa.Column('rule_version', sa.Integer(), nullable=True, comment='规则版本'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='done/error/skipped'),
        sa.Column('skip_reason', sa.String(length=64), nullable=True, comment='no_active_rule/no_client/no_person 等'),
        sa.Column('extracted', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='LLM 抽取原始字段(未脱敏,同 ai_api_calls 原始留存策略)'),
        sa.Column('mapped', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='逐字段写入明细 [{key,column,entity,entity_id,action}]'),
        sa.Column('write_stats', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='{matched_by, client_fields, member_fields, member_created}'),
        sa.Column('error_msg', sa.Text(), nullable=True, comment='错误信息'),
        sa.Column('elapsed_ms', sa.Integer(), nullable=True, comment='耗时毫秒'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.ForeignKeyConstraint(['customer_file_id'], ['customer_files.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['import_task_id'], ['profile_import_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_doc_extract_results_task', 'doc_extract_results', ['import_task_id'], unique=False)
    op.create_index('ix_doc_extract_results_customer_file', 'doc_extract_results', ['customer_file_id'], unique=False)
    op.create_index('ix_doc_extract_results_file_id', 'doc_extract_results', ['file_id'], unique=False)
    op.create_index('ix_doc_extract_results_doc_type', 'doc_extract_results', ['doc_type'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_doc_extract_results_doc_type', table_name='doc_extract_results')
    op.drop_index('ix_doc_extract_results_file_id', table_name='doc_extract_results')
    op.drop_index('ix_doc_extract_results_customer_file', table_name='doc_extract_results')
    op.drop_index('ix_doc_extract_results_task', table_name='doc_extract_results')
    op.drop_table('doc_extract_results')
    op.drop_index('ux_doc_extract_rules_active', table_name='doc_extract_rules')
    op.drop_index('ix_doc_extract_rules_type_status', table_name='doc_extract_rules')
    op.drop_table('doc_extract_rules')
    op.drop_index('ix_customer_files_status', table_name='customer_files')
    op.drop_index('ix_customer_files_client', table_name='customer_files')
    op.drop_index('ix_customer_files_doc_type', table_name='customer_files')
    op.drop_index('ix_customer_files_task', table_name='customer_files')
    op.drop_table('customer_files')
    op.drop_index('ix_profile_import_tasks_created', table_name='profile_import_tasks')
    op.drop_index('ix_profile_import_tasks_client', table_name='profile_import_tasks')
    op.drop_index('ix_profile_import_tasks_status', table_name='profile_import_tasks')
    op.drop_table('profile_import_tasks')
