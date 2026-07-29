"""person name folded unique: (household_id, name_folded) 唯一索引(DB 层根治并发双卡)

前置条件:存量同名折叠重复已由 merge-duplicates 清零(023 只建非唯一索引)。
若仍有重复组,本迁移直接报错并列出 household 清单 —— 先跑
POST /api/profile/admin/merge-duplicates-all 再升级。
PG 唯一索引视 NULL 互不相同,name_folded IS NULL 行(部署间隙旧代码建的)天然放行。

Revision ID: 024_person_name_folded_unique
Revises: 023_person_name_folded
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '024_person_name_folded_unique'
down_revision: Union[str, None] = '023_person_name_folded'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dups = bind.execute(sa.text(
        'SELECT household_id, name_folded, COUNT(*) AS c FROM profile_persons '
        'WHERE name_folded IS NOT NULL GROUP BY 1, 2 HAVING COUNT(*) > 1')).fetchall()
    if dups:
        detail = ', '.join(f'household={h} fold={f} x{c}' for h, f, c in dups)
        raise Exception(
            f'[024] 仍有 {len(dups)} 组同名折叠重复,请先跑 '
            f'POST /api/profile/admin/merge-duplicates-all 再升级: {detail}')

    # 唯一索引覆盖同列,替换 023 的非唯一索引
    op.drop_index('ix_profile_persons_household_folded', table_name='profile_persons')
    op.create_index('ux_profile_persons_household_folded', 'profile_persons',
                    ['household_id', 'name_folded'], unique=True)


def downgrade() -> None:
    op.drop_index('ux_profile_persons_household_folded', table_name='profile_persons')
    op.create_index('ix_profile_persons_household_folded', 'profile_persons',
                    ['household_id', 'name_folded'], unique=False)
