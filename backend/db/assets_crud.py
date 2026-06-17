"""
资产 CRUD（assets 表）。
asset_type 区分：房产/存款/银行流水/股票/车辆/其他。
"""

from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Optional
from sqlalchemy import select, delete as sa_delete

from db.engine import async_session_maker
from db.models import Asset


_ASSET_COLUMNS = {
    "asset_type", "asset_name", "owner_name", "co_owners",
    "value_amount", "currency", "certificate_no",
    "location_address", "area_sqm", "usage_type", "acquired_date",
    "bank_name", "account_no", "period_start", "period_end", "frozen_until",
    "notes",
}

_DATE_COLUMNS = {"acquired_date", "period_start", "period_end", "frozen_until"}
_DECIMAL_COLUMNS = {"value_amount", "area_sqm"}


def _coerce_value(col: str, value):
    if value is None or value == "":
        return None

    if col in _DATE_COLUMNS and not isinstance(value, date):
        s = str(value).strip()
        import re
        digits = re.findall(r"\d+", s)
        if len(digits) >= 3:
            try:
                y, m, d = int(digits[0]), int(digits[1]), int(digits[2])
                return date(y, m, d)
            except (ValueError, TypeError):
                return None
        return None

    if col in _DECIMAL_COLUMNS and not isinstance(value, (int, float, Decimal)):
        s = str(value).strip()
        # 过滤千分位逗号、人民币符号、文字
        cleaned = "".join(c for c in s if c.isdigit() or c == "." or c == "-")
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    return value


def _filter_payload(payload: dict) -> dict:
    out = {}
    for k, v in (payload or {}).items():
        if k not in _ASSET_COLUMNS:
            continue
        coerced = _coerce_value(k, v)
        if coerced is not None:
            out[k] = coerced
    return out


# ============== 公共 API ==============

async def list_by_client(client_id: int) -> list[dict]:
    async with async_session_maker() as session:
        stmt = (
            select(Asset)
            .where(Asset.client_id == client_id)
            .order_by(Asset.asset_type, Asset.id.desc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_dict(a) for a in rows]


async def get_by_id(asset_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        res = await session.execute(select(Asset).where(Asset.id == asset_id))
        a = res.scalar_one_or_none()
        return _to_dict(a) if a else None


async def create(client_id: int, payload: dict) -> dict:
    data = _filter_payload(payload)
    if not data.get("asset_type"):
        raise ValueError("asset_type 不能为空")
    async with async_session_maker() as session:
        a = Asset(client_id=client_id, **data)
        session.add(a)
        await session.commit()
        await session.refresh(a)
        return _to_dict(a)


async def update(asset_id: int, payload: dict) -> Optional[dict]:
    data = _filter_payload(payload)
    if not data:
        return await get_by_id(asset_id)
    async with async_session_maker() as session:
        res = await session.execute(select(Asset).where(Asset.id == asset_id))
        a = res.scalar_one_or_none()
        if not a:
            return None
        for k, v in data.items():
            setattr(a, k, v)
        a.updated_at = datetime.now()
        await session.commit()
        await session.refresh(a)
        return _to_dict(a)


async def delete(asset_id: int) -> bool:
    async with async_session_maker() as session:
        res = await session.execute(sa_delete(Asset).where(Asset.id == asset_id))
        await session.commit()
        return res.rowcount > 0


def _to_dict(a: Asset) -> dict:
    if not a:
        return None
    return {
        "id": a.id,
        "client_id": a.client_id,
        "asset_type": a.asset_type,
        "asset_name": a.asset_name,
        "owner_name": a.owner_name,
        "co_owners": a.co_owners,
        "value_amount": float(a.value_amount) if a.value_amount is not None else None,
        "currency": a.currency,
        "certificate_no": a.certificate_no,
        "location_address": a.location_address,
        "area_sqm": float(a.area_sqm) if a.area_sqm is not None else None,
        "usage_type": a.usage_type,
        "acquired_date": a.acquired_date.isoformat() if a.acquired_date else None,
        "bank_name": a.bank_name,
        "account_no": a.account_no,
        "period_start": a.period_start.isoformat() if a.period_start else None,
        "period_end": a.period_end.isoformat() if a.period_end else None,
        "frozen_until": a.frozen_until.isoformat() if a.frozen_until else None,
        "notes": a.notes,
        "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "",
        "updated_at": a.updated_at.strftime("%Y-%m-%d %H:%M:%S") if a.updated_at else "",
    }
