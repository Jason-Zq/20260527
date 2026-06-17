"""新增移民档案 schema：clients 主表加列 + 新建 family_members + 新建 assets。

Revision ID: 005_immigration_schema
Revises: 004_split_tasks
Create Date: 2026-06-15

重点：
- clients 主表加 ~28 个新列（联系/护照/教育/工作/婚姻/业务/client_code），全部 nullable，向后兼容
- family_members 新表（25 字段）：配偶/子女/父母/紧急联系人共用
- assets 新表（17 字段）：房产/存款/银行流水/其他共用
- 不改 client_info / documents / templates / template_fills / split_tasks
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '005_immigration_schema'
down_revision: Union[str, None] = '004_split_tasks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============== 1) clients 主表加列 ==============
    op.add_column('clients', sa.Column('client_code', sa.String(length=30), nullable=True, comment='客户编号（手动填入）'))

    # 身份扩展
    op.add_column('clients', sa.Column('name_en', sa.String(length=100), nullable=True))
    op.add_column('clients', sa.Column('former_name', sa.String(length=100), nullable=True))
    op.add_column('clients', sa.Column('birth_place', sa.String(length=200), nullable=True))
    op.add_column('clients', sa.Column('ethnicity', sa.String(length=50), nullable=True))
    op.add_column('clients', sa.Column('marital_status', sa.String(length=20), nullable=True))
    op.add_column('clients', sa.Column('hukou_address', sa.String(length=300), nullable=True))

    # 联系方式
    op.add_column('clients', sa.Column('phone', sa.String(length=30), nullable=True))
    op.add_column('clients', sa.Column('email', sa.String(length=100), nullable=True))
    op.add_column('clients', sa.Column('current_address', sa.String(length=300), nullable=True))

    # 护照
    op.add_column('clients', sa.Column('passport_no', sa.String(length=50), nullable=True))
    op.add_column('clients', sa.Column('passport_issue_date', sa.Date(), nullable=True))
    op.add_column('clients', sa.Column('passport_expiry_date', sa.Date(), nullable=True))
    op.add_column('clients', sa.Column('passport_issuing_authority', sa.String(length=100), nullable=True))

    # 教育
    op.add_column('clients', sa.Column('school_name', sa.String(length=200), nullable=True))
    op.add_column('clients', sa.Column('school_name_en', sa.String(length=200), nullable=True))
    op.add_column('clients', sa.Column('major', sa.String(length=100), nullable=True))
    op.add_column('clients', sa.Column('degree', sa.String(length=50), nullable=True))
    op.add_column('clients', sa.Column('graduation_date', sa.Date(), nullable=True))
    op.add_column('clients', sa.Column('graduation_cert_no', sa.String(length=50), nullable=True))
    op.add_column('clients', sa.Column('degree_cert_no', sa.String(length=50), nullable=True))

    # 工作
    op.add_column('clients', sa.Column('company_name', sa.String(length=200), nullable=True))
    op.add_column('clients', sa.Column('position', sa.String(length=100), nullable=True))
    op.add_column('clients', sa.Column('employment_start_date', sa.Date(), nullable=True))
    op.add_column('clients', sa.Column('monthly_salary', sa.Numeric(precision=12, scale=2), nullable=True))

    # 婚姻
    op.add_column('clients', sa.Column('marriage_date', sa.Date(), nullable=True))
    op.add_column('clients', sa.Column('marriage_authority', sa.String(length=100), nullable=True))
    op.add_column('clients', sa.Column('marriage_cert_no', sa.String(length=50), nullable=True))

    # 业务
    op.add_column('clients', sa.Column('visa_type', sa.String(length=50), nullable=True, comment='业务类型标签'))

    # 唯一约束 + 索引
    op.create_unique_constraint('uq_clients_client_code', 'clients', ['client_code'])
    op.create_index('ix_clients_passport_expiry', 'clients', ['passport_expiry_date'])
    op.create_index('ix_clients_visa_type', 'clients', ['visa_type'])

    # ============== 2) family_members 新表 ==============
    op.create_table(
        'family_members',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        # 基本
        sa.Column('relation', sa.String(length=20), nullable=False, comment='配偶/子/女/父/母/紧急联系人'),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('name_en', sa.String(length=100), nullable=True),
        sa.Column('gender', sa.String(length=10), nullable=True),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('nationality', sa.String(length=50), nullable=True),
        sa.Column('id_number', sa.String(length=50), nullable=True),
        sa.Column('phone', sa.String(length=30), nullable=True),
        # POA 必需
        sa.Column('passport_no', sa.String(length=50), nullable=True),
        sa.Column('email', sa.String(length=100), nullable=True),
        sa.Column('current_address', sa.String(length=300), nullable=True),
        sa.Column('company_name', sa.String(length=200), nullable=True),
        sa.Column('position', sa.String(length=100), nullable=True),
        # 配偶教育
        sa.Column('school_name', sa.String(length=200), nullable=True),
        sa.Column('school_name_en', sa.String(length=200), nullable=True),
        sa.Column('major', sa.String(length=100), nullable=True),
        sa.Column('degree', sa.String(length=50), nullable=True),
        sa.Column('graduation_date', sa.Date(), nullable=True),
        sa.Column('graduation_cert_no', sa.String(length=50), nullable=True),
        sa.Column('degree_cert_no', sa.String(length=50), nullable=True),
        # 子女出生
        sa.Column('birth_cert_no', sa.String(length=50), nullable=True),
        sa.Column('birth_hospital', sa.String(length=200), nullable=True),
        sa.Column('birth_place', sa.String(length=200), nullable=True),
        # 其他
        sa.Column('will_accompany', sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_family_members_client_relation', 'family_members', ['client_id', 'relation'])

    # ============== 3) assets 新表 ==============
    op.create_table(
        'assets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('asset_type', sa.String(length=20), nullable=False, comment='房产/存款/银行流水/股票/车辆/其他'),
        sa.Column('asset_name', sa.String(length=300), nullable=True),
        sa.Column('owner_name', sa.String(length=100), nullable=True),
        sa.Column('co_owners', sa.String(length=300), nullable=True),
        sa.Column('value_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=True),
        sa.Column('certificate_no', sa.String(length=50), nullable=True),
        # 房产专用
        sa.Column('location_address', sa.String(length=300), nullable=True),
        sa.Column('area_sqm', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('usage_type', sa.String(length=20), nullable=True),
        sa.Column('acquired_date', sa.Date(), nullable=True),
        # 银行专用
        sa.Column('bank_name', sa.String(length=100), nullable=True),
        sa.Column('account_no', sa.String(length=50), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=True),
        sa.Column('period_end', sa.Date(), nullable=True),
        sa.Column('frozen_until', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_assets_client_type', 'assets', ['client_id', 'asset_type'])


def downgrade() -> None:
    # 倒序删除
    op.drop_index('ix_assets_client_type', table_name='assets')
    op.drop_table('assets')

    op.drop_index('ix_family_members_client_relation', table_name='family_members')
    op.drop_table('family_members')

    # clients 索引
    op.drop_index('ix_clients_visa_type', table_name='clients')
    op.drop_index('ix_clients_passport_expiry', table_name='clients')
    op.drop_constraint('uq_clients_client_code', 'clients', type_='unique')

    # clients 列（与 upgrade 反序）
    cols = [
        'visa_type',
        'marriage_cert_no', 'marriage_authority', 'marriage_date',
        'monthly_salary', 'employment_start_date', 'position', 'company_name',
        'degree_cert_no', 'graduation_cert_no', 'graduation_date', 'degree', 'major', 'school_name_en', 'school_name',
        'passport_issuing_authority', 'passport_expiry_date', 'passport_issue_date', 'passport_no',
        'current_address', 'email', 'phone',
        'hukou_address', 'marital_status', 'ethnicity', 'birth_place', 'former_name', 'name_en',
        'client_code',
    ]
    for c in cols:
        op.drop_column('clients', c)
