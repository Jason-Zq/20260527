"""api_request_logs CRUD。"""

from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select, delete as sa_delete, func, and_

from db.engine import async_session_maker
from db.models import ApiRequestLog


def _to_dict(r: ApiRequestLog) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        "source": r.source,
        "method": r.method,
        "path": r.path,
        "client_ip": r.client_ip,
        "request_body": r.request_body or {},
        "response_status": r.response_status,
        "elapsed_ms": r.elapsed_ms,
    }


async def insert_request_log(
    source: str, method: str, path: str, client_ip: str | None,
    request_body: dict | None, response_status: int | None,
    elapsed_ms: int | None,
) -> None:
    async with async_session_maker() as session:
        row = ApiRequestLog(
            source=source, method=method, path=path, client_ip=client_ip,
            request_body=request_body, response_status=response_status,
            elapsed_ms=elapsed_ms,
        )
        session.add(row)
        await session.commit()


async def list_request_logs(
    *,
    source: Optional[str] = None,
    method: Optional[str] = None,
    path_contains: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if source:
        filters.append(ApiRequestLog.source == source)
    if method:
        filters.append(ApiRequestLog.method == method.upper())
    if path_contains:
        filters.append(ApiRequestLog.path.ilike(f"%{path_contains}%"))
    if since is not None:
        filters.append(ApiRequestLog.created_at >= since)
    if until is not None:
        filters.append(ApiRequestLog.created_at < until)

    where = and_(*filters) if filters else None

    async with async_session_maker() as session:
        count_stmt = select(func.count()).select_from(ApiRequestLog)
        if where is not None:
            count_stmt = count_stmt.where(where)
        total = (await session.execute(count_stmt)).scalar_one()

        stmt = select(ApiRequestLog).order_by(ApiRequestLog.created_at.desc(), ApiRequestLog.id.desc())
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.limit(limit).offset(offset)
        res = await session.execute(stmt)
        items = [_to_dict(r) for r in res.scalars().all()]
        return items, total


async def delete_request_logs_older_than(days: int = 30) -> int:
    if days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    async with async_session_maker() as session:
        stmt = sa_delete(ApiRequestLog).where(ApiRequestLog.created_at < cutoff)
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount or 0
