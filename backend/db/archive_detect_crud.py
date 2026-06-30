"""文件留底检测的数据库 CRUD（archive_detect_batches + archive_detect_files）。

设计上与 split_crud.py 类似：分阶段更新（pending→fetching→ocr→llm→done/error），
每次写都更新 updated_at 便于排查"卡住"的任务。
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Iterable
from sqlalchemy import select, update as sa_update, delete as sa_delete, func
from sqlalchemy.orm import selectinload, defer, undefer

from db.engine import async_session_maker
from db.models import ArchiveDetectBatch, ArchiveDetectFile, ArchiveDetectProgress, Client


def _file_to_dict(f: ArchiveDetectFile) -> dict:
    """默认不含 ocr_text（大字段，按需在 get_file_detail 单独取）。

    is_reused 由 elapsed_sec < 0.5 推导（复用项 elapsed_sec=0.0）。
    """
    elapsed = float(f.elapsed_sec) if f.elapsed_sec is not None else None
    return {
        "id": f.id,
        "idx": f.idx,
        "progress_id": f.progress_id,
        "file_id": f.file_id,
        "version": f.version,
        "source_url": f.source_url,
        "filename": f.filename,
        "mime_type": f.mime_type,
        "page_count": f.page_count,
        "char_count": f.char_count,
        "is_archival": f.is_archival,
        "confidence": f.confidence,
        "verdict": f.verdict,
        "match_score": f.match_score,
        "reason": f.reason,
        "key_points": f.key_points or [],
        "doc_category": f.doc_category,
        "status": f.status,
        "error_msg": f.error_msg,
        "elapsed_sec": elapsed,
        "is_reused": (f.status == "done" and elapsed is not None and elapsed < 0.5),
    }


def _progress_to_dict(p: ArchiveDetectProgress) -> dict:
    return {
        "id": p.id,
        "client_id": p.client_id,
        "handler": p.handler,
        "project_name": p.project_name,
        "project_code": p.project_code,
        "project_detail_name": p.project_detail_name,
        "project_detail_code": p.project_detail_code,
        "progress_oid": p.progress_oid,
        "progress_name": p.progress_name,
    }


def _client_to_brief_dict(c: Client) -> dict:
    return {
        "id": c.id,
        "client_code": c.client_code,
        "name": c.name,
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
        "overall_verdict": b.overall_verdict,
        "overall_score": b.overall_score,
        "overall_reason": b.overall_reason,
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
    """落库脱敏后的 LLM 结果 + 文件元信息 + 脱敏后 OCR 文本。

    payload 里允许的键见 _file_to_dict + ocr_text（已脱敏）。
    """
    values = {
        "status": "done",
        "filename": payload.get("filename"),
        "mime_type": payload.get("mime_type"),
        "page_count": payload.get("page_count"),
        "char_count": payload.get("char_count"),
        "is_archival": payload.get("is_archival"),
        "confidence": payload.get("confidence"),
        "verdict": payload.get("verdict"),
        "match_score": payload.get("match_score"),
        "reason": payload.get("reason"),
        "key_points": payload.get("key_points"),
        "doc_category": payload.get("doc_category"),
        "ocr_text": payload.get("ocr_text"),
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
    """返回 batch + 所有 files（按 idx 排序）。

    ocr_text 用 defer 延迟加载，避免 N 文件大文本一次性拉出。
    需要看 OCR 文本时调 get_file_detail(file_id)。
    """
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectBatch)
            .options(
                selectinload(ArchiveDetectBatch.files).options(
                    defer(ArchiveDetectFile.ocr_text)
                )
            )
            .where(ArchiveDetectBatch.batch_id == batch_id)
        )
        res = await session.execute(stmt)
        b = res.scalar_one_or_none()
        return _batch_to_dict(b) if b else None


async def get_file_detail(file_id: int) -> Optional[dict]:
    """返回单文件完整记录（含 ocr_text）。供"单文件详情"接口用。

    本阶段只预留 CRUD 函数，路由暴露留给后续阶段。
    """
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectFile)
            .options(undefer(ArchiveDetectFile.ocr_text))
            .where(ArchiveDetectFile.id == file_id)
        )
        res = await session.execute(stmt)
        f = res.scalar_one_or_none()
        if not f:
            return None
        d = _file_to_dict(f)
        d["ocr_text"] = f.ocr_text
        return d


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


async def update_batch_overall(
    batch_id: str,
    overall_verdict: Optional[str],
    overall_score: Optional[int],
    overall_reason: Optional[str],
) -> None:
    """写入批次总报告(overall_*)。在 _orchestrate finally 内,所有文件 done 后调用。"""
    async with async_session_maker() as session:
        stmt = (
            sa_update(ArchiveDetectBatch)
            .where(ArchiveDetectBatch.batch_id == batch_id)
            .values(
                overall_verdict=overall_verdict,
                overall_score=overall_score,
                overall_reason=overall_reason,
                updated_at=datetime.now(),
            )
        )
        await session.execute(stmt)
        await session.commit()


# ==================== 业务接口专用 CRUD ====================


async def upsert_client_by_code(client_code: str, name: str) -> int:
    """按 client_code upsert clients 表,返回 client.id。

    存在则 UPDATE name(若变了),不存在则 INSERT。
    """
    async with async_session_maker() as session:
        stmt = select(Client).where(Client.client_code == client_code)
        res = await session.execute(stmt)
        c = res.scalar_one_or_none()
        if c:
            if c.name != name:
                c.name = name
                c.updated_at = datetime.now()
                await session.commit()
            return c.id
        c = Client(
            client_code=client_code,
            name=name,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return c.id


async def upsert_progress(
    *,
    client_id: int,
    progress_oid: str,
    handler: Optional[str] = None,
    project_name: Optional[str] = None,
    project_code: Optional[str] = None,
    project_detail_name: Optional[str] = None,
    project_detail_code: Optional[str] = None,
    progress_name: Optional[str] = None,
) -> dict:
    """按 (client_id, progress_oid) upsert,返回 progress dict(含 id)。

    存在则更新所有可选字段(handler/项目/进展名,即便为 None 也覆盖),不存在则 INSERT。
    """
    async with async_session_maker() as session:
        stmt = select(ArchiveDetectProgress).where(
            ArchiveDetectProgress.client_id == client_id,
            ArchiveDetectProgress.progress_oid == progress_oid,
        )
        res = await session.execute(stmt)
        p = res.scalar_one_or_none()
        now = datetime.now()
        if p:
            p.handler = handler
            p.project_name = project_name
            p.project_code = project_code
            p.project_detail_name = project_detail_name
            p.project_detail_code = project_detail_code
            p.progress_name = progress_name
            p.updated_at = now
            await session.commit()
            await session.refresh(p)
            return _progress_to_dict(p)
        p = ArchiveDetectProgress(
            client_id=client_id,
            progress_oid=progress_oid,
            handler=handler,
            project_name=project_name,
            project_code=project_code,
            project_detail_name=project_detail_name,
            project_detail_code=project_detail_code,
            progress_name=progress_name,
            created_at=now,
            updated_at=now,
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return _progress_to_dict(p)


async def find_latest_done_file(progress_id: int, file_id: str) -> Optional[dict]:
    """查同 progress 下同 file_id 的最新一条 status=done AND deleted=false 记录。

    用于增量复用判定。返回 dict 含 verdict/match_score/reason/key_points/doc_category/ocr_text/...
    """
    async with async_session_maker() as session:
        # 显式 undefer ocr_text(复用要拷贝它)
        stmt = (
            select(ArchiveDetectFile)
            .options(undefer(ArchiveDetectFile.ocr_text))
            .where(
                ArchiveDetectFile.progress_id == progress_id,
                ArchiveDetectFile.file_id == file_id,
                ArchiveDetectFile.status == "done",
                # deleted 列 nullable,显式判 == false 或 IS NULL 都算未删
                (ArchiveDetectFile.deleted.is_(False)) | (ArchiveDetectFile.deleted.is_(None)),
            )
            .order_by(ArchiveDetectFile.version.desc(), ArchiveDetectFile.created_at.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        f = res.scalar_one_or_none()
        if not f:
            return None
        d = _file_to_dict(f)
        d["ocr_text"] = f.ocr_text
        return d


async def find_latest_done_files_bulk(
    progress_id: int,
    file_ids: list[str],
) -> dict[str, dict]:
    """批量版 find_latest_done_file:一次性查同 progress 下多个 file_id 的最新 done 记录。

    submit 时遍历调单查接口会做 N 次往返,在大 batch + 网络 RTT 高时显著拖慢提交速度。
    本函数一次 SQL 用 DISTINCT ON 抓每个 file_id 的最新一条,返回 {file_id: dict}(不命中的不在 key 里)。
    返回字段含 ocr_text(复用旧 OCR 文本时需要)。

    实现:用 PG 的 DISTINCT ON (file_id) ORDER BY file_id, version DESC, created_at DESC。
    若未来需移植到其它库,可降级为子查询 + window function;当前项目固定 PG,直接用 DISTINCT ON。
    """
    if not file_ids:
        return {}
    # 去重避免重复 ID 浪费参数
    unique_ids = list(dict.fromkeys(file_ids))
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectFile)
            .options(undefer(ArchiveDetectFile.ocr_text))
            .where(
                ArchiveDetectFile.progress_id == progress_id,
                ArchiveDetectFile.file_id.in_(unique_ids),
                ArchiveDetectFile.status == "done",
                (ArchiveDetectFile.deleted.is_(False)) | (ArchiveDetectFile.deleted.is_(None)),
            )
            .order_by(
                ArchiveDetectFile.file_id,
                ArchiveDetectFile.version.desc(),
                ArchiveDetectFile.created_at.desc(),
            )
            .distinct(ArchiveDetectFile.file_id)
        )
        res = await session.execute(stmt)
        out: dict[str, dict] = {}
        for f in res.scalars().all():
            d = _file_to_dict(f)
            d["ocr_text"] = f.ocr_text
            out[f.file_id] = d
        return out


async def create_business_batch_with_files(
    *,
    batch_id: str,
    user_prompt: str,
    progress_id: int,
    items_plan: list,
) -> dict:
    """创建 batch + N 个 file 记录,reuse 项直接复制旧 verdict 等;new 项 status='pending'。

    items_plan: [{file_id, filename, source_url(可None), reuse_from: dict|None, version: int}]

    返回 {reused_count, new_count, done_files} (done_files = reused_count,初始 done 计数)。
    """
    now = datetime.now()
    reused_count = 0
    new_count = 0
    # 收集所有新建 file row,commit 后回填 id 给上层(用于前端跳"详情"链接)
    file_rows: list[ArchiveDetectFile] = []

    async with async_session_maker() as session:
        # 1) 创建 batch
        batch = ArchiveDetectBatch(
            batch_id=batch_id,
            user_prompt=user_prompt,
            source_kind="batch",        # 业务模式统一标 batch
            total_files=len(items_plan),
            done_files=0,                # 会被下面循环里 reuse 项累加
            status="running",
            progress_id=progress_id,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)

        # 2) 创建 file 记录
        for i, spec in enumerate(items_plan):
            reuse = spec.get("reuse_from")
            common = dict(
                batch_id=batch_id,
                idx=i,
                progress_id=progress_id,
                file_id=spec["file_id"],
                filename=spec.get("filename"),
                source_url=spec.get("source_url"),
                local_path=spec.get("local_path"),  # upload 模式:主进程落盘后写 DB,worker 读取
                version=spec.get("version") or 1,
                deleted=False,
                created_at=now,
                updated_at=now,
            )
            if reuse:
                # 复用项:直接 done + 拷贝结果字段
                row = ArchiveDetectFile(
                    **common,
                    status="done",
                    elapsed_sec=Decimal("0.0"),
                    is_archival=reuse.get("is_archival"),
                    confidence=reuse.get("confidence"),
                    verdict=reuse.get("verdict"),
                    match_score=reuse.get("match_score"),
                    reason=reuse.get("reason"),
                    key_points=reuse.get("key_points"),
                    doc_category=reuse.get("doc_category"),
                    ocr_text=reuse.get("ocr_text"),
                    page_count=reuse.get("page_count"),
                    char_count=reuse.get("char_count"),
                )
                session.add(row)
                file_rows.append(row)
                reused_count += 1
            else:
                # 新检项:pending 等待 worker 处理
                row = ArchiveDetectFile(
                    **common,
                    status="pending",
                )
                session.add(row)
                file_rows.append(row)
                new_count += 1

        # 3) batch.done_files = reused_count(复用项已 done)
        batch.done_files = reused_count
        await session.commit()
        # commit 后 row.id 才被 PG 回填;按插入顺序对应 items_plan 的 idx
        idx_to_id = {i: row.id for i, row in enumerate(file_rows)}

    return {"reused_count": reused_count, "new_count": new_count, "idx_to_id": idx_to_id}


async def get_business_batch(batch_id: str) -> Optional[dict]:
    """业务接口专用 get:返回 batch + files + client + progress 完整信息。

    ocr_text 仍 defer,文件级查看走 get_file_detail。
    """
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectBatch)
            .options(
                selectinload(ArchiveDetectBatch.files).options(
                    defer(ArchiveDetectFile.ocr_text)
                ),
                selectinload(ArchiveDetectBatch.progress),
            )
            .where(ArchiveDetectBatch.batch_id == batch_id)
        )
        res = await session.execute(stmt)
        b = res.scalar_one_or_none()
        if not b:
            return None

        # 组装基础 batch dict(含 overall_*)
        out = {
            "batch_id": b.batch_id,
            "criteria": b.user_prompt,
            "user_prompt": b.user_prompt,   # 兼容字段
            "source_kind": b.source_kind,
            "total_files": b.total_files,
            "done_files": b.done_files,
            "status": b.status,
            "error": b.error,
            "overall_verdict": b.overall_verdict,
            "overall_score": b.overall_score,
            "overall_reason": b.overall_reason,
            "created_at": b.created_at.strftime("%Y-%m-%d %H:%M:%S") if b.created_at else "",
            "updated_at": b.updated_at.strftime("%Y-%m-%d %H:%M:%S") if b.updated_at else "",
            "files": [_file_to_dict(f) for f in (b.files or [])],
        }

        # 进展 + 客户透传
        if b.progress:
            p = b.progress
            out["progress"] = _progress_to_dict(p)
            # 取 client 简要信息
            client_stmt = select(Client).where(Client.id == p.client_id)
            cres = await session.execute(client_stmt)
            c = cres.scalar_one_or_none()
            out["client"] = _client_to_brief_dict(c) if c else None
        else:
            out["progress"] = None
            out["client"] = None

        # 统计 reused/new (基于 is_reused 推导)
        files = out["files"]
        reused = sum(1 for f in files if f.get("is_reused"))
        out["reused_count"] = reused
        out["new_count"] = len(files) - reused

        return out


async def get_batch_files_for_recheck(batch_id: str) -> Optional[dict]:
    """返回原 batch + files(含 ocr_text) + progress/client(若有),供重新审核使用。"""
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectBatch)
            .options(
                selectinload(ArchiveDetectBatch.files).options(
                    undefer(ArchiveDetectFile.ocr_text)
                ),
                selectinload(ArchiveDetectBatch.progress),
            )
            .where(ArchiveDetectBatch.batch_id == batch_id)
        )
        res = await session.execute(stmt)
        b = res.scalar_one_or_none()
        if not b:
            return None

        out = _batch_to_dict(b, include_files=False)
        out["progress_id"] = b.progress_id
        out["progress"] = _progress_to_dict(b.progress) if b.progress else None
        out["client"] = None
        if b.progress:
            cres = await session.execute(select(Client).where(Client.id == b.progress.client_id))
            c = cres.scalar_one_or_none()
            out["client"] = _client_to_brief_dict(c) if c else None

        files = []
        for f in b.files or []:
            d = _file_to_dict(f)
            d["ocr_text"] = f.ocr_text
            files.append(d)
        out["files"] = files
        return out


async def create_recheck_batch_with_files(
    *,
    source_batch: dict,
    new_batch_id: str,
    criteria: str,
    items_plan: list[dict],
) -> dict:
    """创建 recheck batch + N 个 pending file。

    recheck 的 source_kind='recheck'。progress_id 沿用原 batch(如果有)。
    所有 file 初始 pending,由 _process_one_recheck 决定 AI-only 或重新 OCR。
    """
    now = datetime.now()
    progress_id = source_batch.get("progress_id")
    async with async_session_maker() as session:
        batch = ArchiveDetectBatch(
            batch_id=new_batch_id,
            user_prompt=criteria,
            source_kind="recheck",
            total_files=len(items_plan),
            done_files=0,
            status="running",
            progress_id=progress_id,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        for i, spec in enumerate(items_plan):
            session.add(ArchiveDetectFile(
                batch_id=new_batch_id,
                idx=i,
                progress_id=progress_id,
                file_id=spec.get("file_id"),
                version=spec.get("version") or 1,
                source_url=spec.get("source_url"),
                filename=spec.get("filename"),
                mime_type=spec.get("mime_type"),
                status="pending",
                deleted=False,
                created_at=now,
                updated_at=now,
            ))
        await session.commit()
    return {"total_files": len(items_plan)}


# ==================== 后台管理/监控只读查询 ====================

async def admin_list_batches(
    *,
    status: Optional[str] = None,
    source_kind: Optional[str] = None,
    client_code: Optional[str] = None,
    client_name: Optional[str] = None,
    progress_oid: Optional[str] = None,
    progress_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """后台批次列表:join progress/client,支持基础筛选。"""
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectBatch, ArchiveDetectProgress, Client)
            .outerjoin(ArchiveDetectProgress, ArchiveDetectBatch.progress_id == ArchiveDetectProgress.id)
            .outerjoin(Client, ArchiveDetectProgress.client_id == Client.id)
        )
        count_stmt = (
            select(func.count())
            .select_from(ArchiveDetectBatch)
            .outerjoin(ArchiveDetectProgress, ArchiveDetectBatch.progress_id == ArchiveDetectProgress.id)
            .outerjoin(Client, ArchiveDetectProgress.client_id == Client.id)
        )

        conditions = []
        if status:
            conditions.append(ArchiveDetectBatch.status == status)
        if source_kind:
            conditions.append(ArchiveDetectBatch.source_kind == source_kind)
        if client_code:
            conditions.append(Client.client_code.ilike(f"%{client_code}%"))
        if client_name:
            conditions.append(Client.name.ilike(f"%{client_name}%"))
        if progress_oid:
            conditions.append(ArchiveDetectProgress.progress_oid.ilike(f"%{progress_oid}%"))
        if progress_name:
            conditions.append(ArchiveDetectProgress.progress_name.ilike(f"%{progress_name}%"))
        if date_from:
            start = datetime.strptime(date_from, "%Y-%m-%d")
            conditions.append(ArchiveDetectBatch.created_at >= start)
        if date_to:
            end = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            conditions.append(ArchiveDetectBatch.created_at < end)
        for cond in conditions:
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        stmt = stmt.order_by(ArchiveDetectBatch.created_at.desc()).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).all()
        total = (await session.execute(count_stmt)).scalar() or 0

        items = []
        for b, p, c in rows:
            items.append({
                "batch_id": b.batch_id,
                "source_kind": b.source_kind,
                "status": b.status,
                "total_files": b.total_files,
                "done_files": b.done_files,
                "overall_verdict": b.overall_verdict,
                "overall_score": b.overall_score,
                "overall_reason": b.overall_reason,
                "created_at": b.created_at.strftime("%Y-%m-%d %H:%M:%S") if b.created_at else "",
                "updated_at": b.updated_at.strftime("%Y-%m-%d %H:%M:%S") if b.updated_at else "",
                "client": _client_to_brief_dict(c) if c else None,
                "progress": _progress_to_dict(p) if p else None,
            })
        return {"items": items, "total": total}


async def admin_list_progress(
    *,
    client_code: Optional[str] = None,
    client_name: Optional[str] = None,
    handler: Optional[str] = None,
    project_name: Optional[str] = None,
    progress_oid: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """后台进展包列表:join client,返回进展基本信息。"""
    async with async_session_maker() as session:
        stmt = select(ArchiveDetectProgress, Client).join(Client, ArchiveDetectProgress.client_id == Client.id)
        count_stmt = select(func.count()).select_from(ArchiveDetectProgress).join(Client, ArchiveDetectProgress.client_id == Client.id)
        conditions = []
        if client_code:
            conditions.append(Client.client_code.ilike(f"%{client_code}%"))
        if client_name:
            conditions.append(Client.name.ilike(f"%{client_name}%"))
        if handler:
            conditions.append(ArchiveDetectProgress.handler.ilike(f"%{handler}%"))
        if project_name:
            conditions.append(ArchiveDetectProgress.project_name.ilike(f"%{project_name}%"))
        if progress_oid:
            conditions.append(ArchiveDetectProgress.progress_oid.ilike(f"%{progress_oid}%"))
        for cond in conditions:
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        stmt = stmt.order_by(ArchiveDetectProgress.updated_at.desc()).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).all()
        total = (await session.execute(count_stmt)).scalar() or 0
        items = []
        for p, c in rows:
            item = _progress_to_dict(p)
            item["client"] = _client_to_brief_dict(c)
            item["created_at"] = p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else ""
            item["updated_at"] = p.updated_at.strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else ""
            items.append(item)
        return {"items": items, "total": total}


async def admin_get_file_detail(record_id: int) -> Optional[dict]:
    """后台单文件详情:含 ocr_text + batch/progress/client 简要信息。"""
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectFile)
            .options(undefer(ArchiveDetectFile.ocr_text))
            .where(ArchiveDetectFile.id == record_id)
        )
        f = (await session.execute(stmt)).scalar_one_or_none()
        if not f:
            return None
        d = _file_to_dict(f)
        d["ocr_text"] = f.ocr_text
        d["batch_id"] = f.batch_id

        if f.progress_id:
            p = (await session.execute(
                select(ArchiveDetectProgress).where(ArchiveDetectProgress.id == f.progress_id)
            )).scalar_one_or_none()
            d["progress"] = _progress_to_dict(p) if p else None
            if p:
                c = (await session.execute(select(Client).where(Client.id == p.client_id))).scalar_one_or_none()
                d["client"] = _client_to_brief_dict(c) if c else None
        else:
            d["progress"] = None
            d["client"] = None
        return d


# ==================== 方案二 2b: DB 队列 Worker 调度 ====================

from sqlalchemy import text as sa_text

# 单 worker lease 时长(秒)。一个文件 OCR + LLM 最长 ~5 分钟,这里给 10 分钟
# 超时未更新即被 watchdog 回收(认为 worker 进程崩了)
DEFAULT_LEASE_SECONDS = 600

# 单文件 retry 上限
MAX_RETRY_COUNT = 1


async def claim_one_pending_file(
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Optional[dict]:
    """worker 从 DB 抢一个 pending 文件,原子操作(SKIP LOCKED 防多 worker 抢同行)。

    返回单文件 dict(含 batch_id/idx/source_url/filename/file_id/progress_id/version),
    无 pending 任务时返回 None。

    注:worker_id 当前只用于事件日志,不写 DB(防止 DB 表加列;后期需要观测可加 worker_id 字段)。
    """
    # 用 raw SQL 因为 SQLAlchemy 对 UPDATE FROM SELECT FOR UPDATE SKIP LOCKED 支持不够直接
    sql = sa_text(f"""
        UPDATE archive_detect_files
        SET status = 'leased',
            worker_lease_until = now() + interval '{int(lease_seconds)} seconds',
            updated_at = now()
        WHERE id = (
            SELECT id FROM archive_detect_files
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING
            id, batch_id, idx, progress_id, file_id, version,
            source_url, local_path, filename, mime_type, retry_count
    """)
    async with async_session_maker() as session:
        res = await session.execute(sql)
        row = res.mappings().first()
        await session.commit()
        if row is None:
            return None
        return dict(row)


async def count_pending_files() -> int:
    """submit 时查当前 pending 数,做水位线判断用。"""
    async with async_session_maker() as session:
        stmt = (
            select(func.count())
            .select_from(ArchiveDetectFile)
            .where(ArchiveDetectFile.status.in_(["pending", "leased", "fetching", "ocr", "llm"]))
        )
        res = await session.execute(stmt)
        return int(res.scalar_one())


async def reclaim_expired_leases(
    max_retry: int = MAX_RETRY_COUNT,
) -> dict:
    """watchdog 周期调用:超时的 leased 任务根据 retry_count 决定回 pending 还是终态 error。

    - retry_count < max_retry:回 pending,retry_count + 1,清 lease
    - retry_count >= max_retry:置 error,清 lease,error_msg 记原因

    返回 {requeued: int, killed: int, requeued_ids: list[int], killed_ids: list[int]}
    """
    # 1) 先查所有过期 leased 行
    now = datetime.now()
    async with async_session_maker() as session:
        stmt = select(
            ArchiveDetectFile.id,
            ArchiveDetectFile.batch_id,
            ArchiveDetectFile.idx,
            ArchiveDetectFile.retry_count,
        ).where(
            ArchiveDetectFile.status == "leased",
            ArchiveDetectFile.worker_lease_until < now,
        )
        res = await session.execute(stmt)
        rows = res.all()

    if not rows:
        return {"requeued": 0, "killed": 0, "requeued_ids": [], "killed_ids": []}

    requeued_ids = [r.id for r in rows if r.retry_count < max_retry]
    killed_ids = [r.id for r in rows if r.retry_count >= max_retry]

    async with async_session_maker() as session:
        # 2) 可重试的:回 pending,retry + 1
        if requeued_ids:
            await session.execute(sa_text(
                "UPDATE archive_detect_files "
                "SET status = 'pending', worker_lease_until = NULL, "
                "    retry_count = retry_count + 1, updated_at = now() "
                "WHERE id = ANY(:ids)"
            ), {"ids": requeued_ids})

        # 3) 不可重试的:终态 error
        if killed_ids:
            await session.execute(sa_text(
                "UPDATE archive_detect_files "
                "SET status = 'error', worker_lease_until = NULL, "
                "    error_msg = COALESCE(error_msg, 'worker 多次失败,放弃重试'), "
                "    updated_at = now() "
                "WHERE id = ANY(:ids)"
            ), {"ids": killed_ids})

        await session.commit()

    return {
        "requeued": len(requeued_ids),
        "killed": len(killed_ids),
        "requeued_ids": requeued_ids,
        "killed_ids": killed_ids,
    }


async def update_file_intermediate_status(file_id: int, status: str) -> None:
    """worker 处理过程中更新中间状态(fetching/ocr/llm),供前端轮询看进度。
    顺便延长 lease,防止 watchdog 误回收正在跑的任务。
    """
    # interval 后不接受参数化占位符,lease 秒数写死在 SQL 常量中(DEFAULT_LEASE_SECONDS=600)
    async with async_session_maker() as session:
        await session.execute(sa_text(f"""
            UPDATE archive_detect_files
            SET status = :status,
                worker_lease_until = now() + interval '{DEFAULT_LEASE_SECONDS} seconds',
                updated_at = now()
            WHERE id = :id
        """), {"id": file_id, "status": status})
        await session.commit()


async def get_batch_progress(batch_id: str) -> dict:
    """主进程 finalize 轮询使用:查批次完成度。

    返回 {done: int, error: int, running: int, total: int, is_complete: bool}
    is_complete: 没有任何非终态行
    """
    async with async_session_maker() as session:
        stmt = sa_text("""
            SELECT
              COUNT(*) FILTER (WHERE status = 'done')                 AS done,
              COUNT(*) FILTER (WHERE status = 'error')                AS error_cnt,
              COUNT(*) FILTER (WHERE status IN ('pending','leased','fetching','ocr','llm'))
                                                                       AS running,
              COUNT(*)                                                 AS total
            FROM archive_detect_files
            WHERE batch_id = :bid
        """)
        res = await session.execute(stmt, {"bid": batch_id})
        row = res.mappings().first()
        if row is None:
            return {"done": 0, "error": 0, "running": 0, "total": 0, "is_complete": True}
        d = dict(row)
        d["error"] = d.pop("error_cnt")
        d["is_complete"] = (d["running"] == 0 and d["total"] > 0)
        return d


async def list_running_batch_ids() -> list[str]:
    """主进程启动恢复用:查所有 status='running' 的 batch_id,重新启动 finalize 轮询。"""
    async with async_session_maker() as session:
        stmt = select(ArchiveDetectBatch.batch_id).where(
            ArchiveDetectBatch.status == "running"
        )
        res = await session.execute(stmt)
        return [r[0] for r in res.all()]


async def get_batch_meta(batch_id: str) -> Optional[dict]:
    """finalize 需要的 batch 元信息:criteria/progress_id/source_kind。"""
    async with async_session_maker() as session:
        stmt = select(ArchiveDetectBatch).where(ArchiveDetectBatch.batch_id == batch_id)
        res = await session.execute(stmt)
        b = res.scalar_one_or_none()
        if not b:
            return None
        return {
            "batch_id": b.batch_id,
            "user_prompt": b.user_prompt,
            "source_kind": b.source_kind,
            "progress_id": b.progress_id,
            "status": b.status,
        }


async def get_batch_files_simple(batch_id: str) -> list[dict]:
    """finalize 时读 batch 内所有文件的判定结果(不含 ocr_text),用于 compute_overall。"""
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectFile)
            .where(ArchiveDetectFile.batch_id == batch_id)
            .order_by(ArchiveDetectFile.idx)
        )
        res = await session.execute(stmt)
        return [_file_to_dict(f) for f in res.scalars().all()]
