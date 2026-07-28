"""customer_files add person_id (manual file-to-person attribution)

文件归属页(/file-assign)与复核修正确认归属人后,归属关系落到 customer_files.person_id,
作为"文件↔人"关联的权威载体(此前只寄生在 doc_extract_results.write_stats JSONB,
无提取结果的文件没有载体)。list_person_files/完备度矩阵均改为 列 ∪ write_stats 并集。

Revision ID: 021_customer_files_person_id
Revises: 020_drop_extract_rules
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '021_customer_files_person_id'
down_revision: Union[str, None] = '020_drop_extract_rules'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('customer_files', sa.Column('person_id', sa.Integer(), nullable=True,
                                              comment='归属人(手动指定;权威归属载体,与 write_stats 归因并集使用)'))
    op.create_index('ix_customer_files_person', 'customer_files', ['person_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_customer_files_person', table_name='customer_files')
    op.drop_column('customer_files', 'person_id')
