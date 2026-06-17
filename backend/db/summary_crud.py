"""
摘要历史 CRUD（summaries 表）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import select, delete as sa_delete

from db.engine import async_session_maker
from db.models import Summary


def _to_dict(s: Summary, *, include_text: bool = False) -> dict:
    """ORM → JSON。include_text=False 时不返 extracted_text，节省网络。"""
    if not s:
        return None
    out = {
        "id": s.id,
        "url": s.url,
        "progress_name": s.progress_name,
        "filename": s.filename,
        "mime_type": s.mime_type,
        "source": s.source,
        "page_count": s.page_count,
        "char_count": s.char_count,
        "title": s.title,
        "summary": s.summary,
        "key_points": s.key_points or [],
        "doc_category": s.doc_category,
        "relevance": s.relevance,
        "relevance_score": s.relevance_score,
        "relevance_reason": s.relevance_reason,
        "elapsed_sec": float(s.elapsed_sec) if s.elapsed_sec is not None else None,
        "status": s.status,
        "error_msg": s.error_msg,
        "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
    }
    if include_text:
        out["extracted_text"] = s.extracted_text
    return out


async def create(payload: dict) -> dict:
    """新建一条摘要记录。"""
    async with async_session_maker() as session:
        s = Summary(
            url=payload["url"],
            progress_name=payload.get("progress_name"),
            filename=payload.get("filename"),
            mime_type=payload.get("mime_type"),
            source=payload.get("source"),
            page_count=payload.get("page_count"),
            char_count=payload.get("char_count"),
            extracted_text=payload.get("extracted_text"),
            title=payload.get("title"),
            summary=payload.get("summary"),
            key_points=payload.get("key_points"),
            doc_category=payload.get("doc_category"),
            relevance=payload.get("relevance"),
            relevance_score=payload.get("relevance_score"),
            relevance_reason=payload.get("relevance_reason"),
            elapsed_sec=Decimal(str(payload["elapsed_sec"])) if payload.get("elapsed_sec") is not None else None,
            status=payload.get("status", "done"),
            error_msg=payload.get("error_msg"),
            created_at=datetime.now(),
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return _to_dict(s, include_text=True)


async def get_by_id(summary_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        res = await session.execute(select(Summary).where(Summary.id == summary_id))
        s = res.scalar_one_or_none()
        return _to_dict(s, include_text=True) if s else None


async def list_summaries(limit: int = 100, offset: int = 0) -> list[dict]:
    """列表（按时间倒序，不返 extracted_text）。"""
    async with async_session_maker() as session:
        stmt = (
            select(Summary)
            .order_by(Summary.created_at.desc(), Summary.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_dict(s, include_text=False) for s in rows]


async def delete(summary_id: int) -> bool:
    async with async_session_maker() as session:
        res = await session.execute(sa_delete(Summary).where(Summary.id == summary_id))
        await session.commit()
        return res.rowcount > 0
