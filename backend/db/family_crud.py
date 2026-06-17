"""
家庭成员 CRUD（family_members 表）。
风格对齐 backend/db/crud.py：所有函数 async def + async with async_session_maker。
"""

from datetime import datetime, date
from typing import Optional
from sqlalchemy import select, delete as sa_delete

from db.engine import async_session_maker
from db.models import FamilyMember


# ============== 内部工具 ==============

# 强 schema 列白名单（防止 payload 注入随意键）
_FAMILY_COLUMNS = {
    "relation", "name", "name_en", "gender", "birth_date", "nationality",
    "id_number", "phone",
    "passport_no", "email", "current_address", "company_name", "position",
    "school_name", "school_name_en", "major", "degree", "graduation_date",
    "graduation_cert_no", "degree_cert_no",
    "birth_cert_no", "birth_hospital", "birth_place",
    "will_accompany", "notes",
}

# 日期类列
_DATE_COLUMNS = {"birth_date", "graduation_date"}


def _coerce_value(col: str, value):
    """把字符串转成数据库期望的类型（最简版）。
    日期：YYYY-MM-DD / YYYY/MM/DD / YYYY年MM月DD日
    bool：true/false/1/0/是/否/Y/N
    其他：原样字符串
    """
    if value is None or value == "":
        return None
    if col in _DATE_COLUMNS and not isinstance(value, date):
        s = str(value).strip()
        # 提取数字
        import re
        digits = re.findall(r"\d+", s)
        if len(digits) >= 3:
            try:
                y, m, d = int(digits[0]), int(digits[1]), int(digits[2])
                return date(y, m, d)
            except (ValueError, TypeError):
                return None
        return None
    if col == "will_accompany":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        return s in ("true", "1", "是", "y", "yes")
    return value


def _filter_payload(payload: dict) -> dict:
    """只保留白名单里的列，做类型转换。"""
    out = {}
    for k, v in (payload or {}).items():
        if k not in _FAMILY_COLUMNS:
            continue
        coerced = _coerce_value(k, v)
        if coerced is not None:
            out[k] = coerced
    return out


# ============== 公共 API ==============

async def list_by_client(client_id: int) -> list[dict]:
    """列出某客户的所有家庭成员。"""
    async with async_session_maker() as session:
        stmt = (
            select(FamilyMember)
            .where(FamilyMember.client_id == client_id)
            .order_by(FamilyMember.relation, FamilyMember.id)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_dict(m) for m in rows]


async def get_by_id(member_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        res = await session.execute(select(FamilyMember).where(FamilyMember.id == member_id))
        m = res.scalar_one_or_none()
        return _to_dict(m) if m else None


async def create(client_id: int, payload: dict) -> dict:
    """新建一个家庭成员。relation 与 name 必填。"""
    data = _filter_payload(payload)
    if not data.get("relation"):
        raise ValueError("relation 不能为空")
    if not data.get("name"):
        raise ValueError("name 不能为空")

    async with async_session_maker() as session:
        m = FamilyMember(client_id=client_id, **data)
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return _to_dict(m)


async def update(member_id: int, payload: dict) -> Optional[dict]:
    """部分更新。仅更新 payload 中提供的字段。"""
    data = _filter_payload(payload)
    if not data:
        return await get_by_id(member_id)

    async with async_session_maker() as session:
        res = await session.execute(select(FamilyMember).where(FamilyMember.id == member_id))
        m = res.scalar_one_or_none()
        if not m:
            return None
        for k, v in data.items():
            setattr(m, k, v)
        m.updated_at = datetime.now()
        await session.commit()
        await session.refresh(m)
        return _to_dict(m)


async def upsert_by_relation(client_id: int, relation: str, payload: dict) -> dict:
    """按 (client_id, relation) 找现有行，找不到就新建。
    用于"配偶身份证 OCR 后，没有配偶就新建一个"的归档场景。
    payload 中可不含 relation；name 必填（如果是新建）。
    """
    async with async_session_maker() as session:
        # 优先按 relation+name 找；name 也是 payload 的一部分
        stmt = select(FamilyMember).where(
            FamilyMember.client_id == client_id,
            FamilyMember.relation == relation,
        )
        rows = (await session.execute(stmt)).scalars().all()

        # 找不到就建，data 必须有 name；找到一个直接合并；多个先用第一个
        data = _filter_payload({**payload, "relation": relation})
        if rows:
            target = rows[0]
            for k, v in data.items():
                if v is not None:
                    setattr(target, k, v)
            target.updated_at = datetime.now()
            await session.commit()
            await session.refresh(target)
            return _to_dict(target)
        else:
            if not data.get("name"):
                raise ValueError(f"无法新建 {relation}：缺少 name")
            m = FamilyMember(client_id=client_id, **data)
            session.add(m)
            await session.commit()
            await session.refresh(m)
            return _to_dict(m)


async def delete(member_id: int) -> bool:
    async with async_session_maker() as session:
        res = await session.execute(sa_delete(FamilyMember).where(FamilyMember.id == member_id))
        await session.commit()
        return res.rowcount > 0


# ============== 序列化 ==============

def _to_dict(m: FamilyMember) -> dict:
    """ORM → JSON 字典。日期转 ISO 字符串。"""
    if not m:
        return None
    return {
        "id": m.id,
        "client_id": m.client_id,
        "relation": m.relation,
        "name": m.name,
        "name_en": m.name_en,
        "gender": m.gender,
        "birth_date": m.birth_date.isoformat() if m.birth_date else None,
        "nationality": m.nationality,
        "id_number": m.id_number,
        "phone": m.phone,
        "passport_no": m.passport_no,
        "email": m.email,
        "current_address": m.current_address,
        "company_name": m.company_name,
        "position": m.position,
        "school_name": m.school_name,
        "school_name_en": m.school_name_en,
        "major": m.major,
        "degree": m.degree,
        "graduation_date": m.graduation_date.isoformat() if m.graduation_date else None,
        "graduation_cert_no": m.graduation_cert_no,
        "degree_cert_no": m.degree_cert_no,
        "birth_cert_no": m.birth_cert_no,
        "birth_hospital": m.birth_hospital,
        "birth_place": m.birth_place,
        "will_accompany": bool(m.will_accompany) if m.will_accompany is not None else False,
        "notes": m.notes,
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
        "updated_at": m.updated_at.strftime("%Y-%m-%d %H:%M:%S") if m.updated_at else "",
    }
