"""客户资料结构化生成 CRUD。

基于 archive_detect_files.ocr_text 抽取事实后，直接写入 clients/family_members/assets/client_info。
写入策略：只补空字段，不覆盖已有非空人工数据。
"""
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select, update as sa_update
from sqlalchemy.orm import selectinload, undefer

from db.engine import async_session_maker
from db.models import (
    Client,
    FamilyMember,
    Asset,
    ClientInfo,
    ArchiveDetectFile,
    ArchiveDetectProgress,
    ClientProfileGenerationTask,
)


def _dt() -> datetime:
    return datetime.now()


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _parse_decimal(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _clean_str(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


async def list_source_files_for_client(client_id: int, source_file_ids: Optional[list[int]] = None) -> list[dict]:
    """查客户名下可用于生成档案的 OCR 文件（仅元数据，不含 ocr_text）。"""
    async with async_session_maker() as session:
        stmt = (
            select(ArchiveDetectFile, ArchiveDetectProgress)
            .join(ArchiveDetectProgress, ArchiveDetectFile.progress_id == ArchiveDetectProgress.id)
            .where(
                ArchiveDetectProgress.client_id == client_id,
                ArchiveDetectFile.status == "done",
                (ArchiveDetectFile.deleted.is_(False)) | (ArchiveDetectFile.deleted.is_(None)),
                ArchiveDetectFile.ocr_text.is_not(None),
            )
            .order_by(ArchiveDetectFile.created_at.desc())
        )
        if source_file_ids:
            stmt = stmt.where(ArchiveDetectFile.id.in_(source_file_ids))
        rows = (await session.execute(stmt)).all()
        out = []
        for f, p in rows:
            out.append({
                "id": f.id,
                "filename": f.filename,
                "doc_category": f.doc_category,
                "progress_name": p.progress_name,
                "progress_oid": p.progress_oid,
                "char_count": f.char_count,
                "has_ocr_text": True,
                "selectable": True,
            })
        return out


async def create_generation_task(client_id: int, source_files: list[dict]) -> dict:
    now = _dt()
    source_file_ids = [f["id"] for f in source_files]
    snapshot = [
        {
            "id": f["id"],
            "filename": f.get("filename"),
            "doc_category": f.get("doc_category"),
            "char_count": f.get("char_count"),
            "progress_name": f.get("progress_name"),
        }
        for f in source_files
    ]
    async with async_session_maker() as session:
        task = ClientProfileGenerationTask(
            client_id=client_id,
            status="running",
            source_file_ids=source_file_ids,
            source_files_snapshot=snapshot,
            source_file_count=len(source_file_ids),
            created_count={},
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return _task_to_dict(task)


def _task_to_dict(t: ClientProfileGenerationTask) -> dict:
    return {
        "task_id": t.id,
        "id": t.id,
        "client_id": t.client_id,
        "status": t.status,
        "source_file_ids": t.source_file_ids or [],
        "source_files_snapshot": t.source_files_snapshot or [],
        "source_file_count": t.source_file_count,
        "extracted_summary": t.extracted_summary or {},
        "created_count": t.created_count or {},
        "error": t.error,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
        "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M:%S") if t.updated_at else "",
    }


async def get_generation_task(task_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        t = (await session.execute(
            select(ClientProfileGenerationTask).where(ClientProfileGenerationTask.id == task_id)
        )).scalar_one_or_none()
        return _task_to_dict(t) if t else None


async def list_generation_tasks(client_id: int, limit: int = 20) -> list[dict]:
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(ClientProfileGenerationTask)
            .where(ClientProfileGenerationTask.client_id == client_id)
            .order_by(ClientProfileGenerationTask.created_at.desc())
            .limit(limit)
        )).scalars().all()
        return [_task_to_dict(t) for t in rows]


async def update_generation_task(
    task_id: int,
    *,
    status: str,
    extracted_summary: Optional[dict] = None,
    created_count: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    values = {"status": status, "updated_at": _dt()}
    if extracted_summary is not None:
        values["extracted_summary"] = extracted_summary
    if created_count is not None:
        values["created_count"] = created_count
    if error is not None:
        values["error"] = error
    async with async_session_maker() as session:
        await session.execute(
            sa_update(ClientProfileGenerationTask)
            .where(ClientProfileGenerationTask.id == task_id)
            .values(**values)
        )
        await session.commit()


async def apply_profile_facts(client_id: int, facts_list: list[dict]) -> dict:
    """把多个文件抽取出的 facts 写入结构化表。返回写入统计。"""
    counts = {"client_fields": 0, "family_members": 0, "assets": 0, "client_info": 0}
    async with async_session_maker() as session:
        client = (await session.execute(select(Client).where(Client.id == client_id))).scalar_one()

        for facts in facts_list:
            # clients: 只补空字段
            basic = facts.get("client_basic") or {}
            client_field_map = {
                "name_en": "name_en",
                "gender": "gender",
                "birth_date": "birth_date",
                "birth_place": "birth_place",
                "nationality": "nationality",
                "id_number": "id_number",
                "passport_no": "passport_no",
                "passport_expiry_date": "passport_expiry_date",
                "marital_status": "marital_status",
            }
            for src_key, attr in client_field_map.items():
                value = basic.get(src_key)
                if not value or getattr(client, attr, None):
                    continue
                if attr in ("birth_date", "passport_expiry_date"):
                    value = _parse_date(value)
                else:
                    value = _clean_str(value)
                if value:
                    setattr(client, attr, value)
                    counts["client_fields"] += 1
            client.updated_at = _dt()

            # family_members
            for fm in facts.get("family_members") or []:
                name = _clean_str(fm.get("name"))
                relation = _clean_str(fm.get("relation")) or "other"
                if not name:
                    continue
                existing = (await session.execute(
                    select(FamilyMember).where(
                        FamilyMember.client_id == client_id,
                        FamilyMember.relation == relation,
                        FamilyMember.name == name,
                    )
                )).scalar_one_or_none()
                target = existing or FamilyMember(client_id=client_id, relation=relation, name=name, created_at=_dt(), updated_at=_dt())
                if not existing:
                    session.add(target)
                    counts["family_members"] += 1
                for key in ["gender", "nationality", "id_number", "passport_no", "birth_cert_no", "birth_place"]:
                    value = _clean_str(fm.get(key))
                    if value and not getattr(target, key, None):
                        setattr(target, key, value)
                bdate = _parse_date(fm.get("birth_date"))
                if bdate and not target.birth_date:
                    target.birth_date = bdate
                target.updated_at = _dt()

            # assets
            for asset in facts.get("assets") or []:
                asset_type = _clean_str(asset.get("asset_type")) or "other"
                asset_name = _clean_str(asset.get("asset_name")) or _clean_str(asset.get("location_address")) or _clean_str(asset.get("bank_name")) or asset_type
                certificate_no = _clean_str(asset.get("certificate_no"))
                existing = None
                if certificate_no:
                    existing = (await session.execute(
                        select(Asset).where(Asset.client_id == client_id, Asset.certificate_no == certificate_no)
                    )).scalar_one_or_none()
                if not existing:
                    existing = (await session.execute(
                        select(Asset).where(Asset.client_id == client_id, Asset.asset_type == asset_type, Asset.asset_name == asset_name)
                    )).scalar_one_or_none()
                target = existing or Asset(client_id=client_id, asset_type=asset_type, asset_name=asset_name, created_at=_dt(), updated_at=_dt())
                if not existing:
                    session.add(target)
                    counts["assets"] += 1
                for key in ["owner_name", "currency", "bank_name", "account_no", "location_address", "certificate_no"]:
                    value = _clean_str(asset.get(key))
                    if value and not getattr(target, key, None):
                        setattr(target, key, value)
                amount = _parse_decimal(asset.get("value_amount"))
                if amount is not None and target.value_amount is None:
                    target.value_amount = amount
                target.updated_at = _dt()

            # extra_info + confidence_notes
            extras = list(facts.get("extra_info") or [])
            for note in facts.get("confidence_notes") or []:
                extras.append({"key": "AI置信说明", "value": note})
            for item in extras:
                key = _clean_str(item.get("key"))
                value = _clean_str(item.get("value"))
                if not key or not value:
                    continue
                existing = (await session.execute(
                    select(ClientInfo).where(ClientInfo.client_id == client_id, ClientInfo.info_key == key)
                )).scalar_one_or_none()
                if existing:
                    if not existing.info_value:
                        existing.info_value = value
                else:
                    session.add(ClientInfo(client_id=client_id, info_key=key, info_value=value, confirmed=False, created_at=_dt()))
                    counts["client_info"] += 1

        await session.commit()
    return counts
