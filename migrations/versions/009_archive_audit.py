"""文件审核：新建进展包表 + 汇总表，batches/files 扩列支持增量与总体判断。

业务模型：调用方推送"进展包"（客户编码/姓名/办理人 + 项目/项目详情/进展 + 多文件）。
- archive_detect_progress: 进展包实体（办理人 + 项目/项目详情/进展 8 字段），(client_id, progress_oid) 唯一
- archive_detect_batches: 加 progress_id 关联 + overall_* 当次总体判断快照
- archive_detect_files: 加 progress_id/file_id/version/content_sha256/match_score/verdict/deleted 支持增量去重
- archive_detect_folder_summaries: 进展包维度滚动总体判断（多版本），建表不激活

Revision ID: 009_archive_audit
Revises: 008_archive_detect
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = '009_archive_audit'
down_revision: Union[str, None] = '008_archive_detect'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 新建进展包表
    op.create_table(
        'archive_detect_progress',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('client_id', sa.Integer(), nullable=False, comment='关联客户 clients.id'),
        sa.Column('handler', sa.String(length=100), nullable=True, comment='办理人（进展包属性，只存名字）'),
        sa.Column('project_name', sa.String(length=200), nullable=True, comment='项目名称'),
        sa.Column('project_code', sa.String(length=100), nullable=True, comment='项目编码'),
        sa.Column('project_detail_name', sa.String(length=200), nullable=True, comment='项目详情名称'),
        sa.Column('project_detail_code', sa.String(length=100), nullable=True, comment='项目详情编码'),
        sa.Column('progress_oid', sa.String(length=100), nullable=False, comment='进展OID（业务方标识）'),
        sa.Column('progress_name', sa.String(length=200), nullable=True, comment='进展名称'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
    )
    op.create_index('ux_archive_detect_progress_client_oid',
                    'archive_detect_progress', ['client_id', 'progress_oid'], unique=True)
    op.create_index('ix_archive_detect_progress_client',
                    'archive_detect_progress', ['client_id'])

    # 2. batches 扩列：进展包关联 + 当次总体判断快照
    op.add_column('archive_detect_batches',
                  sa.Column('progress_id', sa.Integer(), nullable=True, comment='关联进展包'))
    op.add_column('archive_detect_batches',
                  sa.Column('overall_verdict', sa.String(length=20), nullable=True,
                            comment='当次总体判断 match|partial|mismatch'))
    op.add_column('archive_detect_batches',
                  sa.Column('overall_score', sa.Integer(), nullable=True, comment='当次总体匹配度 0-100'))
    op.add_column('archive_detect_batches',
                  sa.Column('overall_reason', sa.Text(), nullable=True, comment='当次总体判断依据（脱敏后）'))
    op.create_foreign_key(
        'fk_archive_detect_batches_progress',
        'archive_detect_batches', 'archive_detect_progress',
        ['progress_id'], ['id'],
    )
    op.create_index('ix_archive_detect_batches_progress',
                    'archive_detect_batches', ['progress_id'])

    # 3. files 扩列：进展包关联 + 增量去重字段
    op.add_column('archive_detect_files',
                  sa.Column('progress_id', sa.Integer(), nullable=True, comment='关联进展包'))
    op.add_column('archive_detect_files',
                  sa.Column('file_id', sa.String(length=200), nullable=True,
                            comment='调用方传的显式文件标识（增量 key）'))
    op.add_column('archive_detect_files',
                  sa.Column('version', sa.Integer(), nullable=True, server_default='1',
                            comment='同 file_id 的检测版本号'))
    op.add_column('archive_detect_files',
                  sa.Column('content_sha256', sa.String(length=64), nullable=True, comment='文件内容哈希'))
    op.add_column('archive_detect_files',
                  sa.Column('match_score', sa.Integer(), nullable=True, comment='匹配度 0-100'))
    op.add_column('archive_detect_files',
                  sa.Column('verdict', sa.String(length=20), nullable=True,
                            comment='match|partial|mismatch'))
    op.add_column('archive_detect_files',
                  sa.Column('deleted', sa.Boolean(), nullable=True, server_default='false',
                            comment='软删标记'))
    op.create_foreign_key(
        'fk_archive_detect_files_progress',
        'archive_detect_files', 'archive_detect_progress',
        ['progress_id'], ['id'],
    )
    op.create_index('ix_archive_detect_files_progress',
                    'archive_detect_files', ['progress_id'])
    op.create_index('ix_archive_detect_files_fileid_version',
                    'archive_detect_files', ['progress_id', 'file_id', 'version'])
    op.create_index('ix_archive_detect_files_fileid_hash',
                    'archive_detect_files', ['progress_id', 'file_id', 'content_sha256'])
    op.create_index('ix_archive_detect_files_deleted',
                    'archive_detect_files', ['deleted'])

    # 4. 新建进展包维度汇总表（滚动总体判断，多版本）
    op.create_table(
        'archive_detect_folder_summaries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('progress_id', sa.Integer(), nullable=False, comment='关联进展包'),
        sa.Column('version', sa.Integer(), nullable=False, comment='汇总版本号'),
        sa.Column('criteria', sa.Text(), nullable=True, comment='本次汇总用的审核标准'),
        sa.Column('summary', JSONB(), nullable=True, comment='汇总内容（统计+LLM概述）'),
        sa.Column('file_count', sa.Integer(), nullable=True, comment='参与汇总的文件数'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['progress_id'], ['archive_detect_progress.id'],
            ondelete='CASCADE',
        ),
    )
    op.create_index('ux_archive_detect_summaries_progress_version',
                    'archive_detect_folder_summaries', ['progress_id', 'version'], unique=True)
    op.create_index('ix_archive_detect_summaries_progress_created',
                    'archive_detect_folder_summaries', ['progress_id', 'created_at'])


def downgrade() -> None:
    # 4
    op.drop_index('ix_archive_detect_summaries_progress_created', table_name='archive_detect_folder_summaries')
    op.drop_index('ux_archive_detect_summaries_progress_version', table_name='archive_detect_folder_summaries')
    op.drop_table('archive_detect_folder_summaries')

    # 3
    op.drop_index('ix_archive_detect_files_deleted', table_name='archive_detect_files')
    op.drop_index('ix_archive_detect_files_fileid_hash', table_name='archive_detect_files')
    op.drop_index('ix_archive_detect_files_fileid_version', table_name='archive_detect_files')
    op.drop_index('ix_archive_detect_files_progress', table_name='archive_detect_files')
    op.drop_constraint('fk_archive_detect_files_progress', 'archive_detect_files', type_='foreignkey')
    op.drop_column('archive_detect_files', 'deleted')
    op.drop_column('archive_detect_files', 'verdict')
    op.drop_column('archive_detect_files', 'match_score')
    op.drop_column('archive_detect_files', 'content_sha256')
    op.drop_column('archive_detect_files', 'version')
    op.drop_column('archive_detect_files', 'file_id')
    op.drop_column('archive_detect_files', 'progress_id')

    # 2
    op.drop_index('ix_archive_detect_batches_progress', table_name='archive_detect_batches')
    op.drop_constraint('fk_archive_detect_batches_progress', 'archive_detect_batches', type_='foreignkey')
    op.drop_column('archive_detect_batches', 'overall_reason')
    op.drop_column('archive_detect_batches', 'overall_score')
    op.drop_column('archive_detect_batches', 'overall_verdict')
    op.drop_column('archive_detect_batches', 'progress_id')

    # 1
    op.drop_index('ix_archive_detect_progress_client', table_name='archive_detect_progress')
    op.drop_index('ux_archive_detect_progress_client_oid', table_name='archive_detect_progress')
    op.drop_table('archive_detect_progress')
