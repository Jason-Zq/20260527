"""system_events 表 CRUD。

只暴露 3 个函数:
  - insert_event:写一条事件(由 event_service.log_event 调用)
  - list_events: 后台查询接口使用
  - delete_events_older_than: GC 用,_split_cleanup_loop 每小时跑一次
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, delete as sa_delete, func, and_, or_, cast, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

from db.engine import async_session_maker, sync_engine
from db.models import SystemEvent


# 同步 session 工厂，供无 event loop 的线程上下文写入使用（与 ai_api_call_crud 同模式）
SyncSession = sessionmaker(bind=sync_engine)


def _event_to_dict(e: SystemEvent) -> dict:
    return {
        "id": e.id,
        "occurred_at": e.occurred_at.strftime("%Y-%m-%d %H:%M:%S") if e.occurred_at else "",
        "severity": e.severity,
        "category": e.category,
        "message": e.message,
        "context": e.context or {},
    }


async def insert_event(
    severity: str,
    category: str,
    message: str,
    context: Optional[dict] = None,
) -> None:
    """写入一条事件。调用方保证参数合法(不抛 ValueError)。"""
    async with async_session_maker() as session:
        row = SystemEvent(
            severity=severity,
            category=category,
            message=message,
            context=context,
        )
        session.add(row)
        await session.commit()


def insert_event_sync(
    severity: str,
    category: str,
    message: str,
    context: Optional[dict] = None,
) -> None:
    """同步写入一条事件。供无 event loop 的线程上下文(如 asyncio.to_thread 里的
    _call_llm/text_extractor)使用——async 引擎连接池绑在主 loop,跨 loop 用
    asyncpg 会段错误杀整个进程(2026-07-28 生产实锤,protocol.so 同一指令两次)。"""
    with SyncSession() as session:
        row = SystemEvent(
            severity=severity,
            category=category,
            message=message,
            context=context,
        )
        session.add(row)
        session.commit()


async def list_events(
    *,
    severities: Optional[list[str]] = None,
    categories: Optional[list[str]] = None,
    batch_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """按筛选条件查事件;返回 (items, total)。

    items 按 occurred_at DESC 排序;total 为符合条件的总数(用于前端分页)。
    """
    filters = []
    if severities:
        filters.append(SystemEvent.severity.in_(severities))
    if categories:
        filters.append(SystemEvent.category.in_(categories))
    if since is not None:
        filters.append(SystemEvent.occurred_at >= since)
    if until is not None:
        filters.append(SystemEvent.occurred_at < until)
    if batch_id:
        # 走 ix_system_events_batch_id 索引(JSONB 表达式索引)
        filters.append(SystemEvent.context["batch_id"].astext == batch_id)

    where_clause = and_(*filters) if filters else None

    async with async_session_maker() as session:
        # total
        count_stmt = select(func.count()).select_from(SystemEvent)
        if where_clause is not None:
            count_stmt = count_stmt.where(where_clause)
        total = (await session.execute(count_stmt)).scalar_one()

        # items
        stmt = select(SystemEvent).order_by(SystemEvent.occurred_at.desc(), SystemEvent.id.desc())
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        stmt = stmt.limit(limit).offset(offset)
        res = await session.execute(stmt)
        items = [_event_to_dict(e) for e in res.scalars().all()]
        return items, total


async def delete_events_older_than(days: int = 30) -> int:
    """删除 occurred_at < now() - days 天的事件,返回删除行数。"""
    if days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    async with async_session_maker() as session:
        stmt = sa_delete(SystemEvent).where(SystemEvent.occurred_at < cutoff)
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount or 0


async def distinct_categories() -> list[str]:
    """前端筛选下拉用:返回 system_events 表里出现过的所有 category。"""
    async with async_session_maker() as session:
        stmt = select(SystemEvent.category).distinct().order_by(SystemEvent.category)
        res = await session.execute(stmt)
        return [row[0] for row in res.all()]
