"""project cases: 售后项目字段入库 + 案件升级为按项目多案件

业务方接口 getAfterCustomerAllFiles 返回一户多项目(嵌套 list[]),项目级字段
(affter_entryoid/projectno/projectname/projectno_detailed/projectname_detailed)
此前在清单拍平时全部丢弃。本迁移:
1. profile_households 加 customer_code/crm_oid(只补空,姓名键不动)。
2. customer_files 加 affter_entryoid(项目案件路由键)+ project_name(反范式展示列)。
3. profile_cases 升级为「一个售后项目=一个案件」:加项目 6 列;
   affter_entryoid IS NULL = 默认案件(承接历史数据/扁平形态文件)。
   两个部分唯一索引:同家庭同 entryoid 唯一 + 同家庭默认案件唯一
   (PG 唯一索引视 NULL 互不相同,普通 UNIQUE 约束不住默认案件)。
4. 防御性数据守卫:同家庭多个 NULL-entryoid 旧案件合并为一条(正常库零命中)。

Revision ID: 022_project_cases
Revises: 021_customer_files_person_id
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '022_project_cases'
down_revision: Union[str, None] = '021_customer_files_person_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 家庭:客户编号 + CRM OID
    op.add_column('profile_households', sa.Column(
        'customer_code', sa.String(100), nullable=True,
        comment='业务方客户编号(首个非空值,只补空)'))
    op.add_column('profile_households', sa.Column(
        'crm_oid', sa.String(64), nullable=True,
        comment='业务方 CRM OID(只补空)'))

    # 文件:项目路由键 + 反范式展示列
    op.add_column('customer_files', sa.Column(
        'affter_entryoid', sa.String(64), nullable=True,
        comment='售后项目OID(项目案件路由键);NULL=扁平形态/旧数据'))
    op.add_column('customer_files', sa.Column(
        'project_name', sa.String(300), nullable=True,
        comment='项目显示名(反范式: projectname_detailed || projectname)'))
    op.create_index('ix_customer_files_entryoid', 'customer_files', ['affter_entryoid'], unique=False)

    # 案件:升级为项目案件
    op.add_column('profile_cases', sa.Column(
        'affter_entryoid', sa.String(64), nullable=True,
        comment='售后项目OID;NULL=默认案件(旧数据/扁平形态路由)'))
    op.add_column('profile_cases', sa.Column('projectno', sa.String(50), nullable=True,
                                             comment='办理的项目编码'))
    op.add_column('profile_cases', sa.Column('projectname', sa.String(200), nullable=True,
                                             comment='办理的项目名称'))
    op.add_column('profile_cases', sa.Column('projectno_detailed', sa.String(50), nullable=True,
                                             comment='二级项目编码'))
    op.add_column('profile_cases', sa.Column('projectname_detailed', sa.String(200), nullable=True,
                                             comment='二级项目名称'))
    op.add_column('profile_cases', sa.Column('project_created_at', sa.DateTime(), nullable=True,
                                             comment='项目创建时间(接口 create_time)'))

    # 防御性守卫:同家庭多个默认案件(NULL entryoid)合并为一条(保留 max(id),里程碑按 name 并集,同名取较新案件值)
    op.execute("""
        DO $$
        DECLARE
            r RECORD;
            keep_id INT;
            merged JSONB;
        BEGIN
            FOR r IN
                SELECT household_id FROM profile_cases
                WHERE affter_entryoid IS NULL
                GROUP BY household_id HAVING COUNT(*) > 1
            LOOP
                keep_id := (SELECT max(id) FROM profile_cases
                            WHERE household_id = r.household_id AND affter_entryoid IS NULL);
                SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb) INTO merged FROM (
                    SELECT DISTINCT ON (elem->>'name') elem
                    FROM profile_cases c,
                         LATERAL jsonb_array_elements(COALESCE(c.milestones, '[]'::jsonb)) elem
                    WHERE c.household_id = r.household_id AND c.affter_entryoid IS NULL
                    ORDER BY elem->>'name', c.id DESC
                ) t;
                UPDATE profile_cases SET milestones = merged, updated_at = now() WHERE id = keep_id;
                DELETE FROM profile_cases
                WHERE household_id = r.household_id AND affter_entryoid IS NULL AND id <> keep_id;
            END LOOP;
        END $$;
    """)

    op.create_index('ux_profile_cases_household_entryoid', 'profile_cases',
                    ['household_id', 'affter_entryoid'], unique=True,
                    postgresql_where=sa.text('affter_entryoid IS NOT NULL'))
    op.create_index('ux_profile_cases_household_default', 'profile_cases',
                    ['household_id'], unique=True,
                    postgresql_where=sa.text('affter_entryoid IS NULL'))


def downgrade() -> None:
    op.drop_index('ux_profile_cases_household_default', table_name='profile_cases')
    op.drop_index('ux_profile_cases_household_entryoid', table_name='profile_cases')
    op.drop_column('profile_cases', 'project_created_at')
    op.drop_column('profile_cases', 'projectname_detailed')
    op.drop_column('profile_cases', 'projectno_detailed')
    op.drop_column('profile_cases', 'projectname')
    op.drop_column('profile_cases', 'projectno')
    op.drop_column('profile_cases', 'affter_entryoid')
    op.drop_index('ix_customer_files_entryoid', table_name='customer_files')
    op.drop_column('customer_files', 'project_name')
    op.drop_column('customer_files', 'affter_entryoid')
    op.drop_column('profile_households', 'crm_oid')
    op.drop_column('profile_households', 'customer_code')
