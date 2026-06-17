"""
数据库 CRUD 操作封装
所有数据库读写操作集中在此模块。
"""

from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Optional
import re
from sqlalchemy import select, delete as sa_delete, func, or_, cast, String, case
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import async_session_maker
from db.models import Client, Document, ClientInfo, FamilyMember, Asset
from db import field_router
from db import family_crud
from db import assets_crud


async def create_document(task_id: str, filename: str, status: str = "ocr") -> Document:
    """上传时立即写入一条 documents 记录。"""
    async with async_session_maker() as session:
        doc = Document(
            task_id=task_id,
            filename=filename,
            status=status,
            created_at=datetime.now(),
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc


async def update_document_result(
    task_id: str,
    items: list,
    ocr_text: str,
    doc_type: Optional[str] = None,
    confidence_avg: Optional[float] = None,
    file_path: Optional[str] = None,
) -> Optional[Document]:
    """OCR+LLM 完成后更新文档记录。"""
    async with async_session_maker() as session:
        stmt = select(Document).where(Document.task_id == task_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()
        if not doc:
            return None

        doc.extracted_fields = items
        doc.ocr_text = ocr_text
        doc.doc_type = doc_type
        doc.confidence_avg = confidence_avg
        doc.file_path = file_path or f"output/{task_id}"
        doc.status = "done"
        doc.error_msg = None

        await session.commit()
        await session.refresh(doc)
        return doc


async def update_document_status(task_id: str, status: str, error_msg: Optional[str] = None):
    """更新文档处理状态（如 error）。"""
    async with async_session_maker() as session:
        stmt = select(Document).where(Document.task_id == task_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()
        if not doc:
            return

        doc.status = status
        if error_msg:
            doc.error_msg = error_msg
        await session.commit()


async def update_document_review(task_id: str, items: list) -> Optional[Document]:
    """人工复核保存时更新数据库。"""
    async with async_session_maker() as session:
        stmt = select(Document).where(Document.task_id == task_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()
        if not doc:
            return None

        doc.extracted_fields = items
        doc.reviewed = True

        # 重新计算平均置信度
        all_conf = []
        for item in items:
            fields = item.get("fields", {})
            for field_info in fields.values():
                if isinstance(field_info, dict):
                    all_conf.append(field_info.get("confidence", 0.5))
        if all_conf:
            doc.confidence_avg = round(sum(all_conf) / len(all_conf), 4)

        await session.commit()
        await session.refresh(doc)
        return doc


async def get_document(task_id: str) -> Optional[Document]:
    """按 task_id 查询文档记录。"""
    async with async_session_maker() as session:
        stmt = select(Document).where(Document.task_id == task_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def list_documents(limit: int = 50, offset: int = 0) -> list[dict]:
    """历史列表查询（替代扫描目录）。"""
    async with async_session_maker() as session:
        stmt = (
            select(Document)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        docs = result.scalars().all()

        history = []
        for doc in docs:
            # 从 items 中提取 doc_types
            doc_types = []
            field_count = 0
            items = doc.extracted_fields or []
            if isinstance(items, list):
                for item in items:
                    doc_types.append(item.get("doc_type", "未知"))
                    field_count += len(item.get("fields", {}))

            history.append({
                "task_id": doc.task_id,
                "filename": doc.filename,
                "doc_types": doc_types,
                "field_count": field_count,
                "reviewed": doc.reviewed or False,
                "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M:%S") if doc.created_at else "",
                "status": doc.status,
            })
        return history


async def delete_document(task_id: str) -> bool:
    """删除文档记录。"""
    async with async_session_maker() as session:
        stmt = select(Document).where(Document.task_id == task_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()
        if not doc:
            return False

        # 删除关联的 client_info 记录
        await session.execute(
            sa_delete(ClientInfo).where(ClientInfo.source_doc_id == doc.id)
        )
        # 删除文档记录
        await session.delete(doc)
        await session.commit()
        return True


async def find_or_create_client(name: str, id_number: Optional[str] = None) -> Client:
    """客户去重匹配：先按证件号查，再按姓名查，找不到则创建。"""
    async with async_session_maker() as session:
        # 优先按证件号匹配
        if id_number:
            stmt = select(Client).where(Client.id_number == id_number)
            result = await session.execute(stmt)
            client = result.scalar_one_or_none()
            if client:
                return client

        # 按姓名匹配（简单去重，后续可加强）
        if name:
            stmt = select(Client).where(Client.name == name)
            result = await session.execute(stmt)
            client = result.scalar_one_or_none()
            if client:
                return client

        # 创建新客户
        client = Client(
            name=name,
            id_number=id_number,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        session.add(client)
        await session.commit()
        await session.refresh(client)
        return client


async def search_documents(keyword: str, limit: int = 50) -> list[dict]:
    """
    全文模糊搜索文档。
    覆盖字段：filename / doc_type / ocr_text / extracted_fields(::text)
    仅返回 status='done' 的记录。
    排序：filename/doc_type 命中优先，再按时间倒序。
    返回 snippet：从 ocr_text 中截取关键词上下文 ±50 字符。
    """
    if not keyword or not keyword.strip():
        return []

    kw = keyword.strip()
    pattern = f"%{kw}%"

    async with async_session_maker() as session:
        # 4 字段 OR 模糊匹配
        # filename / doc_type 命中给较高排序权重
        rank_expr = case(
            (Document.filename.ilike(pattern), 3),
            (Document.doc_type.ilike(pattern), 2),
            else_=1,
        )

        stmt = (
            select(Document)
            .where(
                Document.status == "done",
                or_(
                    Document.filename.ilike(pattern),
                    Document.doc_type.ilike(pattern),
                    Document.ocr_text.ilike(pattern),
                    cast(Document.extracted_fields, String).ilike(pattern),
                ),
            )
            .order_by(rank_expr.desc(), Document.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        docs = result.scalars().all()

        results = []
        kw_lower = kw.lower()
        for doc in docs:
            # 生成 snippet：从 ocr_text 中找命中位置，截取上下文
            snippet = ""
            if doc.ocr_text:
                text = doc.ocr_text
                idx = text.lower().find(kw_lower)
                if idx >= 0:
                    start = max(0, idx - 50)
                    end = min(len(text), idx + len(kw) + 50)
                    prefix = "..." if start > 0 else ""
                    suffix = "..." if end < len(text) else ""
                    snippet = (prefix + text[start:end] + suffix).replace("\n", " ")
                else:
                    # ocr_text 没命中，可能是 filename/doc_type/extracted_fields 命中
                    snippet = text[:100].replace("\n", " ") + ("..." if len(text) > 100 else "")

            # 从 extracted_fields 拿 doc_types 和字段数
            doc_types = []
            field_count = 0
            items = doc.extracted_fields or []
            if isinstance(items, list):
                for item in items:
                    doc_types.append(item.get("doc_type", "未知"))
                    field_count += len(item.get("fields", {}))

            results.append({
                "task_id": doc.task_id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "doc_types": doc_types,
                "field_count": field_count,
                "confidence_avg": doc.confidence_avg,
                "reviewed": doc.reviewed or False,
                "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M:%S") if doc.created_at else "",
                "snippet": snippet,
            })
        return results


# ============== 字段归档：clients + client_info ==============
# 字段映射规则（拍平到 clients 主表）：
#   姓名 / Name -> clients.name
#   身份证号 / 证件号 / Passport No -> clients.id_number
#   性别 / Sex -> clients.gender
#   出生日期 / Date of Birth / 生日 -> clients.birth_date
#   国籍 / Nationality -> clients.nationality
# 其余字段全部进入 client_info（info_key=字段名，info_value=值）
# 特殊：有效期/到期日 -> client_info.valid_until

_NAME_KEYS = ("姓名", "name", "Name")
_ID_KEYS = ("身份证号", "证件号", "护照号", "passport", "id_number")
_GENDER_KEYS = ("性别", "sex", "gender")
_BIRTH_KEYS = ("出生日期", "出生", "生日", "date of birth", "dob")
_NATION_KEYS = ("国籍", "nationality")
_VALID_UNTIL_KEYS = ("有效期", "到期日", "expiry", "valid until", "有效期至", "失效日期")


def _extract_field_value(fields: dict, candidate_keys: tuple) -> Optional[str]:
    """从 fields 中按候选键名（不区分大小写、模糊匹配）找一个非空值。"""
    if not fields:
        return None
    for key in fields.keys():
        key_lower = str(key).lower()
        for c in candidate_keys:
            if c.lower() in key_lower:
                v = fields[key]
                val = v.get("value") if isinstance(v, dict) else v
                if val and str(val).strip():
                    return str(val).strip()
    return None


def _parse_date(s: Optional[str]):
    """尝试解析多种日期字符串格式，返回 date 或 None。"""
    if not s:
        return None
    s = str(s).strip()
    # 抽取数字
    digits = re.findall(r"\d+", s)
    if len(digits) >= 3:
        try:
            y, m, d = int(digits[0]), int(digits[1]), int(digits[2])
            if y < 100:  # 两位年份不处理
                return None
            from datetime import date as _date
            return _date(y, m, d)
        except (ValueError, TypeError):
            return None
    return None


async def upsert_client_info(client_id: int, key_values: dict, source_doc_id: Optional[int] = None) -> int:
    """
    把 {info_key: info_value} 字典 upsert 到 client_info。
    - 已存在 (client_id, info_key) -> 更新 info_value（仅当新值非空且不同时）
    - 不存在 -> 插入
    - 自动识别"有效期"类字段并填 valid_until
    返回：成功更新/插入的条目数。
    """
    if not key_values:
        return 0

    written = 0
    async with async_session_maker() as session:
        # 校验 client 存在
        res = await session.execute(select(Client).where(Client.id == client_id))
        client = res.scalar_one_or_none()
        if not client:
            return 0

        for fkey, raw_value in key_values.items():
            if raw_value is None:
                continue
            value_str = str(raw_value).strip()
            if not value_str:
                continue

            # 识别有效期字段
            valid_until = None
            fkey_lower = str(fkey).lower()
            for k in _VALID_UNTIL_KEYS:
                if k.lower() in fkey_lower:
                    valid_until = _parse_date(value_str)
                    break

            res = await session.execute(
                select(ClientInfo).where(
                    ClientInfo.client_id == client_id,
                    ClientInfo.info_key == fkey,
                )
            )
            existing = res.scalar_one_or_none()
            if existing:
                if existing.info_value == value_str:
                    continue  # 值未变，跳过
                existing.info_value = value_str
                if valid_until:
                    existing.valid_until = valid_until
                if source_doc_id is not None:
                    existing.source_doc_id = source_doc_id
                existing.confirmed = True
            else:
                session.add(ClientInfo(
                    client_id=client_id,
                    info_key=fkey,
                    info_value=value_str,
                    source_doc_id=source_doc_id,
                    valid_until=valid_until,
                    confirmed=True,
                    created_at=datetime.now(),
                ))
            written += 1

        client.updated_at = datetime.now()
        await session.commit()
    return written


async def archive_to_specific_client(task_id: str, client_id: int, items: list) -> int:
    """A1 批量队列模式：跳过复核直接归档到指定客户。
    - 设置 documents.client_id = client_id 并标记 reviewed
    - 把 items 中所有字段拍平 upsert 到 client_info（复用 upsert_client_info）
    返回：写入 client_info 的条目数（0 表示客户不存在或无字段）。
    """
    if not items:
        return 0

    async with async_session_maker() as session:
        # 校验客户存在
        res = await session.execute(select(Client).where(Client.id == client_id))
        client = res.scalar_one_or_none()
        if not client:
            return 0

        # 关联 documents.client_id + reviewed
        res = await session.execute(select(Document).where(Document.task_id == task_id))
        doc = res.scalar_one_or_none()
        doc_id = None
        if doc:
            doc.client_id = client_id
            doc.reviewed = True
            doc_id = doc.id

        await session.commit()

    # 拍平字段
    flat = {}
    for item in items:
        for fkey, fval in (item.get("fields") or {}).items():
            value = fval.get("value") if isinstance(fval, dict) else fval
            if value is None or str(value).strip() == "":
                continue
            flat[fkey] = str(value).strip()

    if not flat:
        return 0
    return await upsert_client_info(client_id, flat, source_doc_id=doc_id)


async def archive_to_client_info(task_id: str, items: list) -> Optional[int]:
    """
    复核保存时把 items 拍平归档到 clients + client_info。
    - 从 items 中识别姓名/证件号 -> find_or_create_client
    - 关联 documents.client_id
    - 把所有 fields 落到 client_info（按 client_id+info_key upsert）
    返回：client_id（成功）/ None（无可识别姓名）
    """
    if not items:
        return None

    # 取第一张证件做客户主体识别（多证件场景：以第一张为主）
    primary = items[0]
    primary_fields = primary.get("fields", {}) if isinstance(primary, dict) else {}

    name = _extract_field_value(primary_fields, _NAME_KEYS)
    id_number = _extract_field_value(primary_fields, _ID_KEYS)
    gender = _extract_field_value(primary_fields, _GENDER_KEYS)
    birth_str = _extract_field_value(primary_fields, _BIRTH_KEYS)
    nation = _extract_field_value(primary_fields, _NATION_KEYS)

    if not name and not id_number:
        # 无法识别客户主体，跳过归档
        return None

    async with async_session_maker() as session:
        # 1) find_or_create client
        client = None
        if id_number:
            res = await session.execute(select(Client).where(Client.id_number == id_number))
            client = res.scalar_one_or_none()
        if not client and name:
            res = await session.execute(select(Client).where(Client.name == name))
            client = res.scalar_one_or_none()
        if not client:
            client = Client(
                name=name or "未知",
                id_number=id_number,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            session.add(client)
            await session.flush()  # 拿 client.id

        # 更新主表字段（仅在原值为空时填充，避免覆盖人工修改）
        if name and not client.name:
            client.name = name
        if id_number and not client.id_number:
            client.id_number = id_number
        if gender and not client.gender:
            client.gender = gender
        if nation and not client.nationality:
            client.nationality = nation
        birth_date = _parse_date(birth_str)
        if birth_date and not client.birth_date:
            client.birth_date = birth_date
        client.updated_at = datetime.now()

        # 2) 关联 documents.client_id
        res = await session.execute(select(Document).where(Document.task_id == task_id))
        doc = res.scalar_one_or_none()
        if doc:
            doc.client_id = client.id
        doc_id = doc.id if doc else None

        # 3) 把所有 items.fields 拍平 upsert 到 client_info
        for item in items:
            fields = item.get("fields", {}) if isinstance(item, dict) else {}
            for fkey, fval in fields.items():
                value = fval.get("value") if isinstance(fval, dict) else fval
                if value is None or str(value).strip() == "":
                    continue
                value_str = str(value).strip()

                # 特殊字段：有效期 -> valid_until
                valid_until = None
                fkey_lower = str(fkey).lower()
                for k in _VALID_UNTIL_KEYS:
                    if k.lower() in fkey_lower:
                        valid_until = _parse_date(value_str)
                        break

                # 已存在则更新，否则插入
                res = await session.execute(
                    select(ClientInfo).where(
                        ClientInfo.client_id == client.id,
                        ClientInfo.info_key == fkey,
                    )
                )
                existing = res.scalar_one_or_none()
                if existing:
                    existing.info_value = value_str
                    if valid_until:
                        existing.valid_until = valid_until
                    existing.source_doc_id = doc_id
                    existing.confirmed = True
                else:
                    info = ClientInfo(
                        client_id=client.id,
                        info_key=fkey,
                        info_value=value_str,
                        source_doc_id=doc_id,
                        valid_until=valid_until,
                        confirmed=True,
                        created_at=datetime.now(),
                    )
                    session.add(info)

        await session.commit()
        return client.id


# ============== 客户档案查询 ==============

async def list_clients(
    keyword: Optional[str] = None,
    visa_type: Optional[str] = None,
    expiring_soon_days: Optional[int] = None,
    sort_by: str = "updated_at",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    客户列表（含每个客户的文档数量、家属数、资产数统计）。
    keyword 可选：按 name / id_number / passport_no / client_code 模糊匹配
    visa_type 可选：按业务类型精确筛选
    expiring_soon_days 可选：仅返回护照在 N 天内到期的
    sort_by ∈ {"updated_at", "passport_expiry"}
    """
    async with async_session_maker() as session:
        # 文档/家属/资产数量子查询
        doc_count_subq = (
            select(Document.client_id, func.count(Document.id).label("doc_count"))
            .where(Document.client_id.isnot(None))
            .group_by(Document.client_id)
            .subquery()
        )
        family_count_subq = (
            select(FamilyMember.client_id, func.count(FamilyMember.id).label("family_count"))
            .group_by(FamilyMember.client_id)
            .subquery()
        )
        asset_count_subq = (
            select(Asset.client_id, func.count(Asset.id).label("asset_count"))
            .group_by(Asset.client_id)
            .subquery()
        )

        stmt = (
            select(
                Client,
                doc_count_subq.c.doc_count,
                family_count_subq.c.family_count,
                asset_count_subq.c.asset_count,
            )
            .outerjoin(doc_count_subq, Client.id == doc_count_subq.c.client_id)
            .outerjoin(family_count_subq, Client.id == family_count_subq.c.client_id)
            .outerjoin(asset_count_subq, Client.id == asset_count_subq.c.client_id)
        )

        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            stmt = stmt.where(or_(
                Client.name.ilike(kw),
                Client.id_number.ilike(kw),
                Client.passport_no.ilike(kw),
                Client.client_code.ilike(kw),
            ))

        if visa_type and visa_type.strip():
            stmt = stmt.where(Client.visa_type == visa_type.strip())

        if expiring_soon_days is not None and expiring_soon_days >= 0:
            from datetime import date, timedelta
            today = date.today()
            cutoff = today + timedelta(days=expiring_soon_days)
            stmt = stmt.where(
                Client.passport_expiry_date.isnot(None),
                Client.passport_expiry_date <= cutoff,
                Client.passport_expiry_date >= today,
            )

        # 排序
        if sort_by == "passport_expiry":
            stmt = stmt.order_by(Client.passport_expiry_date.asc().nullslast(), Client.id.desc())
        else:
            stmt = stmt.order_by(Client.updated_at.desc().nullslast(), Client.id.desc())

        stmt = stmt.limit(limit).offset(offset)
        result = await session.execute(stmt)
        rows = result.all()

        clients_list = []
        for client, doc_count, family_count, asset_count in rows:
            clients_list.append({
                "id": client.id,
                "client_code": client.client_code,
                "name": client.name,
                "name_en": client.name_en,
                "id_number": client.id_number,
                "passport_no": client.passport_no,
                "passport_expiry_date": client.passport_expiry_date.isoformat() if client.passport_expiry_date else None,
                "gender": client.gender,
                "birth_date": client.birth_date.isoformat() if client.birth_date else None,
                "nationality": client.nationality,
                "visa_type": client.visa_type,
                "consultant": client.consultant,
                "doc_count": doc_count or 0,
                "family_count": family_count or 0,
                "asset_count": asset_count or 0,
                "created_at": client.created_at.strftime("%Y-%m-%d %H:%M:%S") if client.created_at else "",
                "updated_at": client.updated_at.strftime("%Y-%m-%d %H:%M:%S") if client.updated_at else "",
            })
        return clients_list


def _client_to_dict(client: Client) -> dict:
    """完整 Client → dict 序列化（用于 get_client_detail / 编辑 API）。"""
    if not client:
        return None
    return {
        "id": client.id,
        "client_code": client.client_code,
        # 身份
        "name": client.name,
        "name_en": client.name_en,
        "former_name": client.former_name,
        "gender": client.gender,
        "birth_date": client.birth_date.isoformat() if client.birth_date else None,
        "birth_place": client.birth_place,
        "ethnicity": client.ethnicity,
        "nationality": client.nationality,
        "id_number": client.id_number,
        "hukou_address": client.hukou_address,
        "marital_status": client.marital_status,
        # 联系方式
        "phone": client.phone,
        "email": client.email,
        "current_address": client.current_address,
        # 护照
        "passport_no": client.passport_no,
        "passport_issue_date": client.passport_issue_date.isoformat() if client.passport_issue_date else None,
        "passport_expiry_date": client.passport_expiry_date.isoformat() if client.passport_expiry_date else None,
        "passport_issuing_authority": client.passport_issuing_authority,
        # 教育
        "school_name": client.school_name,
        "school_name_en": client.school_name_en,
        "major": client.major,
        "degree": client.degree,
        "graduation_date": client.graduation_date.isoformat() if client.graduation_date else None,
        "graduation_cert_no": client.graduation_cert_no,
        "degree_cert_no": client.degree_cert_no,
        # 工作
        "company_name": client.company_name,
        "position": client.position,
        "employment_start_date": client.employment_start_date.isoformat() if client.employment_start_date else None,
        "monthly_salary": float(client.monthly_salary) if client.monthly_salary is not None else None,
        # 婚姻
        "marriage_date": client.marriage_date.isoformat() if client.marriage_date else None,
        "marriage_authority": client.marriage_authority,
        "marriage_cert_no": client.marriage_cert_no,
        # 业务+审计
        "visa_type": client.visa_type,
        "consultant": client.consultant,
        "notes": client.notes,
        "created_at": client.created_at.strftime("%Y-%m-%d %H:%M:%S") if client.created_at else "",
        "updated_at": client.updated_at.strftime("%Y-%m-%d %H:%M:%S") if client.updated_at else "",
    }


async def get_client_detail(client_id: int) -> Optional[dict]:
    """客户详情：基本信息 + family + assets + client_info + 名下文档。"""
    async with async_session_maker() as session:
        res = await session.execute(select(Client).where(Client.id == client_id))
        client = res.scalar_one_or_none()
        if not client:
            return None

        # client_info 列表
        res = await session.execute(
            select(ClientInfo)
            .where(ClientInfo.client_id == client_id)
            .order_by(ClientInfo.info_key)
        )
        infos = res.scalars().all()

        info_list = [{
            "id": info.id,
            "info_key": info.info_key,
            "info_value": info.info_value,
            "valid_until": info.valid_until.strftime("%Y-%m-%d") if info.valid_until else None,
            "valid_from": info.valid_from.strftime("%Y-%m-%d") if info.valid_from else None,
            "confirmed": info.confirmed or False,
            "source_doc_id": info.source_doc_id,
            "created_at": info.created_at.strftime("%Y-%m-%d %H:%M:%S") if info.created_at else "",
        } for info in infos]

        # 名下文档列表
        res = await session.execute(
            select(Document)
            .where(Document.client_id == client_id)
            .order_by(Document.created_at.desc())
        )
        docs = res.scalars().all()

        doc_list = []
        for doc in docs:
            doc_types = []
            field_count = 0
            items = doc.extracted_fields or []
            if isinstance(items, list):
                for item in items:
                    doc_types.append(item.get("doc_type", "未知"))
                    field_count += len(item.get("fields", {}))
            doc_list.append({
                "task_id": doc.task_id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "doc_types": doc_types,
                "field_count": field_count,
                "reviewed": doc.reviewed or False,
                "confidence_avg": doc.confidence_avg,
                "status": doc.status,
                "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M:%S") if doc.created_at else "",
            })

    # family / assets 走各自 crud（独立 session，简化）
    family_list = await family_crud.list_by_client(client_id)
    asset_list = await assets_crud.list_by_client(client_id)

    detail = _client_to_dict(client)
    detail["infos"] = info_list
    detail["documents"] = doc_list
    detail["family_members"] = family_list
    detail["assets"] = asset_list
    return detail


# ============== 客户智能匹配 ==============

async def match_clients(
    id_number: Optional[str] = None,
    passport_no: Optional[str] = None,
    name: Optional[str] = None,
    birth_date: Optional[str] = None,
) -> list[dict]:
    """根据 OCR 结果智能匹配现有客户。返回按 score 倒序的候选列表。

    匹配规则（score）：
      - 100: 身份证号精确命中
      - 95:  护照号精确命中
      - 80:  姓名 + 出生日期 同时命中
      - 50:  仅姓名命中
    """
    if not (id_number or passport_no or name):
        return []

    bd = _parse_date(birth_date) if birth_date else None
    candidates: dict[int, dict] = {}    # client.id → {client_dict, score, reason}

    async with async_session_maker() as session:
        # 1) 身份证号
        if id_number:
            res = await session.execute(select(Client).where(Client.id_number == id_number))
            for c in res.scalars().all():
                candidates[c.id] = {"client": c, "score": 100, "reason": "身份证号匹配"}

        # 2) 护照号
        if passport_no:
            res = await session.execute(select(Client).where(Client.passport_no == passport_no))
            for c in res.scalars().all():
                if c.id not in candidates or candidates[c.id]["score"] < 95:
                    candidates[c.id] = {"client": c, "score": 95, "reason": "护照号匹配"}

        # 3) 姓名 + 出生日期
        if name and bd:
            res = await session.execute(
                select(Client).where(Client.name == name, Client.birth_date == bd)
            )
            for c in res.scalars().all():
                if c.id not in candidates or candidates[c.id]["score"] < 80:
                    candidates[c.id] = {"client": c, "score": 80, "reason": "姓名+出生日期匹配"}

        # 4) 仅姓名
        if name:
            res = await session.execute(select(Client).where(Client.name == name))
            for c in res.scalars().all():
                if c.id not in candidates:
                    candidates[c.id] = {"client": c, "score": 50, "reason": "仅姓名匹配"}

    # 序列化 + 排序
    out = []
    for cid, info in candidates.items():
        c = info["client"]
        out.append({
            "client_id": c.id,
            "client_code": c.client_code,
            "name": c.name,
            "id_number": c.id_number,
            "passport_no": c.passport_no,
            "birth_date": c.birth_date.isoformat() if c.birth_date else None,
            "score": info["score"],
            "reason": info["reason"],
        })
    out.sort(key=lambda x: -x["score"])
    return out


# ============== 客户主表 create / update ==============

# clients 主表强 schema 列白名单（防止注入随意键）
_CLIENT_COLUMNS = {
    # 身份
    "client_code", "name", "name_en", "former_name", "gender", "birth_date",
    "birth_place", "ethnicity", "nationality", "id_number", "hukou_address", "marital_status",
    # 联系
    "phone", "email", "current_address",
    # 护照
    "passport_no", "passport_issue_date", "passport_expiry_date", "passport_issuing_authority",
    # 教育
    "school_name", "school_name_en", "major", "degree",
    "graduation_date", "graduation_cert_no", "degree_cert_no",
    # 工作
    "company_name", "position", "employment_start_date", "monthly_salary",
    # 婚姻
    "marriage_date", "marriage_authority", "marriage_cert_no",
    # 业务
    "visa_type", "consultant", "notes",
}

_CLIENT_DATE_COLUMNS = {
    "birth_date", "passport_issue_date", "passport_expiry_date",
    "graduation_date", "employment_start_date", "marriage_date",
}

_CLIENT_DECIMAL_COLUMNS = {"monthly_salary"}


def _coerce_client_value(col: str, value):
    if value is None or value == "":
        return None
    if col in _CLIENT_DATE_COLUMNS and not isinstance(value, date):
        return _parse_date(value)
    if col in _CLIENT_DECIMAL_COLUMNS and not isinstance(value, (int, float, Decimal)):
        s = str(value).strip()
        cleaned = "".join(c for c in s if c.isdigit() or c == "." or c == "-")
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None
    return value


def _filter_client_payload(payload: dict) -> dict:
    out = {}
    for k, v in (payload or {}).items():
        if k not in _CLIENT_COLUMNS:
            continue
        coerced = _coerce_client_value(k, v)
        if coerced is not None:
            out[k] = coerced
    return out


async def create_client(payload: dict) -> dict:
    """新建客户。name 必填。返回完整 dict。"""
    data = _filter_client_payload(payload)
    if not data.get("name"):
        raise ValueError("name 不能为空")
    async with async_session_maker() as session:
        c = Client(**data)
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return _client_to_dict(c)


async def update_client(client_id: int, payload: dict) -> Optional[dict]:
    """部分更新客户主表。仅更新 payload 中提供的字段。"""
    data = _filter_client_payload(payload)
    async with async_session_maker() as session:
        res = await session.execute(select(Client).where(Client.id == client_id))
        c = res.scalar_one_or_none()
        if not c:
            return None
        for k, v in data.items():
            setattr(c, k, v)
        c.updated_at = datetime.now()
        await session.commit()
        await session.refresh(c)
        return _client_to_dict(c)


# ============== 新归档函数：archive_document ==============

async def archive_document(
    task_id: str,
    client_id: int,
    entity: str,                    # 'clients' | 'family' | 'assets'
    target_id: Optional[int] = None,
    sub_meta: Optional[dict] = None,    # {'relation': '配偶'} 或 {'asset_type': '房产'}
    items: Optional[list] = None,
    write_unmapped_to_kv: bool = True,
) -> dict:
    """新版归档：按 entity 把 OCR fields 路由到 clients/family/assets 表。

    - entity='clients': 把字段更新到 clients 主表
    - entity='family':  按 sub_meta['relation'] upsert family_members 行；
                        target_id 给定时直接更新该行
    - entity='assets':  按 sub_meta['asset_type'] 新建 assets 行；
                        target_id 给定时更新该行
    - 未映射字段写 client_info KV（write_unmapped_to_kv=True 时）
    - 写完更新 documents.client_id + reviewed=True

    返回：{
      "client_id": ...,
      "entity": ...,
      "target_id": ...,        # family/assets 行的 id（新建/更新）
      "mapped_count": ...,     # 命中 schema 的字段数
      "unmapped_count": ...,   # 进 KV 的字段数
    }
    """
    if items is None:
        items = []
    sub_meta = sub_meta or {}

    # 把 items 中所有 fields 拍平成一个 dict（多 item 合并；后者覆盖前者）
    flat_fields: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for k, v in (item.get("fields") or {}).items():
            flat_fields[k] = v

    # ==== 路由 ====
    mapped, unmapped = field_router.route_fields(entity, flat_fields)

    # ==== 写主表/子表 ====
    target_id_out = target_id

    if entity == "clients":
        if mapped:
            await update_client(client_id, mapped)

    elif entity == "family":
        relation = sub_meta.get("relation") or "配偶"
        if target_id:
            await family_crud.update(target_id, mapped)
        else:
            row = await family_crud.upsert_by_relation(client_id, relation, mapped)
            target_id_out = row["id"]

    elif entity == "assets":
        asset_type = sub_meta.get("asset_type") or "其他"
        payload = {**mapped, "asset_type": asset_type}
        if target_id:
            await assets_crud.update(target_id, payload)
        else:
            row = await assets_crud.create(client_id, payload)
            target_id_out = row["id"]

    else:
        raise ValueError(f"未知 entity: {entity}")

    # ==== 关联 documents.client_id 并标 reviewed ====
    doc_id = None
    async with async_session_maker() as session:
        res = await session.execute(select(Document).where(Document.task_id == task_id))
        doc = res.scalar_one_or_none()
        if doc:
            doc.client_id = client_id
            doc.reviewed = True
            doc_id = doc.id
            await session.commit()

    # ==== 未映射字段写 KV 兜底 ====
    if write_unmapped_to_kv and unmapped:
        kv_payload = {}
        for k, v in unmapped.items():
            kv_payload[k] = v
        # 走现有 upsert_client_info（带 source_doc_id）
        await upsert_client_info(client_id, kv_payload, source_doc_id=doc_id)

    return {
        "client_id": client_id,
        "entity": entity,
        "target_id": target_id_out,
        "mapped_count": len(mapped),
        "unmapped_count": len(unmapped),
    }

