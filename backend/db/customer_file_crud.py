"""customer_files + profile_import_tasks CRUD: 客户文件库与导入任务。

客户文件库按 file_code 全局唯一:同一文件重复导入只 re-link,不重复下载/OCR。
OCR 复用:全局查 archive_detect_files 同 file_id 的最新 done 且有 ocr_text 行(脱敏文本)。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import cast, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import undefer

from db.ai_api_call_crud import _clean_text
from db.engine import async_session_maker
from db.models import (
    ArchiveDetectFile, CustomerFile, DocExtractResult, ProfileAsset,
    ProfileHousehold, ProfileImportTask, ProfilePerson, ProfilePersonField,
)


# ==================== 序列化 ====================

def _to_task_dict(t: ProfileImportTask, *,
                   asset_counts: Optional[dict[int, int]] = None,
                   main_passport: Optional[dict[int, dict]] = None,
                   household_codes: Optional[dict[int, str]] = None) -> dict:
    base = {
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
    if household_codes is not None:
        base["customer_code"] = household_codes.get(t.household_id) if t.household_id else None
    if asset_counts is not None and t.household_id:
        base["asset_count"] = asset_counts.get(t.household_id, 0)
    if main_passport is not None and t.household_id:
        p = main_passport.get(t.household_id, {})
        base["main_passport_issue_date"] = p.get("issue_date")
        base["main_passport_expiry_date"] = p.get("expiry_date")
    return base


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
        "person_id": f.person_id,
        "affter_entryoid": f.affter_entryoid,
        "project_name": f.project_name,
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
                            customer_code: Optional[str] = None,
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
        if customer_code:
            hh_by_code = select(ProfileHousehold.id).where(
                ProfileHousehold.customer_code.ilike(f"%{customer_code}%"))
            stmt = stmt.where(ProfileImportTask.household_id.in_(hh_by_code))
            cnt = cnt.where(ProfileImportTask.household_id.in_(hh_by_code))
        total = await session.scalar(cnt) or 0
        rows = (await session.execute(
            stmt.order_by(ProfileImportTask.created_at.desc(), ProfileImportTask.id.desc())
                .limit(limit).offset(offset)
        )).scalars().all()

        # 附加家庭客户编码 + 家庭资产数 + 户主护照签发/到期日期(表格快速筛查用)
        hh_ids = {r.household_id for r in rows if r.household_id}
        household_codes: dict[int, str] = {}
        asset_counts: dict[int, int] = {}
        main_passport: dict[int, dict] = {}
        if hh_ids:
            code_rows = (await session.execute(
                select(ProfileHousehold.id, ProfileHousehold.customer_code)
                .where(ProfileHousehold.id.in_(hh_ids))
            )).all()
            household_codes = {hid: code for hid, code in code_rows if code}

            ac_rows = (await session.execute(
                select(ProfileAsset.household_id, func.count(ProfileAsset.id))
                .where(ProfileAsset.household_id.in_(hh_ids))
                .group_by(ProfileAsset.household_id)
            )).all()
            asset_counts = {hid: n for hid, n in ac_rows}

            pp_rows = (await session.execute(
                select(ProfilePerson.household_id, ProfilePersonField.field, ProfilePersonField.value)
                .join(ProfilePersonField, ProfilePersonField.person_id == ProfilePerson.id)
                .where(ProfilePerson.is_main.is_(True),
                       ProfilePerson.household_id.in_(hh_ids),
                       ProfilePersonField.field.in_(("passport_issue_date", "passport_expiry_date")))
            )).all()
            for hid, field, value in pp_rows:
                slot = main_passport.setdefault(hid, {})
                if field == "passport_issue_date":
                    slot["issue_date"] = value
                elif field == "passport_expiry_date":
                    slot["expiry_date"] = value

        return [_to_task_dict(r, asset_counts=asset_counts, main_passport=main_passport,
                              household_codes=household_codes)
                for r in rows], total


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


async def list_household_files(household_id: int) -> list[dict]:
    """家庭名下全部客户文件(跨任务并集),重新生成画像的输入。

    file_code 全局唯一,同一文件被多任务先后 link 时只有当前所在任务的一行,
    天然无重复;只返回 upsert_task_files 需要的字段。
    """
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(CustomerFile)
            .join(ProfileImportTask,
                  CustomerFile.import_task_id == ProfileImportTask.id)
            .where(ProfileImportTask.household_id == household_id)
            .order_by(CustomerFile.id)
        )).scalars().all()
        return [{
            "file_code": r.file_code,
            "filename": r.filename,
            "folder_name": r.folder_name,
            "rel_path": r.rel_path,
            "client_name": r.client_name,
            "affter_entryoid": r.affter_entryoid,
            "project_name": r.project_name,
        } for r in rows]


async def get_latest_task_for_household(household_id: int) -> Optional[dict]:
    """家庭最近一个导入任务(重新生成画像复用的宿主任务)。"""
    async with async_session_maker() as session:
        row = (await session.execute(
            select(ProfileImportTask)
            .where(ProfileImportTask.household_id == household_id)
            .order_by(ProfileImportTask.id.desc())
            .limit(1)
        )).scalars().first()
        return _to_task_dict(row) if row else None


async def reset_import_task(task_id: int, *, total_files: int) -> None:
    """把任务重置回 running 初始态,供重新生成画像原地重跑(不新建任务)。"""
    async with async_session_maker() as session:
        row = await session.get(ProfileImportTask, task_id)
        if row:
            row.status = "running"
            row.error = None
            row.current_file = None
            row.total_files = total_files
            row.processed_files = 0
            row.reused_count = 0
            row.relinked_count = 0
            row.fresh_ocr_count = 0
            row.failed_count = 0
            row.extracted_count = 0
            row.id_card_count = 0
            row.hukou_count = 0
            row.degree_cert_count = 0
            row.birth_cert_count = 0
            row.needs_review_count = 0
            row.updated_at = datetime.now()
            await session.commit()


async def has_running_task(household_id: int) -> bool:
    """家庭是否有 running 状态任务(重新生成的并发保护)。"""
    async with async_session_maker() as session:
        return (await session.execute(
            select(func.count(ProfileImportTask.id)).where(
                ProfileImportTask.household_id == household_id,
                ProfileImportTask.status == "running")
        )).scalar_one() > 0


async def delete_household_profile(household_id: int) -> tuple[bool, list[str], dict]:
    """删除画像:只删客户画像数据(家庭/人员/字段/资产/案件),文件与 OCR 全保留。

    只 DELETE profile_households 行:DB CASCADE 删 persons/person_fields/assets/cases;
    profile_import_tasks.household_id 被 FK(SET NULL)自动置空 → 任务/customer_files/
    ocr_text/doc_extract_results/磁盘原件全部保留,重新导入可按 file_code re-link 复用 OCR。
    customer_files.person_id 是指向画像域的裸列(无 FK),删前清 NULL 避免悬挂。
    返回 (是否删除成功, 恒空路径列表, {"tasks": 任务数, "files_kept": 保留文件数})。
    """
    async with async_session_maker() as session:
        if await session.get(ProfileHousehold, household_id) is None:
            return False, [], {"tasks": 0, "files_kept": 0}
        task_ids = (await session.execute(
            select(ProfileImportTask.id).where(
                ProfileImportTask.household_id == household_id)
        )).scalars().all()
        files_kept = 0
        if task_ids:
            files_kept = (await session.execute(
                select(func.count(CustomerFile.id)).where(
                    CustomerFile.import_task_id.in_(task_ids))
            )).scalar_one()
            await session.execute(
                update(CustomerFile).where(CustomerFile.import_task_id.in_(task_ids))
                .values(person_id=None))
        await session.execute(
            sa_delete(ProfileHousehold).where(ProfileHousehold.id == household_id))
        await session.commit()
        return True, [], {"tasks": len(task_ids), "files_kept": files_kept}


async def delete_import_task(task_id: int) -> tuple[bool, list[str], dict]:
    """删除导入任务。

    任务有 household_id 时 = 删除画像(委托 delete_household_profile:只删画像数据,
    任务/文件/OCR/提取结果/磁盘原件全保留);无 household 时删任务级数据
    (customer_files/doc_extract_results 由 DB CASCADE 级联,返回磁盘路径)。
    返回 (是否删除, local_path 列表, 统计 dict)。
    """
    async with async_session_maker() as session:
        household_id = (await session.execute(
            select(ProfileImportTask.household_id).where(ProfileImportTask.id == task_id)
        )).scalar_one_or_none()
    if household_id is not None:
        deleted, paths, stats = await delete_household_profile(household_id)
        stats["household_deleted"] = deleted
        return deleted, paths, stats
    async with async_session_maker() as session:
        paths = (await session.execute(
            select(CustomerFile.local_path).where(
                CustomerFile.import_task_id == task_id,
                CustomerFile.local_path.isnot(None),
            )
        )).scalars().all()
        res = await session.execute(
            sa_delete(ProfileImportTask).where(ProfileImportTask.id == task_id))
        await session.commit()
        return (res.rowcount or 0) > 0, [p for p in paths if p], {
            "tasks": 1, "household_deleted": False}


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
                row.affter_entryoid = f.get("affter_entryoid")
                row.project_name = f.get("project_name")
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
                    affter_entryoid=f.get("affter_entryoid"),
                    project_name=f.get("project_name"),
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
    """任务文件列表(不含 ocr_text 大字段;带最新提取状态)。"""
    async with async_session_maker() as session:
        stmt = select(CustomerFile).where(CustomerFile.import_task_id == task_id)
        cnt = select(func.count(CustomerFile.id)).where(CustomerFile.import_task_id == task_id)
        total = await session.scalar(cnt) or 0
        rows = (await session.execute(
            stmt.order_by(CustomerFile.id).limit(limit).offset(offset)
        )).scalars().all()
        # 批量查每个文件最新一条提取结果的状态(DISTINCT ON 取 id 最大=最新)
        latest_status: dict[int, str] = {}
        if rows:
            cfids = [r.id for r in rows]
            sub = (
                select(DocExtractResult.customer_file_id, DocExtractResult.status)
                .where(DocExtractResult.customer_file_id.in_(cfids))
                .order_by(DocExtractResult.customer_file_id, DocExtractResult.id.desc())
                .distinct(DocExtractResult.customer_file_id)
            )
            for cfid, status in (await session.execute(sub)).all():
                latest_status[cfid] = status
        out = []
        for r in rows:
            d = _to_file_dict(r)
            d["latest_extract_status"] = latest_status.get(r.id)
            out.append(d)
        return out, total


async def list_person_files(person_id: int) -> list[dict]:
    """该人员关联的文件(并集):① customer_files.person_id 手动归属
    ② 提取结果 write_stats 顶层 person_id ③ write_stats.persons[] 多人模式明细。按 id 升序。"""
    async with async_session_maker() as session:
        cfids = set((await session.execute(
            select(CustomerFile.id).where(CustomerFile.person_id == person_id)
        )).scalars().all())
        cfids.update((await session.execute(
            select(DocExtractResult.customer_file_id)
            .where(or_(
                DocExtractResult.write_stats["person_id"].as_string() == str(person_id),
                DocExtractResult.write_stats.contains({"persons": [{"person_id": person_id}]}),
            ))
            .distinct()
        )).scalars().all())
        if not cfids:
            return []
        rows = (await session.execute(
            select(CustomerFile).where(CustomerFile.id.in_(cfids))
            .order_by(CustomerFile.id)
        )).scalars().all()
        return [_to_file_dict(r) for r in rows]


async def list_asset_files(household_id: int) -> list[dict]:
    """家庭资产相关文件:全部资产 source_file_id 去重对应的 customer_files 行,按 id 升序。"""
    async with async_session_maker() as session:
        cfids = (await session.execute(
            select(ProfileAsset.source_file_id)
            .where(ProfileAsset.household_id == household_id,
                   ProfileAsset.source_file_id.is_not(None))
            .distinct()
        )).scalars().all()
        if not cfids:
            return []
        rows = (await session.execute(
            select(CustomerFile).where(CustomerFile.id.in_(cfids))
            .order_by(CustomerFile.id)
        )).scalars().all()
        return [_to_file_dict(r) for r in rows]


async def assign_file_person(row_id: int, person_id: Optional[int]) -> None:
    """设置/清除文件手动归属人(person_id=None 为清除)。"""
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.person_id = person_id
            row.updated_at = datetime.now()
            await session.commit()


def _result_person_ids(write_stats: Optional[dict]) -> list:
    """write_stats → 归因 person_id 列表(顶层单人 + persons[] 多人明细)。
    与 profile_crud._result_person_ids 同逻辑;本地复制避免 crud 模块互相 import。"""
    ws = write_stats or {}
    ids = [p.get("person_id") for p in ws.get("persons") or [] if p.get("person_id")]
    if not ids and ws.get("person_id"):
        ids = [ws["person_id"]]
    return ids


async def list_files_for_assignment(*, client_name: Optional[str] = None,
                                    doc_type: Optional[str] = None,
                                    assigned: Optional[str] = None,
                                    file_name: Optional[str] = None,
                                    limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """文件归属页全局文件列表(分页+筛选)。每行带 household_id、归属人(列优先,提取归因兜底)、
    attributed_by(manual/extract/None)。file_name 同时模糊匹配 filename 与 file_code(列表展示 filename || file_code)。"""
    async with async_session_maker() as session:
        # 有效归属(person_id 列 ∪ write_stats 归因)的 EXISTS 条件,assigned 筛选用
        attr_exists = exists(
            select(DocExtractResult.id)
            .where(DocExtractResult.customer_file_id == CustomerFile.id)
            .where(or_(
                DocExtractResult.write_stats["person_id"].as_string().isnot(None),
                func.jsonb_path_exists(
                    DocExtractResult.write_stats,
                    cast('$.persons[*].person_id ? (@ != null)', JSONPATH)),
            ))
        )
        filters = []
        if client_name:
            filters.append(CustomerFile.client_name.ilike(f"%{client_name}%"))
        if doc_type:
            filters.append(CustomerFile.doc_type == doc_type)
        if file_name:
            filters.append(or_(
                CustomerFile.filename.ilike(f"%{file_name}%"),
                CustomerFile.file_code.ilike(f"%{file_name}%"),
            ))
        if assigned == "none":
            filters += [CustomerFile.person_id.is_(None), ~attr_exists]
        elif assigned == "any":
            filters.append(or_(CustomerFile.person_id.isnot(None), attr_exists))

        total = await session.scalar(
            select(func.count(CustomerFile.id)).where(*filters)) or 0
        rows = (await session.execute(
            select(CustomerFile, ProfileImportTask.household_id)
            .join(ProfileImportTask, CustomerFile.import_task_id == ProfileImportTask.id)
            .where(*filters)
            .order_by(CustomerFile.id.desc())
            .limit(limit).offset(offset)
        )).all()

        files = []
        for cf, hid in rows:
            d = _to_file_dict(cf)
            d["household_id"] = hid
            d["attributed_by"] = "manual" if d["person_id"] else None
            files.append(d)
        by_id = {f["id"]: f for f in files}
        # 归属人兜底:无手动归属时取最新一条提取结果的 write_stats 归因
        need = [fid for fid, f in by_id.items() if not f["person_id"]]
        if need:
            sub = (select(DocExtractResult.customer_file_id, DocExtractResult.write_stats)
                   .where(DocExtractResult.customer_file_id.in_(need))
                   .order_by(DocExtractResult.customer_file_id, DocExtractResult.id.desc())
                   .distinct(DocExtractResult.customer_file_id))
            for cfid, ws in (await session.execute(sub)).all():
                ids = _result_person_ids(ws)
                if ids:
                    by_id[cfid]["person_id"] = ids[0]
                    by_id[cfid]["attributed_by"] = "extract"
        pids = {f["person_id"] for f in files if f.get("person_id")}
        names = {}
        if pids:
            for pid, pname in (await session.execute(
                    select(ProfilePerson.id, ProfilePerson.name)
                    .where(ProfilePerson.id.in_(pids)))).all():
                names[pid] = pname
        for f in files:
            f["person_name"] = names.get(f.get("person_id"))
        return files, total


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


async def list_unfinished_files(task_id: int) -> list[dict]:
    """本 task 未完成文件(pending+error,按 id 升序)。断点续跑用:done 行不重跑。

    error 行不需先重置:_process_one_file 对非 done 行自然走 fetch/OCR 重试路径。
    """
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(CustomerFile)
            .where(CustomerFile.import_task_id == task_id,
                   CustomerFile.status.in_(["pending", "error"]))
            .order_by(CustomerFile.id)
        )).scalars().all()
        return [_to_file_dict(r, with_text=True) for r in rows]


async def list_running_import_tasks() -> list[dict]:
    """全部 running 状态导入任务(按 id 升序)。恢复中断任务端点用:
    与 profile_import_service 活跃登记对照,不在活跃集合里的即 stale。"""
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(ProfileImportTask)
            .where(ProfileImportTask.status == "running")
            .order_by(ProfileImportTask.id)
        )).scalars().all()
        return [_to_task_dict(r) for r in rows]


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
                          char_count: Optional[int] = None,
                          content_sha256: Optional[str] = None) -> None:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        if row:
            row.status = status
            row.ocr_source = ocr_source
            row.ocr_text = _clean_text(ocr_text)  # 去 NUL/控制符,不截断(原文全存是决策)
            row.mime_type = mime_type
            row.page_count = page_count
            row.char_count = char_count if char_count is not None else (len(ocr_text) if ocr_text else 0)
            if content_sha256 is not None:
                row.content_sha256 = content_sha256
            row.updated_at = datetime.now()
            await session.commit()


async def find_household_dup_ocr(household_id: int, sha256: str, *,
                                 exclude_id: Optional[int] = None) -> Optional[dict]:
    """同家庭 + 同内容 sha256 且已有 OCR 文本的最新文件行(内容级去重的复用源)。

    同一文件在不同售后项目下 file_code 不同,按编号去重拦不住;按内容 hash 跨任务找兄弟行。
    优先 fresh(未脱敏原文),其次 reused(archive_detect 脱敏);同来源取 id 最大(最新)。
    """
    if not household_id or not sha256:
        return None
    async with async_session_maker() as session:
        stmt = (
            select(CustomerFile)
            .options(undefer(CustomerFile.ocr_text))
            .join(ProfileImportTask, CustomerFile.import_task_id == ProfileImportTask.id)
            .where(
                ProfileImportTask.household_id == household_id,
                CustomerFile.content_sha256 == sha256,
                CustomerFile.ocr_text.isnot(None),
                CustomerFile.ocr_text != "",
            )
        )
        if exclude_id is not None:
            stmt = stmt.where(CustomerFile.id != exclude_id)
        row = (await session.execute(
            stmt.order_by((CustomerFile.ocr_source == "fresh").desc(),
                          CustomerFile.id.desc())
                .limit(1)
        )).scalar_one_or_none()
        if not row:
            return None
        return {
            "id": row.id,
            "file_code": row.file_code,
            "ocr_text": row.ocr_text,
            "ocr_source": row.ocr_source,
            "mime_type": row.mime_type,
            "page_count": row.page_count,
            "char_count": row.char_count,
            "doc_type": row.doc_type,
            "classify_by": row.classify_by,
            "classify_score": row.classify_score,
        }


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
