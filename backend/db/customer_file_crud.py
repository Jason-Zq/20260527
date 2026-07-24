"""customer_files + profile_import_tasks CRUD: 客户文件库与导入任务。

客户文件库按 file_code 全局唯一:同一文件重复导入只 re-link,不重复下载/OCR。
OCR 复用:全局查 archive_detect_files 同 file_id 的最新 done 且有 ocr_text 行(脱敏文本)。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import undefer

from db.ai_api_call_crud import _clean_text
from db.engine import async_session_maker
from db.models import ArchiveDetectFile, CustomerFile, ProfileImportTask


# ==================== 序列化 ====================

def _to_task_dict(t: ProfileImportTask) -> dict:
    return {
        "id": t.id,
        "filename": t.filename,
        "client_name": t.client_name,
        "client_id": t.client_id,
        "status": t.status,
        "total_files": t.total_files,
        "processed_files": t.processed_files,
        "reused_count": t.reused_count,
        "relinked_count": t.relinked_count,
        "fresh_ocr_count": t.fresh_ocr_count,
        "failed_count": t.failed_count,
        "extracted_count": t.extracted_count,
        "id_card_count": t.id_card_count,
        "hukou_count": t.hukou_count,
        "degree_cert_count": t.degree_cert_count,
        "birth_cert_count": t.birth_cert_count,
        "current_file": t.current_file,
        "error": t.error,
        "household_id": t.household_id,
        "needs_review_count": t.needs_review_count,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
        "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M:%S") if t.updated_at else "",
    }


def _to_file_dict(f: CustomerFile, *, with_text: bool = False) -> dict:
    d = {
        "id": f.id,
        "file_code": f.file_code,
        "import_task_id": f.import_task_id,
        "client_name": f.client_name,
        "client_id": f.client_id,
        "filename": f.filename,
        "folder_name": f.folder_name,
        "rel_path": f.rel_path,
        "status": f.status,
        "ocr_source": f.ocr_source,
        "mime_type": f.mime_type,
        "page_count": f.page_count,
        "char_count": f.char_count,
        "doc_type": f.doc_type,
        "classify_by": f.classify_by,
        "classify_score": f.classify_score,
        "error_msg": f.error_msg,
        "local_path": f.local_path,
        "file_keep_until": f.file_keep_until.strftime("%Y-%m-%d %H:%M:%S") if f.file_keep_until else None,
        "review_status": f.review_status,
        "review_reason": f.review_reason,
        "quality_score": f.quality_score,
        "created_at": f.created_at.strftime("%Y-%m-%d %H:%M:%S") if f.created_at else "",
        "updated_at": f.updated_at.strftime("%Y-%m-%d %H:%M:%S") if f.updated_at else "",
    }
    if with_text:
        d["ocr_text"] = f.ocr_text
    return d


# ==================== OCR 复用(全局,不 scope progress) ====================

async def find_reusable_ocr(file_code: str) -> Optional[dict]:
    """archive_detect_files 中同 file_id 的最新 status='done' 且 ocr_text 非空行(脱敏文本)。"""
    if not file_code:
        return None
    async with async_session_maker() as session:
        row = (await session.execute(
            select(ArchiveDetectFile)
            .options(undefer(ArchiveDetectFile.ocr_text))
            .where(
                ArchiveDetectFile.file_id == file_code,
                ArchiveDetectFile.status == "done",
                ArchiveDetectFile.ocr_text.isnot(None),
                ArchiveDetectFile.ocr_text != "",
                (ArchiveDetectFile.deleted.is_(False)) | (ArchiveDetectFile.deleted.is_(None)),
            )
            .order_by(ArchiveDetectFile.version.desc(), ArchiveDetectFile.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not row:
            return None
        return {
            "archive_file_id": row.id,
            "ocr_text": row.ocr_text,
            "page_count": row.page_count,
            "char_count": row.char_count,
            "mime_type": row.mime_type,
            "filename": row.filename,
        }


# ==================== 任务 ====================

async def create_import_task(*, filename: str, client_name: str,
                             client_id: Optional[int], total_files: int,
                             household_id: Optional[int] = None) -> dict:
    async with async_session_maker() as session:
        row = ProfileImportTask(
            filename=filename, client_name=client_name, client_id=client_id,
            household_id=household_id,
            status="running", total_files=total_files, processed_files=0,
            reused_count=0, relinked_count=0, fresh_ocr_count=0, failed_count=0,
            extracted_count=0, id_card_count=0, hukou_count=0,
            degree_cert_count=0, birth_cert_count=0,
            created_at=datetime.now(), updated_at=datetime.now(),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_task_dict(row)


async def get_import_task(task_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        row = await session.get(ProfileImportTask, task_id)
        return _to_task_dict(row) if row else None


async def list_import_tasks(*, status: Optional[str] = None, client_name: Optional[str] = None,
                            limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    async with async_session_maker() as session:
        stmt = select(ProfileImportTask)
        cnt = select(func.count(ProfileImportTask.id))
        if status:
            stmt = stmt.where(ProfileImportTask.status == status)
            cnt = cnt.where(ProfileImportTask.status == status)
        if client_name:
            stmt = stmt.where(ProfileImportTask.client_name.ilike(f"%{client_name}%"))
            cnt = cnt.where(ProfileImportTask.client_name.ilike(f"%{client_name}%"))
        total = await session.scalar(cnt) or 0
        rows = (await session.execute(
            stmt.order_by(ProfileImportTask.created_at.desc(), ProfileImportTask.id.desc())
                .limit(limit).offset(offset)
        )).scalars().all()
        return [_to_task_dict(r) for r in rows], total


async def update_task_progress(task_id: int, **fields) -> None:
    """按绝对值更新任务进度字段(计数器/current_file 等)。"""
    if not fields:
        return
    allowed = {
        "processed_files", "reused_count", "relinked_count", "fresh_ocr_count",
        "failed_count", "extracted_count", "id_card_count", "hukou_count",
        "degree_cert_count", "birth_cert_count", "current_file", "needs_review_count",
    }
    values = {k: v for k, v in fields.items() if k in allowed}
    if not values:
        return
    values["updated_at"] = datetime.now()
    async with async_session_maker() as session:
        row = await session.get(ProfileImportTask, task_id)
        if row:
            for k, v in values.items():
                setattr(row, k, v)
            await session.commit()


async def finish_import_task(task_id: int, status: str, error: Optional[str] = None) -> None:
    async with async_session_maker() as session:
        row = await session.get(ProfileImportTask, task_id)
        if row:
            row.status = status
            row.error = _clean_text(error, 2000)
            row.current_file = None
            row.updated_at = datetime.now()
            await session.commit()


# ==================== 文件 ====================

async def upsert_task_files(task_id: int, client_id: Optional[int], files: list) -> dict:
    """按 file_code 幂等落库。

    已有行: re-link 到本 task(+刷新客户/文件名等);status != 'done' 的重置 pending 允许重试,
    status == 'done' 的保留 OCR 不动(计 relinked)。无行: insert pending(计 new)。
    缺 file_code 的行: 合成占位编码,直接标 error(无法下载)。
    返回 {new, relinked}
    """
    new_count = 0
    relinked_count = 0
    async with async_session_maker() as session:
        for i, f in enumerate(files):
            file_code = (f.get("file_code") or "").strip()
            synthetic = False
            if not file_code:
                file_code = f"nocode-{task_id}-{i}"
                synthetic = True
            row = await session.scalar(
                select(CustomerFile).where(CustomerFile.file_code == file_code))
            if row:
                row.import_task_id = task_id
                row.client_id = client_id
                row.client_name = f.get("client_name")
                row.filename = f.get("filename") or row.filename
                row.folder_name = f.get("folder_name")
                row.rel_path = f.get("rel_path")
                if row.status != "done":
                    row.status = "pending"
                    row.error_msg = None
                else:
                    relinked_count += 1
                row.updated_at = datetime.now()
            else:
                row = CustomerFile(
                    file_code=file_code,
                    import_task_id=task_id,
                    client_id=client_id,
                    client_name=f.get("client_name"),
                    filename=f.get("filename"),
                    folder_name=f.get("folder_name"),
                    rel_path=f.get("rel_path"),
                    status="error" if synthetic else "pending",
                    ocr_source="none",
                    classify_by="none",
                    error_msg="缺少文件编码,无法下载" if synthetic else None,
                    created_at=datetime.now(), updated_at=datetime.now(),
                )
                session.add(row)
                if synthetic:
                    # 合成行直接失败,不算新待处理
                    pass
                else:
                    new_count += 1
        await session.commit()
    return {"new": new_count, "relinked": relinked_count}


async def list_task_files(task_id: int, *, limit: int = 500, offset: int = 0) -> tuple[list[dict], int]:
    """任务文件列表(不含 ocr_text 大字段)。"""
    async with async_session_maker() as session:
        stmt = select(CustomerFile).where(CustomerFile.import_task_id == task_id)
        cnt = select(func.count(CustomerFile.id)).where(CustomerFile.import_task_id == task_id)
        total = await session.scalar(cnt) or 0
        rows = (await session.execute(
            stmt.order_by(CustomerFile.id).limit(limit).offset(offset)
        )).scalars().all()
        return [_to_file_dict(r) for r in rows], total


async def list_pending_files(task_id: int) -> list[dict]:
    """本 task 全部待处理文件(pending,按 id 升序)。串行处理用。"""
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(CustomerFile)
            .where(CustomerFile.import_task_id == task_id,
                   CustomerFile.status.in_(["pending", "done"]))
            .order_by(CustomerFile.id)
        )).scalars().all()
        # done 行也要返回:重复导入时需要重新分类/提取(OCR 直接复用)
        return [_to_file_dict(r, with_text=True) for r in rows]


async def get_customer_file(row_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        return _to_file_dict(row, with_text=True) if row else None


async def set_file_status(row_id: int, status: str) -> None:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.status = status
            row.updated_at = datetime.now()
            await session.commit()


async def update_file_ocr(row_id: int, *, status: str, ocr_source: str,
                          ocr_text: Optional[str], mime_type: Optional[str] = None,
                          page_count: Optional[int] = None,
                          char_count: Optional[int] = None) -> None:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.status = status
            row.ocr_source = ocr_source
            row.ocr_text = _clean_text(ocr_text)  # 去 NUL/控制符,不截断(原文全存是决策)
            row.mime_type = mime_type
            row.page_count = page_count
            row.char_count = char_count if char_count is not None else (len(ocr_text) if ocr_text else 0)
            row.updated_at = datetime.now()
            await session.commit()


async def update_file_classify(row_id: int, *, doc_type: Optional[str],
                               classify_by: str, classify_score: Optional[int],
                               status: str = "done") -> None:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.doc_type = doc_type
            row.classify_by = classify_by
            row.classify_score = classify_score
            row.status = status
            row.updated_at = datetime.now()
            await session.commit()


async def mark_file_error(row_id: int, error_msg: str) -> None:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.status = "error"
            row.error_msg = _clean_text(error_msg, 2000)
            row.updated_at = datetime.now()
            await session.commit()


# ==================== 原件留存 + 复核 ====================

async def update_file_local(row_id: int, *, local_path: str, file_keep_until) -> None:
    """记录原件落盘位置与保留截止时间。"""
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.local_path = local_path
            row.file_keep_until = file_keep_until
            row.updated_at = datetime.now()
            await session.commit()


async def clear_file_local(row_id: int) -> None:
    """GC 删除原件后清空落盘记录(DB/OCR 文本保留)。"""
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.local_path = None
            row.file_keep_until = None
            row.updated_at = datetime.now()
            await session.commit()


async def update_file_review(row_id: int, *, quality_score: int,
                             review_status: str, review_reason: Optional[str]) -> None:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.quality_score = quality_score
            row.review_status = review_status
            row.review_reason = review_reason
            row.updated_at = datetime.now()
            await session.commit()


async def set_file_reviewed(row_id: int) -> None:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.review_status = "reviewed"
            row.updated_at = datetime.now()
            await session.commit()


async def list_review_files(*, import_task_id: Optional[int] = None,
                            client_name: Optional[str] = None,
                            reason: Optional[str] = None,
                            include_reviewed: bool = False,
                            limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """复核队列:待复核文件按质量分升序(越急需越靠前)。"""
    async with async_session_maker() as session:
        stmt = select(CustomerFile)
        cnt = select(func.count(CustomerFile.id))
        if include_reviewed:
            stmt = stmt.where(CustomerFile.review_status.in_(["needs_review", "reviewed"]))
            cnt = cnt.where(CustomerFile.review_status.in_(["needs_review", "reviewed"]))
        else:
            stmt = stmt.where(CustomerFile.review_status == "needs_review")
            cnt = cnt.where(CustomerFile.review_status == "needs_review")
        if import_task_id:
            stmt = stmt.where(CustomerFile.import_task_id == import_task_id)
            cnt = cnt.where(CustomerFile.import_task_id == import_task_id)
        if client_name:
            stmt = stmt.where(CustomerFile.client_name.ilike(f"%{client_name}%"))
            cnt = cnt.where(CustomerFile.client_name.ilike(f"%{client_name}%"))
        if reason:
            stmt = stmt.where(CustomerFile.review_reason == reason)
            cnt = cnt.where(CustomerFile.review_reason == reason)
        total = await session.scalar(cnt) or 0
        rows = (await session.execute(
            stmt.order_by(CustomerFile.quality_score.asc().nulls_last(),
                          CustomerFile.id)
                .limit(limit).offset(offset)
        )).scalars().all()
        return [_to_file_dict(r) for r in rows], total


async def count_needs_review(import_task_id: Optional[int] = None) -> int:
    async with async_session_maker() as session:
        stmt = select(func.count(CustomerFile.id)).where(
            CustomerFile.review_status == "needs_review")
        if import_task_id:
            stmt = stmt.where(CustomerFile.import_task_id == import_task_id)
        return await session.scalar(stmt) or 0


async def list_expired_local_files(limit: int = 200) -> list[dict]:
    """原件已到期的文件(GC 用):file_keep_until < now 且 local_path 非空。"""
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(CustomerFile)
            .where(CustomerFile.local_path.isnot(None),
                   CustomerFile.file_keep_until.isnot(None),
                   CustomerFile.file_keep_until < datetime.now())
            .order_by(CustomerFile.file_keep_until)
            .limit(limit)
        )).scalars().all()
        return [{"id": r.id, "local_path": r.local_path} for r in rows]
