"""文件留底检测的数据库 CRUD（archive_detect_batches + archive_detect_files）。

设计上与 split_crud.py 类似：分阶段更新（pending→fetching→ocr→llm→done/error），
每次写都更新 updated_at 便于排查"卡住"的任务。
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional, Iterable
from sqlalchemy import select, update as sa_update, delete as sa_delete
from sqlalchemy.orm import selectinload

from db.engine import async_session_maker
from db.models import ArchiveDetectBatch, ArchiveDetectFile


def _file_to_dict(f: ArchiveDetectFile) -> dict:
    return {
        "idx": f.idx,
        "source_url": f.source_url,
        "filename": f.filename,
        "mime_type": f.mime_type,
        "page_count": f.page_count,
        "char_count": f.char_count,
        "is_archival": f.is_archival,
        "confidence": f.confidence,
        "reason": f.reason,
        "key_points": f.key_points or [],
        "doc_category": f.doc_category,
        "status": f.status,
        "error_msg": f.error_msg,
        "elapsed_sec": float(f.elapsed_sec) if f.elapsed_sec is not None else None,
    }


def _batch_to_dict(b: ArchiveDetectBatch, *, include_files: bool = True) -> dict:
    out = {
        "batch_id": b.batch_id,
        "user_prompt": b.user_prompt,
        "source_kind": b.source_kind,
        "total_files": b.total_files,
        "done_files": b.done_files,
        "status": b.status,
        "error": b.error,
        "created_at": b.created_at.strftime("%Y-%m-%d %H:%M:%S") if b.created_at else "",
        "updated_at": b.updated_at.strftime("%Y-%m-%d %H:%M:%S") if b.updated_at else "",
    }
    if include_files:
        out["files"] = [_file_to_dict(f) for f in (b.files or [])]
    return out


async def create_batch_with_files(
    *,
    batch_id: str,
    user_prompt: str,
    source_kind: str,
    file_specs: list[dict],
) -> None:
    """创建 batch + N 个 file 记录（一次事务）。

    file_specs: [{"source_url": str|None, "filename": str|None, "mime_type": str|None}, ...]
    """
    now = datetime.now()
    async with async_session_maker() as session:
        batch = ArchiveDetectBatch(
            batch_id=batch_id,
            user_prompt=user_prompt,
            source_kind=source_kind,
            total_files=len(file_specs),
            done_files=0,
            status="running",
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        for i, spec in enumerate(file_specs):
            session.add(ArchiveDetectFile(
                batch_id=batch_id,
                idx=i,
                source_url=spec.get("source_url"),
                filename=spec.get("filename"),
                mime_type=spec.get("mime_type"),
                status="pending",
                created_at=now,
                updated_at=now,
            ))
        await session.commit()


async def update_file_status(batch_id: str, idx: int, status: str) -> None:
    async with async_session_maker() as session:
        stmt = (
            sa_update(ArchiveDetectFile)
            .where(ArchiveDetectFile.batch_id == batch_id, ArchiveDetectFile.idx == idx)
            .values(status=status, updated_at=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()


async def update_file_done(batch_id: str, idx: int, payload: dict) -> None:
    """落库脱敏后的 LLM 结果 + 文件元信息。payload 里允许的键见 _file_to_dict。"""
    values = {
        "status": "done",
        "filename": payload.get("filename"),
        "mime_type": payload.get("mime_type"),
        "page_count": payload.get("page_count"),
        "char_count": payload.get("char_count"),
        "is_archival": payload.get("is_archival"),
        "confidence": payload.get("confidence"),
        "reason": payload.get("reason"),
        "key_points": payload.get("key_points"),
        "doc_category": payload.get("doc_category"),
        "updated_at": datetime.now(),
    }
    if payload.get("elapsed_sec") is not None:
        values["elapsed_sec"] = Decimal(str(payload["elapsed_sec"]))
    async with async_session_maker() as session:
        stmt = (
            sa_update(ArchiveDetectFile)
            .where(ArchiveDetectFile.batch_id == batch_id, ArchiveDetectFile.idx == idx)
            .values(**values)
        )
        await session.execute(stmt)
        await session.commit()


async def update_file_error(
    batch_id: str, idx: int, error_msg: str, elapsed_sec: Optional[float] = None,
    filename: Optional[str] = None,
) -> None:
    values = {
        "status": "error",
        "error_msg": error_msg,
        "updated_at": datetime.now(),
    }
    if elapsed_sec is not None:
        values["elapsed_sec"] = Decimal(str(elapsed_sec))
    if filename:
        values["filename"] = filename
    async with async_session_maker() as session:
        stmt = (
            sa_update(ArchiveDetectFile)
            .where(ArchiveDetectFile.batch_id == batch_id, ArchiveDetectFile.idx == idx)
            .values(**values)
        )
        await session.execute(stmt)
        await session.commit()


async def bump_done_count(batch_id: str) -> int:
    """+1 done_files；返回新值。所有文件处理完毕时由调用方再置 status=done。"""
    async with async_session_maker() as session:
        res = await session.execute(
            select(ArchiveDetectBatch).where(ArchiveDetectBatch.batch_id == batch_id)
        )
        b = res.scalar_one_or_none()
        if not b:
            return 0
        b.done_files = (b.done_files or 0) + 1
        b.updated_at = datetime.now()
        await session.commit()
        return b.done_files


async def update_batch_status(batch_id: str, status: str, error: Optional[str] = None) -> None:
    async with async_session_maker() as session:
        values = {"status": status, "updated_at": datetime.now()}
        if error is not None:
            values["error"] = error
        stmt = (
            sa_update(ArchiveDetectBatch)
            .where(ArchiveDetectBatch.batch_id == batch_id)
            .values(**values)
        )
        await session.execute(stmt)
        await session.commit()


async def get_batch(batch_id: str) -> Optional[dict]:
    """返回 batch + 所有 files（按 idx 排序）。"""
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectBatch)
            .options(selectinload(ArchiveDetectBatch.files))
            .where(ArchiveDetectBatch.batch_id == batch_id)
        )
        res = await session.execute(stmt)
        b = res.scalar_one_or_none()
        return _batch_to_dict(b) if b else None


async def list_batches(limit: int = 200, offset: int = 0) -> list[dict]:
    """历史列表（不含 files 详情，仅 batch 概要）。"""
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectBatch)
            .order_by(ArchiveDetectBatch.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await session.execute(stmt)
        rows = res.scalars().all()
        return [_batch_to_dict(b, include_files=False) for b in rows]


async def delete_batch(batch_id: str) -> bool:
    async with async_session_maker() as session:
        stmt = sa_delete(ArchiveDetectBatch).where(ArchiveDetectBatch.batch_id == batch_id)
        res = await session.execute(stmt)
        await session.commit()
        return (res.rowcount or 0) > 0
