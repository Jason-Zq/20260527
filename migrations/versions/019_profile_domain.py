"""add profile domain tables (households/persons/person_fields/assets/cases) + review columns

客户画像 v2:独立领域模型(不再写 clients/family_members)+ 复核闭环 + 原件留存。
方案:画像流水线写 profile_* 新表;老表仅软关联(legacy_client_id),互不影响。
两条不变原则:customer_files.ocr_text 原文留存、ai_api_calls 全量记录。

Revision ID: 019_profile_domain
Revises: 018_customer_profile
Create Date: 2026-07-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '019_profile_domain'
down_revision: Union[str, None] = '018_customer_profile'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 家庭/客户组 ----
    op.create_table('profile_households',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False, comment='家庭/客户组名称(主客户姓名)'),
        sa.Column('legacy_client_id', sa.Integer(), nullable=True, comment='软关联老 clients.id(仅链接,不写老表)'),
        sa.Column('main_person_id', sa.Integer(), nullable=True, comment='主申请人 profile_persons.id'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.ForeignKeyConstraint(['legacy_client_id'], ['clients.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_profile_households_name', 'profile_households', ['name'], unique=False)

    # ---- 人(骨架) ----
    op.create_table('profile_persons',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('household_id', sa.Integer(), nullable=False, comment='所属家庭'),
        sa.Column('name', sa.String(length=100), nullable=False, comment='姓名'),
        sa.Column('relation_to_main', sa.String(length=20), nullable=False, comment='与主申请人关系:户主/配偶/子/女/父/母/待确认'),
        sa.Column('is_main', sa.Boolean(), nullable=False, comment='是否主申请人'),
        sa.Column('avatar_file_id', sa.BigInteger(), nullable=True, comment='头像证件照 customer_files.id'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.ForeignKeyConstraint(['household_id'], ['profile_households.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_profile_persons_household', 'profile_persons', ['household_id'], unique=False)

    # ---- 字段级档案 + 证据链 ----
    op.create_table('profile_person_fields',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False, comment='所属人'),
        sa.Column('field', sa.String(length=50), nullable=False, comment='字段名:如 id_number/occupation'),
        sa.Column('value', sa.Text(), nullable=True, comment='当前值'),
        sa.Column('layer', sa.String(length=16), nullable=False, comment='可信度层:verified=官方证件/declared=自报'),
        sa.Column('source_file_id', sa.BigInteger(), nullable=True, comment='来源文件 customer_files.id'),
        sa.Column('source_result_id', sa.BigInteger(), nullable=True, comment='来源提取结果 doc_extract_results.id'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='ai(待复核)/confirmed/corrected'),
        sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最后操作人(AI 或复核员)'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.ForeignKeyConstraint(['person_id'], ['profile_persons.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('person_id', 'field', name='ux_profile_person_fields_person_field')
    )
    op.create_index('ix_profile_person_fields_person', 'profile_person_fields', ['person_id'], unique=False)

    # ---- 资产 ----
    op.create_table('profile_assets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('household_id', sa.Integer(), nullable=False, comment='所属家庭'),
        sa.Column('owner_person_id', sa.Integer(), nullable=True, comment='权属人'),
        sa.Column('asset_type', sa.String(length=30), nullable=False, comment='房产/存款/银行流水/股票/车辆/其他'),
        sa.Column('name', sa.String(length=200), nullable=False, comment='资产名称'),
        sa.Column('attrs', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='类型特定字段{地址,面积,证号,金额,币种,...}'),
        sa.Column('source_file_id', sa.BigInteger(), nullable=True, comment='来源文件 customer_files.id'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='ai(待复核)/confirmed/corrected'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.ForeignKeyConstraint(['household_id'], ['profile_households.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_person_id'], ['profile_persons.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_profile_assets_household', 'profile_assets', ['household_id'], unique=False)

    # ---- 案件 + 时间线 ----
    op.create_table('profile_cases',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('household_id', sa.Integer(), nullable=False, comment='所属家庭'),
        sa.Column('case_type', sa.String(length=100), nullable=False, comment='案件类型:如 瓦努阿图永居'),
        sa.Column('status', sa.String(length=30), nullable=False, comment='进行中/已获批/已签收/停滞'),
        sa.Column('milestones', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='时间线 [{name,date,source_file_id}]'),
        sa.Column('created_at', sa.DateTime(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, comment='更新时间'),
        sa.ForeignKeyConstraint(['household_id'], ['profile_households.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_profile_cases_household', 'profile_cases', ['household_id'], unique=False)

    # ---- customer_files 加列:原件留存 + 复核 ----
    op.add_column('customer_files', sa.Column('local_path', sa.String(length=500), nullable=True, comment='原件落盘相对路径(output/customer_files/...)'))
    op.add_column('customer_files', sa.Column('file_keep_until', sa.DateTime(), nullable=True, comment='原件保留截止时间(到期 GC 删除;DB/OCR 永久保留)'))
    op.add_column('customer_files', sa.Column('review_status', sa.String(length=16), nullable=False, server_default='none', comment='复核状态:none/needs_review/reviewed'))
    op.add_column('customer_files', sa.Column('review_reason', sa.String(length=200), nullable=True, comment='待复核原因:no_text/garbled/ocr_short/low_confidence/extract_error/no_person/masked_id'))
    op.add_column('customer_files', sa.Column('quality_score', sa.Integer(), nullable=True, comment='质量分 0-100(越小越急需复核)'))
    op.create_index('ix_customer_files_review', 'customer_files', ['review_status', 'quality_score'], unique=False)

    # ---- doc_extract_results 加列:复核 ----
    op.add_column('doc_extract_results', sa.Column('review_status', sa.String(length=16), nullable=False, server_default='pending', comment='复核状态:pending/confirmed/corrected/dismissed'))
    op.add_column('doc_extract_results', sa.Column('corrected', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='人工修正后的字段(留痕,与 extracted 对照)'))
    op.add_column('doc_extract_results', sa.Column('reviewed_by', sa.String(length=64), nullable=True, comment='复核人'))
    op.add_column('doc_extract_results', sa.Column('reviewed_at', sa.DateTime(), nullable=True, comment='复核时间'))

    # ---- profile_import_tasks 关联家庭 ----
    op.add_column('profile_import_tasks', sa.Column('household_id', sa.Integer(), nullable=True, comment='生成的家庭'))
    op.add_column('profile_import_tasks', sa.Column('needs_review_count', sa.Integer(), nullable=False, server_default='0', comment='待复核文件数'))
    op.create_foreign_key('fk_profile_import_tasks_household', 'profile_import_tasks', 'profile_households', ['household_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_profile_import_tasks_household', 'profile_import_tasks', type_='foreignkey')
    op.drop_column('profile_import_tasks', 'needs_review_count')
    op.drop_column('profile_import_tasks', 'household_id')
    op.drop_column('doc_extract_results', 'reviewed_at')
    op.drop_column('doc_extract_results', 'reviewed_by')
    op.drop_column('doc_extract_results', 'corrected')
    op.drop_column('doc_extract_results', 'review_status')
    op.drop_index('ix_customer_files_review', table_name='customer_files')
    op.drop_column('customer_files', 'quality_score')
    op.drop_column('customer_files', 'review_reason')
    op.drop_column('customer_files', 'review_status')
    op.drop_column('customer_files', 'file_keep_until')
    op.drop_column('customer_files', 'local_path')
    op.drop_index('ix_profile_cases_household', table_name='profile_cases')
    op.drop_table('profile_cases')
    op.drop_index('ix_profile_assets_household', table_name='profile_assets')
    op.drop_table('profile_assets')
    op.drop_index('ix_profile_person_fields_person', table_name='profile_person_fields')
    op.drop_table('profile_person_fields')
    op.drop_index('ix_profile_persons_household', table_name='profile_persons')
    op.drop_table('profile_persons')
    op.drop_index('ix_profile_households_name', table_name='profile_households')
    op.drop_table('profile_households')
