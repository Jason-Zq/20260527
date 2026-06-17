"""PDF 拆分任务的数据库 CRUD。

设计:与 backend/db/crud.py 解耦(后者聚焦证件解析流水线),拆分流水线独立一个模块。
所有写操作走 _refresh_updated_at 更新 updated_at,便于排查"卡住"的任务。
"""

from datetime import datetime, timedelta
from typing import Optional, Iterable
from sqlalchemy import select, update as sa_update, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import async_session_maker
from db.models import SplitTask


async def create(task_id: str, filename: str) -> None:
    """上传时立即写一条 status='ocr' 的记录。"""
    async with async_session_maker() as session:
        row = SplitTask(
            task_id=task_id,
            filename=filename,
            status="ocr",
            files_cleaned=False,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        session.add(row)
        await session.commit()


async def update_status(
    task_id: str,
    status: str,
    error: Optional[str] = None,
    total_pages: Optional[int] = None,
) -> None:
    """流水线阶段切换时调用(ocr→llm→splitting,或失败置 error)。"""
    payload: dict = {"status": status, "updated_at": datetime.now()}
    if error is not None:
        payload["error"] = error
    if total_pages is not None:
        payload["total_pages"] = total_pages
    async with async_session_maker() as session:
        stmt = sa_update(SplitTask).where(SplitTask.task_id == task_id).values(**payload)
        await session.execute(stmt)
        await session.commit()


async def update_done(
    task_id: str,
    total_pages: int,
    ranges: list,
    duration_sec: float,
) -> None:
    """完成时一次性写 status='done' + 拆分结果 + 总耗时。"""
    async with async_session_maker() as session:
        stmt = (
            sa_update(SplitTask)
            .where(SplitTask.task_id == task_id)
            .values(
                status="done",
                error=None,
                total_pages=total_pages,
                ranges=ranges,
                duration_sec=duration_sec,
                updated_at=datetime.now(),
            )
        )
        await session.execute(stmt)
        await session.commit()


async def get(task_id: str) -> Optional[SplitTask]:
    async with async_session_maker() as session:
        stmt = select(SplitTask).where(SplitTask.task_id == task_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def list_history(limit: int = 200, offset: int = 0) -> list[dict]:
    """历史列表:按 created_at 倒序,返回前端可直接渲染的扁平 dict。"""
    async with async_session_maker() as session:
        stmt = (
            select(SplitTask)
            .order_by(SplitTask.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [_to_history_dict(r) for r in rows]


def _to_history_dict(row: SplitTask) -> dict:
    ranges = row.ranges or []
    files_count = len(ranges) if isinstance(ranges, list) else 0
    return {
        "task_id": row.task_id,
        "filename": row.filename,
        "total_pages": row.total_pages,
        "files_count": files_count,
        "status": row.status,
        "error": row.error,
        "duration_sec": row.duration_sec,
        "files_cleaned": row.files_cleaned,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        "ranges": ranges,
    }


async def delete(task_id: str) -> bool:
    """彻底删除一条历史记录(配合上层删 output/{task_id}/)。"""
    async with async_session_maker() as session:
        stmt = sa_delete(SplitTask).where(SplitTask.task_id == task_id)
        result = await session.execute(stmt)
        await session.commit()
        return (result.rowcount or 0) > 0


async def list_expired_task_ids(max_age_days: int) -> list[str]:
    """返回所有 created_at 早于 cutoff 且 files_cleaned=False 的 task_id。

    用于 7 天清理:DB 记录保留,但物理文件需要删,再把 files_cleaned 置 true。
    """
    cutoff = datetime.now() - timedelta(days=max_age_days)
    async with async_session_maker() as session:
        stmt = select(SplitTask.task_id).where(
            SplitTask.created_at < cutoff,
            SplitTask.files_cleaned.is_(False),
        )
        result = await session.execute(stmt)
        return [r[0] for r in result.all()]


async def mark_files_cleaned(task_ids: Iterable[str]) -> None:
    ids = list(task_ids)
    if not ids:
        return
    async with async_session_maker() as session:
        stmt = (
            sa_update(SplitTask)
            .where(SplitTask.task_id.in_(ids))
            .values(files_cleaned=True, updated_at=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()
