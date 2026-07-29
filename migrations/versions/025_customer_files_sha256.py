"""customer files content sha256: 同家庭跨项目重复文件的内容级去重

同一文件在不同售后项目下 file_code 不同,按编号去重拦不住(实测约占 1/3 文件量)。
下载后算 sha256,命中同家庭已 OCR 行直接复用 OCR/分类,跳过重复 OCR。

Revision ID: 025_customer_files_sha256
Revises: 024_person_name_folded_unique
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '025_customer_files_sha256'
down_revision: Union[str, None] = '024_person_name_folded_unique'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('customer_files',
                  sa.Column('content_sha256', sa.String(length=64), nullable=True,
                            comment='原件内容 sha256(同家庭跨项目重复文件的内容级去重)'))
    op.create_index('ix_customer_files_sha256', 'customer_files', ['content_sha256'])


def downgrade() -> None:
    op.drop_index('ix_customer_files_sha256', table_name='customer_files')
    op.drop_column('customer_files', 'content_sha256')
