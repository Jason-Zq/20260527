"""ai_api_calls CRUD: AI/LLM API 调用记录。"""

from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select, delete as sa_delete, func, and_
from sqlalchemy.orm import sessionmaker

from db.engine import async_session_maker, sync_engine
from db.models import AiApiCall


# 同步 session 工厂，供 worker 线程里的 LLM 日志写入使用
SyncSession = sessionmaker(bind=sync_engine)

# prompt/response 存库软上限:超出截断,避免异常超长文本膨胀表/拖慢查询
_TEXT_STORE_LIMIT = 50_000
_ERROR_MSG_LIMIT = 2_000

# C0 控制字符清洗表:PostgreSQL text 列不接受 NUL(0x00),其它 C0 控制符也无存储价值,
# 一并去除;保留 \t(0x09) \n(0x0a) \r(0x0d)。
_CTRL_DELETE = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}
_CTRL_DELETE[0x7F] = None  # DEL


def _clean_text(s: Optional[str], limit: Optional[int] = None) -> Optional[str]:
    """写库前清洗:去除 NUL/控制字符,可选长度截断。None 原样返回。"""
    if s is None:
        return None
    cleaned = s.translate(_CTRL_DELETE)
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[:limit] + f"\n...[已截断,原长 {len(cleaned)} 字]"
    return cleaned


_LIST_PREVIEW_LIMIT = 500  # 列表页 prompt/response 预览截断长度


def _preview(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    if len(s) > _LIST_PREVIEW_LIMIT:
        return s[:_LIST_PREVIEW_LIMIT] + f"...[共 {len(s)} 字,详情查看全文]"
    return s


def _to_dict(r: AiApiCall, *, preview: bool = False) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        "operation": r.operation,
        "model": r.model,
        "prompt": _preview(r.prompt) if preview else r.prompt,
        "response_raw": _preview(r.response_raw) if preview else r.response_raw,
        "status": r.status,
        "error_msg": r.error_msg,
        "elapsed_ms": r.elapsed_ms,
        "batch_id": r.batch_id,
        "file_id": r.file_id,
        "client_code": r.client_code,
        "task_id": r.task_id,
    }


def _build_row(
    *, operation, model, prompt, response_raw, status,
    error_msg, elapsed_ms, batch_id, file_id, client_code, task_id,
) -> AiApiCall:
    """构造 AiApiCall 行,统一清洗大字段(NUL/控制字符 + 长度上限),两个写入路径共用。"""
    return AiApiCall(
        operation=operation, model=model,
        prompt=_clean_text(prompt, _TEXT_STORE_LIMIT),
        response_raw=_clean_text(response_raw, _TEXT_STORE_LIMIT),
        status=status,
        error_msg=_clean_text(error_msg, _ERROR_MSG_LIMIT),
        elapsed_ms=elapsed_ms,
        batch_id=batch_id, file_id=file_id, client_code=client_code, task_id=task_id,
    )


async def insert_ai_api_call(
    *,
    operation: Optional[str] = None,
    model: Optional[str] = None,
    prompt: Optional[str] = None,
    response_raw: Optional[str] = None,
    status: str,
    error_msg: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
    batch_id: Optional[str] = None,
    file_id: Optional[str] = None,
    client_code: Optional[str] = None,
    task_id: Optional[str] = None,
) -> None:
    async with async_session_maker() as session:
        row = _build_row(
            operation=operation, model=model, prompt=prompt, response_raw=response_raw,
            status=status, error_msg=error_msg, elapsed_ms=elapsed_ms,
            batch_id=batch_id, file_id=file_id, client_code=client_code, task_id=task_id,
        )
        session.add(row)
        await session.commit()


def insert_ai_api_call_sync(
    *,
    operation: Optional[str] = None,
    model: Optional[str] = None,
    prompt: Optional[str] = None,
    response_raw: Optional[str] = None,
    status: str,
    error_msg: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
    batch_id: Optional[str] = None,
    file_id: Optional[str] = None,
    client_code: Optional[str] = None,
    task_id: Optional[str] = None,
) -> None:
    """同步写入。供 worker 线程里的 _call_llm 使用——那里没有可用的 async loop。"""
    with SyncSession() as session:
        row = _build_row(
            operation=operation, model=model, prompt=prompt, response_raw=response_raw,
            status=status, error_msg=error_msg, elapsed_ms=elapsed_ms,
            batch_id=batch_id, file_id=file_id, client_code=client_code, task_id=task_id,
        )
        session.add(row)
        session.commit()


async def list_ai_api_calls(
    *,
    operation: Optional[str] = None,
    model: Optional[str] = None,
    status: Optional[str] = None,
    batch_id: Optional[str] = None,
    file_id: Optional[str] = None,
    client_code: Optional[str] = None,
    task_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    filters = []
    if operation:
        filters.append(AiApiCall.operation == operation)
    if model:
        filters.append(AiApiCall.model.ilike(f"%{model}%"))
    if status:
        filters.append(AiApiCall.status == status)
    if batch_id:
        filters.append(AiApiCall.batch_id.ilike(f"%{batch_id}%"))
    if file_id:
        filters.append(AiApiCall.file_id.ilike(f"%{file_id}%"))
    if client_code:
        filters.append(AiApiCall.client_code.ilike(f"%{client_code}%"))
    if task_id:
        filters.append(AiApiCall.task_id.ilike(f"%{task_id}%"))
    if since is not None:
        filters.append(AiApiCall.created_at >= since)
    if until is not None:
        filters.append(AiApiCall.created_at < until)

    where = and_(*filters) if filters else None

    async with async_session_maker() as session:
        count_stmt = select(func.count()).select_from(AiApiCall)
        if where is not None:
            count_stmt = count_stmt.where(where)
        total = (await session.execute(count_stmt)).scalar_one()

        stmt = select(AiApiCall).order_by(
            AiApiCall.created_at.desc(), AiApiCall.id.desc()
        )
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.limit(limit).offset(offset)
        res = await session.execute(stmt)
        items = [_to_dict(r, preview=True) for r in res.scalars().all()]
        return items, total


async def get_ai_api_call_detail(row_id: int) -> Optional[dict]:
    """单条详情:返回 prompt/response 全文(详情页按需拉取,避免列表传全文)。"""
    async with async_session_maker() as session:
        res = await session.execute(select(AiApiCall).where(AiApiCall.id == row_id))
        r = res.scalars().first()
        return _to_dict(r) if r else None


async def delete_ai_api_calls_older_than(days: int = 30) -> int:
    if days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    async with async_session_maker() as session:
        stmt = sa_delete(AiApiCall).where(AiApiCall.created_at < cutoff)
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount or 0
