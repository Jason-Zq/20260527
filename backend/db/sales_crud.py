"""销售视图相关只读查询。"""
from datetime import date
from typing import Optional

from sqlalchemy import select, or_, func

from db.engine import async_session_maker
from db.models import Client, FamilyMember


_CHILD_RELATIONS = ("child", "children", "子女", "孩子", "子", "女", "儿子", "女儿", "son", "daughter")


def _age_parts(birth_date: Optional[date]) -> tuple[Optional[int], Optional[int], str]:
    """按今天计算年龄，返回 (years, months_total, 'X岁Y个月')。"""
    if not birth_date:
        return None, None, "-"
    today = date.today()
    years = today.year - birth_date.year
    months = today.month - birth_date.month
    if today.day < birth_date.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    months_total = max(0, years * 12 + months)
    years = max(0, years)
    if months:
        text = f"{years}岁{months}个月"
    else:
        text = f"{years}岁"
    return years, months_total, text


async def list_child_age_leads(
    *,
    keyword: Optional[str] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """查询客户子女年龄列表，供销售顾问查看。"""
    async with async_session_maker() as session:
        stmt = (
            select(Client, FamilyMember)
            .join(FamilyMember, FamilyMember.client_id == Client.id)
            .where(FamilyMember.relation.in_(_CHILD_RELATIONS))
        )
        count_stmt = (
            select(func.count())
            .select_from(Client)
            .join(FamilyMember, FamilyMember.client_id == Client.id)
            .where(FamilyMember.relation.in_(_CHILD_RELATIONS))
        )

        conditions = []
        if keyword:
            kw = f"%{keyword}%"
            conditions.append(or_(
                Client.name.ilike(kw),
                Client.client_code.ilike(kw),
                FamilyMember.name.ilike(kw),
            ))
        for cond in conditions:
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        stmt = stmt.order_by(Client.name.asc(), FamilyMember.birth_date.asc().nullslast()).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).all()

        items = []
        # 年龄筛选要在 Python 算完后处理；total 用筛选后的总数更准确，故另行计数。
        filtered_count = 0
        for c, m in rows:
            age_years, age_months, age_text = _age_parts(m.birth_date)
            if min_age is not None and age_years is not None and age_years < min_age:
                continue
            if max_age is not None and age_years is not None and age_years > max_age:
                continue
            filtered_count += 1
            items.append({
                "client_id": c.id,
                "client_code": c.client_code,
                "client_name": c.name,
                "child_id": m.id,
                "child_name": m.name,
                "relation": m.relation,
                "birth_date": m.birth_date.isoformat() if m.birth_date else None,
                "age_years": age_years,
                "age_months": age_months,
                "age_text": age_text,
            })

        # 若没有年龄筛选，直接用 SQL count；有年龄筛选时为避免复杂 SQL，当前页内筛选。
        # MVP 可接受；后续需要全量准确 total 再将年龄计算改 SQL。
        total = (await session.execute(count_stmt)).scalar() or 0
        if min_age is not None or max_age is not None:
            total = filtered_count if offset == 0 else max(offset + filtered_count, filtered_count)
        return {"items": items, "total": total}
