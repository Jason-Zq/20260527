"""文件留底检测的数据库 CRUD（archive_detect_batches + archive_detect_files）。

设计上与 split_crud.py 类似：分阶段更新（pending→fetching→ocr→llm→done/error），
每次写都更新 updated_at 便于排查"卡住"的任务。
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional, Iterable
from sqlalchemy import select, update as sa_update, delete as sa_delete
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
                version=spec.get("version") or 1,
                deleted=False,
                created_at=now,
                updated_at=now,
            )
            if reuse:
                # 复用项:直接 done + 拷贝结果字段
                session.add(ArchiveDetectFile(
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
                ))
                reused_count += 1
            else:
                # 新检项:pending 等待 _orchestrate_business 处理
                session.add(ArchiveDetectFile(
                    **common,
                    status="pending",
                ))
                new_count += 1

        # 3) batch.done_files = reused_count(复用项已 done)
        batch.done_files = reused_count
        await session.commit()

    return {"reused_count": reused_count, "new_count": new_count}


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
