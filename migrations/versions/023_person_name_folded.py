"""person name folded: 建卡去重折叠键(加列+回填+非唯一索引)

profile_persons 此前无任何唯一约束,同名(间隔号/空白微差异)或并发重复导入会建双卡。
本迁移:
1. profile_persons 加 name_folded 列(person_name_fold 口径:CJK=繁→简+去空白/间隔号,
   拉丁=大写词序无关;两字母表不相交,空存 NULL)。
2. 按当前口径回填存量行(内联自包含 fold 逻辑,不 import 后端可变代码;opencc 是运行依赖)。
3. 建非唯一索引(查询/upsert 用)。
唯一约束不在本迁移落地:存量双卡会导致唯一索引创建失败。顺序:
023 → 部署代码 → POST /api/profile/admin/merge-duplicates-all 清存量 → SQL 确认零重复 → 024。

Revision ID: 023_person_name_folded
Revises: 022_project_cases
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '023_person_name_folded'
down_revision: Union[str, None] = '022_project_cases'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---- 内联 fold 逻辑(快照 backend/db/profile_crud.py person_name_fold 当前口径,勿 import 后端) ----
import re as _re

_opencc_t2s = None  # 懒加载单例


def _fold(value) -> str:
    global _opencc_t2s
    if not value:
        return ""
    if _opencc_t2s is None:
        from opencc import OpenCC
        _opencc_t2s = OpenCC("t2s")
    folded = _re.sub(r"[\s·•・‧∙]+", "", _opencc_t2s.convert(str(value)))
    if not folded:
        return ""
    if _re.search(r"[一-鿿]", folded):
        return folded
    tokens = _re.findall(r"[A-Za-z]+", str(value).upper())
    return " ".join(sorted(tokens))


def upgrade() -> None:
    op.add_column('profile_persons', sa.Column(
        'name_folded', sa.String(100), nullable=True,
        comment='姓名归一化折叠键(person_name_fold):CJK=繁→简+去空白/间隔号,拉丁=大写词序无关;建卡去重 upsert 用'))

    # 回填存量
    bind = op.get_bind()
    for rid, name in bind.execute(sa.text('SELECT id, name FROM profile_persons')).fetchall():
        bind.execute(sa.text('UPDATE profile_persons SET name_folded = :f WHERE id = :i'),
                     {'f': _fold(name) or None, 'i': rid})

    op.create_index('ix_profile_persons_household_folded', 'profile_persons',
                    ['household_id', 'name_folded'], unique=False)

    # 非阻塞告警:列出当前重复组(应先跑 merge-duplicates 再升 024)
    dups = bind.execute(sa.text(
        'SELECT household_id, name_folded, COUNT(*) AS c FROM profile_persons '
        'WHERE name_folded IS NOT NULL GROUP BY 1, 2 HAVING COUNT(*) > 1')).fetchall()
    if dups:
        print(f'[023] 警告: 发现 {len(dups)} 组同名折叠重复(先跑 merge-duplicates-all 再升 024):')
        for household_id, folded, cnt in dups:
            print(f'  household_id={household_id} name_folded={folded} count={cnt}')


def downgrade() -> None:
    op.drop_index('ix_profile_persons_household_folded', table_name='profile_persons')
    op.drop_column('profile_persons', 'name_folded')
