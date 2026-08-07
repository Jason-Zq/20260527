"""027_drop_legacy_client_archive

删除旧客户档案体系（2026-08，「AI 材料解析」页内嵌客户档案功能整体下线）：

1. archive_detect_progress 客户信息自包含：
   - 加 client_code/client_name（NOT NULL DEFAULT ''），从 clients 回填
     （client_code 用 COALESCE(NULLIF(...),'legacy_'||id) 防空编码撞新唯一索引）；
   - 删旧唯一索引 (client_id, progress_oid) 与普通索引、删 client_id 列；
   - 建唯一索引 (client_code, progress_oid) + client_code 普通索引。
2. template_fills 改挂画像人员：加 person_id(FK→profile_persons) + 索引；
   client_id 去 FK 留列（仅留历史值）；删 ix_template_fills_client 索引。
3. 画像侧历史软关联去 FK 留列：profile_import_tasks.client_id /
   customer_files.client_id / profile_households.legacy_client_id。
4. 物理删除 6 张旧表（含数据，不可逆，用户已确认不迁移不备份）：
   client_info → documents → assets → family_members →
   client_profile_generation_tasks → clients。

Revision ID: 027_drop_legacy_client_archive
Revises: 026_archive_detect_prompts
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '027_drop_legacy_client_archive'
down_revision: Union[str, None] = '026_archive_detect_prompts'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- 1. archive_detect_progress 客户信息自包含 ----------
    op.add_column('archive_detect_progress',
                  sa.Column('client_code', sa.String(length=100), nullable=False,
                            server_default='',
                            comment='客户编码（业务方稳定编码；旧数据回填自 clients）'))
    op.add_column('archive_detect_progress',
                  sa.Column('client_name', sa.String(length=200), nullable=False,
                            server_default='',
                            comment='客户姓名（提交时快照）'))
    # 回填：client_code 空编码兜底 'legacy_<id>'，避免撞 (client_code, progress_oid) 唯一索引
    op.execute("""
        UPDATE archive_detect_progress p
           SET client_code = COALESCE(NULLIF(c.client_code, ''), 'legacy_' || c.id),
               client_name = c.name
          FROM clients c
         WHERE p.client_id = c.id
    """)
    op.drop_index('ux_archive_detect_progress_client_oid', table_name='archive_detect_progress')
    op.drop_index('ix_archive_detect_progress_client', table_name='archive_detect_progress')
    op.drop_column('archive_detect_progress', 'client_id')  # FK 随列一并删除
    op.create_index('ux_archive_detect_progress_client_oid', 'archive_detect_progress',
                    ['client_code', 'progress_oid'], unique=True)
    op.create_index('ix_archive_detect_progress_client', 'archive_detect_progress',
                    ['client_code'])

    # ---------- 2. template_fills 改挂画像人员 ----------
    op.add_column('template_fills',
                  sa.Column('person_id', sa.Integer(), nullable=True,
                            comment='关联画像人员(手动填写时为空)'))
    op.create_foreign_key(None, 'template_fills', 'profile_persons',
                          ['person_id'], ['id'])
    op.create_index('ix_template_fills_person', 'template_fills', ['person_id'])
    op.drop_constraint('template_fills_client_id_fkey', 'template_fills', type_='foreignkey')
    op.drop_index('ix_template_fills_client', table_name='template_fills')

    # ---------- 3. 画像侧历史软关联去 FK 留列 ----------
    op.drop_constraint('profile_import_tasks_client_id_fkey', 'profile_import_tasks',
                       type_='foreignkey')
    op.drop_constraint('customer_files_client_id_fkey', 'customer_files', type_='foreignkey')
    op.drop_constraint('profile_households_legacy_client_id_fkey', 'profile_households',
                       type_='foreignkey')

    # ---------- 4. 按依赖序物理删除 6 张旧表（含数据，不可逆） ----------
    op.drop_table('client_info')
    op.drop_table('documents')
    op.drop_table('assets')
    op.drop_table('family_members')
    op.drop_table('client_profile_generation_tasks')
    op.drop_table('clients')


def downgrade() -> None:
    """重建 6 张旧表（空表）并恢复列/索引/FK。数据不可恢复。"""
    # ---------- 4'. 重建旧表 ----------
    op.create_table(
        'clients',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_code', sa.String(length=30), nullable=True, comment='客户编号（手动填入）'),
        sa.Column('name', sa.String(length=100), nullable=False, comment='客户姓名'),
        sa.Column('name_en', sa.String(length=100), nullable=True, comment='拼音/英文名'),
        sa.Column('former_name', sa.String(length=100), nullable=True, comment='曾用名'),
        sa.Column('gender', sa.String(length=10), nullable=True, comment='性别'),
        sa.Column('birth_date', sa.Date(), nullable=True, comment='出生日期'),
        sa.Column('birth_place', sa.String(length=200), nullable=True, comment='出生地'),
        sa.Column('ethnicity', sa.String(length=50), nullable=True, comment='民族'),
        sa.Column('nationality', sa.String(length=50), nullable=True, comment='国籍'),
        sa.Column('id_number', sa.String(length=50), nullable=True, comment='身份证号'),
        sa.Column('hukou_address', sa.String(length=300), nullable=True, comment='户籍地址'),
        sa.Column('marital_status', sa.String(length=20), nullable=True, comment='婚姻状况'),
        sa.Column('phone', sa.String(length=30), nullable=True, comment='手机'),
        sa.Column('email', sa.String(length=100), nullable=True, comment='邮箱'),
        sa.Column('current_address', sa.String(length=300), nullable=True, comment='现家庭住址'),
        sa.Column('passport_no', sa.String(length=50), nullable=True, comment='护照号'),
        sa.Column('passport_issue_date', sa.Date(), nullable=True, comment='护照签发日期'),
        sa.Column('passport_expiry_date', sa.Date(), nullable=True, comment='护照到期日期'),
        sa.Column('passport_issuing_authority', sa.String(length=100), nullable=True, comment='护照签发机关'),
        sa.Column('school_name', sa.String(length=200), nullable=True, comment='学校名'),
        sa.Column('school_name_en', sa.String(length=200), nullable=True, comment='英文校名'),
        sa.Column('major', sa.String(length=100), nullable=True, comment='专业'),
        sa.Column('degree', sa.String(length=50), nullable=True, comment='学位'),
        sa.Column('graduation_date', sa.Date(), nullable=True, comment='毕业日期（NULL=在读）'),
        sa.Column('graduation_cert_no', sa.String(length=50), nullable=True, comment='毕业证编号'),
        sa.Column('degree_cert_no', sa.String(length=50), nullable=True, comment='学位证编号'),
        sa.Column('company_name', sa.String(length=200), nullable=True, comment='公司名'),
        sa.Column('position', sa.String(length=100), nullable=True, comment='职位'),
        sa.Column('employment_start_date', sa.Date(), nullable=True, comment='入职日期'),
        sa.Column('monthly_salary', sa.Numeric(12, 2), nullable=True, comment='月薪'),
        sa.Column('marriage_date', sa.Date(), nullable=True, comment='结婚登记日期'),
        sa.Column('marriage_authority', sa.String(length=100), nullable=True, comment='结婚登记机关'),
        sa.Column('marriage_cert_no', sa.String(length=50), nullable=True, comment='结婚证编号'),
        sa.Column('visa_type', sa.String(length=50), nullable=True, comment='业务类型标签'),
        sa.Column('consultant', sa.String(length=100), nullable=True, comment='所属顾问（保留兼容）'),
        sa.Column('notes', sa.Text(), nullable=True, comment='备注'),
        sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_code'),
        sa.UniqueConstraint('id_number'),
    )
    op.create_index('ix_clients_passport_expiry', 'clients', ['passport_expiry_date'])
    op.create_index('ix_clients_visa_type', 'clients', ['visa_type'])

    op.create_table(
        'documents',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=True, comment='关联客户（可为空）'),
        sa.Column('task_id', sa.String(length=200), nullable=False, comment='任务ID，对应 output/ 目录名'),
        sa.Column('filename', sa.String(length=500), nullable=False, comment='原始文件名'),
        sa.Column('doc_type', sa.String(length=50), nullable=True, comment='证件类型'),
        sa.Column('file_path', sa.String(length=500), nullable=True, comment='文件存储相对路径'),
        sa.Column('ocr_text', sa.Text(), nullable=True, comment='OCR 全文'),
        sa.Column('extracted_fields', postgresql.JSONB(), nullable=True, comment='AI 提取的结构化字段'),
        sa.Column('confidence_avg', sa.Float(), nullable=True, comment='平均置信度'),
        sa.Column('reviewed', sa.Boolean(), nullable=True, comment='是否已人工复核'),
        sa.Column('status', sa.String(length=20), nullable=True, comment='状态: ocr/llm/done/error'),
        sa.Column('error_msg', sa.Text(), nullable=True, comment='错误信息'),
        sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id'),
    )
    op.create_index('ix_documents_extracted_fields', 'documents', ['extracted_fields'],
                    postgresql_using='gin')
    op.create_index('ix_documents_status', 'documents', ['status'])
    op.create_index('ix_documents_created_at', 'documents', ['created_at'])

    op.create_table(
        'client_info',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False, comment='关联客户'),
        sa.Column('info_key', sa.String(length=100), nullable=False, comment='字段名（如：身份证号、有效期限）'),
        sa.Column('info_value', sa.Text(), nullable=True, comment='字段值'),
        sa.Column('source_doc_id', sa.Integer(), nullable=True, comment='来源文档'),
        sa.Column('valid_from', sa.Date(), nullable=True, comment='生效日期'),
        sa.Column('valid_until', sa.Date(), nullable=True, comment='到期日期（供定时任务用）'),
        sa.Column('confirmed', sa.Boolean(), nullable=True, comment='是否人工确认'),
        sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['source_doc_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_client_info_valid_until', 'client_info', ['valid_until'])
    op.create_index('ix_client_info_client_key', 'client_info', ['client_id', 'info_key'])

    op.create_table(
        'family_members',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False, comment='关联客户'),
        sa.Column('relation', sa.String(length=20), nullable=False, comment='配偶/子/女/父/母/紧急联系人'),
        sa.Column('name', sa.String(length=100), nullable=False, comment='姓名'),
        sa.Column('name_en', sa.String(length=100), nullable=True),
        sa.Column('gender', sa.String(length=10), nullable=True),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('nationality', sa.String(length=50), nullable=True),
        sa.Column('id_number', sa.String(length=50), nullable=True, comment='身份证号'),
        sa.Column('phone', sa.String(length=30), nullable=True),
        sa.Column('passport_no', sa.String(length=50), nullable=True),
        sa.Column('email', sa.String(length=100), nullable=True),
        sa.Column('current_address', sa.String(length=300), nullable=True),
        sa.Column('company_name', sa.String(length=200), nullable=True),
        sa.Column('position', sa.String(length=100), nullable=True),
        sa.Column('school_name', sa.String(length=200), nullable=True),
        sa.Column('school_name_en', sa.String(length=200), nullable=True),
        sa.Column('major', sa.String(length=100), nullable=True),
        sa.Column('degree', sa.String(length=50), nullable=True),
        sa.Column('graduation_date', sa.Date(), nullable=True),
        sa.Column('graduation_cert_no', sa.String(length=50), nullable=True),
        sa.Column('degree_cert_no', sa.String(length=50), nullable=True),
        sa.Column('birth_cert_no', sa.String(length=50), nullable=True, comment='出生医学证编号'),
        sa.Column('birth_hospital', sa.String(length=200), nullable=True, comment='出生医院'),
        sa.Column('birth_place', sa.String(length=200), nullable=True),
        sa.Column('will_accompany', sa.Boolean(), nullable=True, comment='是否随行'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_family_members_client_relation', 'family_members',
                    ['client_id', 'relation'])

    op.create_table(
        'assets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False, comment='关联客户'),
        sa.Column('asset_type', sa.String(length=20), nullable=False, comment='房产/存款/银行流水/股票/车辆/其他'),
        sa.Column('asset_name', sa.String(length=300), nullable=True, comment='资产名称（地址或银行+期次）'),
        sa.Column('owner_name', sa.String(length=100), nullable=True, comment='权利人/户名'),
        sa.Column('co_owners', sa.String(length=300), nullable=True, comment='共有人（房产）'),
        sa.Column('value_amount', sa.Numeric(18, 2), nullable=True, comment='金额或估值'),
        sa.Column('currency', sa.String(length=10), nullable=True, comment='币种'),
        sa.Column('certificate_no', sa.String(length=50), nullable=True, comment='产权证号/存单号/证明编号'),
        sa.Column('location_address', sa.String(length=300), nullable=True, comment='坐落地址'),
        sa.Column('area_sqm', sa.Numeric(10, 2), nullable=True, comment='面积（平米）'),
        sa.Column('usage_type', sa.String(length=20), nullable=True, comment='住宅/商业/工业'),
        sa.Column('acquired_date', sa.Date(), nullable=True, comment='取得日期'),
        sa.Column('bank_name', sa.String(length=100), nullable=True),
        sa.Column('account_no', sa.String(length=50), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=True, comment='起息日/流水起'),
        sa.Column('period_end', sa.Date(), nullable=True, comment='到期日/流水止'),
        sa.Column('frozen_until', sa.Date(), nullable=True, comment='冻结期'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_assets_client_type', 'assets', ['client_id', 'asset_type'])

    op.create_table(
        'client_profile_generation_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False, comment='客户ID'),
        sa.Column('status', sa.String(length=20), nullable=False, comment='running|done|error'),
        sa.Column('source_file_ids', postgresql.JSONB(), nullable=True,
                  comment='本次使用的 archive_detect_files.id 数组'),
        sa.Column('source_files_snapshot', postgresql.JSONB(), nullable=True,
                  comment='本次使用文件的摘要快照'),
        sa.Column('source_file_count', sa.Integer(), nullable=False, comment='本次使用文件数'),
        sa.Column('extracted_summary', postgresql.JSONB(), nullable=True, comment='AI 抽取汇总结果'),
        sa.Column('created_count', postgresql.JSONB(), nullable=True, comment='写入数量统计'),
        sa.Column('error', sa.Text(), nullable=True, comment='错误信息'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_client_profile_generation_client', 'client_profile_generation_tasks',
                    ['client_id'])
    op.create_index('ix_client_profile_generation_status', 'client_profile_generation_tasks',
                    ['status'])
    op.create_index('ix_client_profile_generation_created', 'client_profile_generation_tasks',
                    ['created_at'])

    # ---------- 3'. 画像侧软关联恢复 FK ----------
    op.create_foreign_key('profile_households_legacy_client_id_fkey', 'profile_households',
                          'clients', ['legacy_client_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('customer_files_client_id_fkey', 'customer_files', 'clients',
                          ['client_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('profile_import_tasks_client_id_fkey', 'profile_import_tasks',
                          'clients', ['client_id'], ['id'], ondelete='SET NULL')

    # ---------- 2'. template_fills 还原 ----------
    op.create_index('ix_template_fills_client', 'template_fills', ['client_id'])
    op.create_foreign_key('template_fills_client_id_fkey', 'template_fills', 'clients',
                          ['client_id'], ['id'])
    op.drop_index('ix_template_fills_person', table_name='template_fills')
    op.drop_constraint('template_fills_person_id_fkey', 'template_fills', type_='foreignkey')
    op.drop_column('template_fills', 'person_id')

    # ---------- 1'. archive_detect_progress 还原 ----------
    op.drop_index('ix_archive_detect_progress_client', table_name='archive_detect_progress')
    op.drop_index('ux_archive_detect_progress_client_oid', table_name='archive_detect_progress')
    op.add_column('archive_detect_progress',
                  sa.Column('client_id', sa.Integer(), nullable=True, comment='关联客户'))
    op.create_foreign_key('archive_detect_progress_client_id_fkey', 'archive_detect_progress',
                          'clients', ['client_id'], ['id'])
    op.create_index('ix_archive_detect_progress_client', 'archive_detect_progress', ['client_id'])
    op.create_index('ux_archive_detect_progress_client_oid', 'archive_detect_progress',
                    ['client_id', 'progress_oid'], unique=True)
    op.drop_column('archive_detect_progress', 'client_name')
    op.drop_column('archive_detect_progress', 'client_code')
