"""external_api_logs CRUD:出站调用外部接口(URL 刷新 / LLM)。"""

import json
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select, delete as sa_delete, func, and_, text as sa_text

from db.engine import async_session_maker, sync_engine
from db.models import ExternalApiLog


def _to_dict(r: ExternalApiLog) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        "service": r.service,
        "url": r.url,
        "request_params": r.request_params or {},
        "response_summary": r.response_summary or {},
        "status": r.status,
        "error_msg": r.error_msg,
        "elapsed_ms": r.elapsed_ms,
        "batch_id": r.batch_id,
        "file_id": r.file_id,
    }


async def insert_external_api_log(
    *,
    service: str,
    url: Optional[str],
    request_params: Optional[dict],
    response_summary: Optional[dict],
    status: str,
    error_msg: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
    batch_id: Optional[str] = None,
    file_id: Optional[str] = None,
) -> None:
    async with async_session_maker() as session:
        row = ExternalApiLog(
            service=service, url=url,
            request_params=request_params, response_summary=response_summary,
            status=status, error_msg=error_msg, elapsed_ms=elapsed_ms,
            batch_id=batch_id, file_id=file_id,
        )
        session.add(row)
        await session.commit()


def insert_external_api_log_sync(
    *,
    service: str,
    url: Optional[str],
    request_params: Optional[dict],
    response_summary: Optional[dict],
    status: str,
    error_msg: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
    batch_id: Optional[str] = None,
    file_id: Optional[str] = None,
) -> None:
    """同步写入(psycopg2)。供 worker 线程里的 _call_llm 调用——那里没有可用的 async loop,
    async 引擎的连接池绑定在主 loop 上,跨 loop 用会报 'attached to a different loop'。
    """
    stmt = sa_text("""
        INSERT INTO external_api_logs
            (created_at, service, url, request_params, response_summary,
             status, error_msg, elapsed_ms, batch_id, file_id)
        VALUES
            (now(), :service, :url, CAST(:request_params AS JSONB), CAST(:response_summary AS JSONB),
             :status, :error_msg, :elapsed_ms, :batch_id, :file_id)
    """)
    with sync_engine.begin() as conn:
        conn.execute(stmt, {
            "service": service,
            "url": url,
            "request_params": json.dumps(request_params, ensure_ascii=False) if request_params is not None else None,
            "response_summary": json.dumps(response_summary, ensure_ascii=False) if response_summary is not None else None,
            "status": status,
            "error_msg": error_msg,
            "elapsed_ms": elapsed_ms,
            "batch_id": batch_id,
            "file_id": file_id,
        })


async def list_external_api_logs(
    *,
    service: Optional[str] = None,
    status: Optional[str] = None,
    batch_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if service:
        filters.append(ExternalApiLog.service == service)
    if status:
        filters.append(ExternalApiLog.status == status)
    if batch_id:
        filters.append(ExternalApiLog.batch_id.ilike(f"%{batch_id}%"))
    if since is not None:
        filters.append(ExternalApiLog.created_at >= since)
    if until is not None:
        filters.append(ExternalApiLog.created_at < until)

    where = and_(*filters) if filters else None

    async with async_session_maker() as session:
        count_stmt = select(func.count()).select_from(ExternalApiLog)
        if where is not None:
            count_stmt = count_stmt.where(where)
        total = (await session.execute(count_stmt)).scalar_one()

        stmt = select(ExternalApiLog).order_by(
            ExternalApiLog.created_at.desc(), ExternalApiLog.id.desc()
        )
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.limit(limit).offset(offset)
        res = await session.execute(stmt)
        items = [_to_dict(r) for r in res.scalars().all()]
        return items, total


async def delete_external_api_logs_older_than(days: int = 30) -> int:
    if days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    async with async_session_maker() as session:
        stmt = sa_delete(ExternalApiLog).where(ExternalApiLog.created_at < cutoff)
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount or 0
