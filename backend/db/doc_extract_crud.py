"""doc_extract CRUD: 提取结果(doc_extract_results) + 归因写库。

提取规则已迁至代码常量 backend/extract_rules.py(不再走 DB);提取结果一次运行一行,全程留痕可回溯。

归因写库: find_person_match 在本客户范围内按 证件号->姓名 匹配 client/member,
apply_extracted_fields 按规则 target.column 只补空写入(不覆盖已有非空值)。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Date, select, func

from db.ai_api_call_crud import _clean_text
from db.client_profile_crud import _parse_date, _clean_str
from db.engine import async_session_maker
from db.models import Client, DocExtractResult, FamilyMember


# ==================== person 可写列(规则 target.column 的取值范围) ====================
# (column, 中文说明, 客户表有, 成员表有);归因决定写哪行,两表身份列基本平行。
PERSON_TARGET_COLUMNS = [
    ("name", "姓名", True, True),
    ("name_en", "拼音/英文名", True, True),
    ("gender", "性别", True, True),
    ("birth_date", "出生日期", True, True),
    ("birth_place", "出生地", True, True),
    ("nationality", "国籍", True, True),
    ("ethnicity", "民族", True, False),
    ("id_number", "身份证号", True, True),
    ("hukou_address", "户籍地址", True, False),
    ("marital_status", "婚姻状况", True, False),
    ("phone", "手机", True, True),
    ("email", "邮箱", True, True),
    ("current_address", "现住址", True, True),
    ("passport_no", "护照号", True, True),
    ("passport_issue_date", "护照签发日期", True, False),
    ("passport_expiry_date", "护照到期日期", True, False),
    ("birth_cert_no", "出生医学证编号", False, True),
    ("birth_hospital", "出生医院", False, True),
    ("school_name", "学校名", True, True),
    ("major", "专业", True, True),
    ("degree", "学位", True, True),
    ("graduation_date", "毕业日期", True, True),
    ("graduation_cert_no", "毕业证编号", True, True),
    ("degree_cert_no", "学位证编号", True, True),
]

CLIENT_COLUMNS = {c for c, _, on_client, _ in PERSON_TARGET_COLUMNS if on_client}
MEMBER_COLUMNS = {c for c, _, _, on_member in PERSON_TARGET_COLUMNS if on_member}

_CLIENT_DATE_COLUMNS = {c.name for c in Client.__table__.columns if isinstance(c.type, Date)}
_MEMBER_DATE_COLUMNS = {c.name for c in FamilyMember.__table__.columns if isinstance(c.type, Date)}


def target_columns_text() -> str:
    """给规则起草 prompt 用的可写列清单文本。"""
    lines = []
    for col, label, on_client, on_member in PERSON_TARGET_COLUMNS:
        scope = "客户/成员" if on_client and on_member else ("仅客户" if on_client else "仅成员")
        lines.append(f"{col} = {label}({scope})")
    return "\n".join(lines)


# ==================== 脱敏/证件号判定(service 与测试共用) ====================

MASKED_TOKENS = ("[身份证]", "[手机号]", "[银行卡]", "[金额]", "[座机]")


def is_masked(v) -> bool:
    """值是否被脱敏(含星号或任一占位词)。脱敏值不写库、不参与归因。"""
    if not v or not isinstance(v, str):
        return False
    return "*" in v or any(tok in v for tok in MASKED_TOKENS)


def valid_id_number(v) -> bool:
    """身份证号基本格式:17 位数字 + 数字/X。"""
    if not v or not isinstance(v, str):
        return False
    s = v.strip()
    return len(s) == 18 and s[:17].isdigit() and (s[17].isdigit() or s[17] in "Xx")


# ==================== 序列化 ====================

def _to_result_dict(r: DocExtractResult) -> dict:
    return {
        "id": r.id,
        "customer_file_id": r.customer_file_id,
        "import_task_id": r.import_task_id,
        "file_id": r.file_id,
        "client_id": r.client_id,
        "doc_type": r.doc_type,
        "rule_id": r.rule_id,
        "rule_version": r.rule_version,
        "status": r.status,
        "skip_reason": r.skip_reason,
        "extracted": r.extracted,
        "mapped": r.mapped,
        "write_stats": r.write_stats,
        "error_msg": r.error_msg,
        "elapsed_ms": r.elapsed_ms,
        "review_status": r.review_status,
        "corrected": r.corrected,
        "reviewed_by": r.reviewed_by,
        "reviewed_at": r.reviewed_at.strftime("%Y-%m-%d %H:%M:%S") if r.reviewed_at else None,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


# ==================== 结果 CRUD ====================

async def insert_result(*, customer_file_id: int, import_task_id: int,
                        file_id: Optional[str], client_id: Optional[int],
                        doc_type: str, rule_id: Optional[int], rule_version: Optional[int],
                        status: str, skip_reason: Optional[str] = None,
                        extracted=None, mapped=None, write_stats=None,
                        error_msg: Optional[str] = None, elapsed_ms: Optional[int] = None) -> dict:
    async with async_session_maker() as session:
        row = DocExtractResult(
            customer_file_id=customer_file_id,
            import_task_id=import_task_id,
            file_id=file_id,
            client_id=client_id,
            doc_type=doc_type,
            rule_id=rule_id,
            rule_version=rule_version,
            status=status,
            skip_reason=skip_reason,
            extracted=extracted,
            mapped=mapped,
            write_stats=write_stats,
            error_msg=_clean_text(error_msg, 2000),
            elapsed_ms=elapsed_ms,
            created_at=datetime.now(),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_result_dict(row)


async def list_results(*, import_task_id: Optional[int] = None,
                       customer_file_id: Optional[int] = None,
                       file_id: Optional[str] = None,
                       doc_type: Optional[str] = None,
                       status: Optional[str] = None,
                       limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    async with async_session_maker() as session:
        stmt = select(DocExtractResult)
        cnt = select(func.count(DocExtractResult.id))
        for col, val in ((DocExtractResult.import_task_id, import_task_id),
                         (DocExtractResult.customer_file_id, customer_file_id),
                         (DocExtractResult.doc_type, doc_type),
                         (DocExtractResult.status, status)):
            if val is not None:
                stmt = stmt.where(col == val)
                cnt = cnt.where(col == val)
        if file_id:
            stmt = stmt.where(DocExtractResult.file_id.ilike(f"%{file_id}%"))
            cnt = cnt.where(DocExtractResult.file_id.ilike(f"%{file_id}%"))
        total = await session.scalar(cnt) or 0
        rows = (await session.execute(
            stmt.order_by(DocExtractResult.created_at.desc(), DocExtractResult.id.desc())
                .limit(limit).offset(offset)
        )).scalars().all()
        return [_to_result_dict(r) for r in rows], total


async def get_result(row_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        row = await session.get(DocExtractResult, row_id)
        return _to_result_dict(row) if row else None


# ==================== 归因 + 只补空写库 ====================

async def find_person_match(client_id: int, id_number: Optional[str] = None,
                            name: Optional[str] = None) -> dict:
    """在本客户范围内归因:证件号 -> 姓名,客户本人 -> 家庭成员。

    clients 查询限定 id == client_id(本流程只建"这个客户"的档案,不写别的客户行)。
    返回 {entity: 'client'|'member'|None, row_id, matched_by: 'id_number'|'name'|None}
    """
    async with async_session_maker() as session:
        if id_number:
            row = await session.scalar(
                select(Client).where(Client.id == client_id, Client.id_number == id_number))
            if row:
                return {"entity": "client", "row_id": row.id, "matched_by": "id_number"}
            row = await session.scalar(
                select(FamilyMember).where(FamilyMember.client_id == client_id,
                                           FamilyMember.id_number == id_number)
                .order_by(FamilyMember.id))
            if row:
                return {"entity": "member", "row_id": row.id, "matched_by": "id_number"}
        if name:
            row = await session.scalar(
                select(Client).where(Client.id == client_id, Client.name == name))
            if row:
                return {"entity": "client", "row_id": row.id, "matched_by": "name"}
            row = await session.scalar(
                select(FamilyMember).where(FamilyMember.client_id == client_id,
                                           FamilyMember.name == name)
                .order_by(FamilyMember.id))
            if row:
                return {"entity": "member", "row_id": row.id, "matched_by": "name"}
    return {"entity": None, "row_id": None, "matched_by": None}


async def apply_extracted_fields(client_id: int, match: dict, field_items: list) -> dict:
    """把提取字段按 target.column 只补空写入归因出的人。

    field_items: [{key, label, value, column}];value 已由调用方去空。
    match.entity 为 None 时:有 name 则新建 FamilyMember(relation='待确认')再写。
    逐字段 action: written / skipped_filled / skipped_masked / skipped_invalid /
                  skipped_conflict / unmapped。
    返回 {mapped, write_stats}
    """
    mapped: list[dict] = []
    stats = {"matched_by": match.get("matched_by"), "client_fields": 0,
             "member_fields": 0, "member_created": 0}

    async with async_session_maker() as session:
        entity = match.get("entity")
        obj = None
        if entity == "client":
            obj = await session.get(Client, match["row_id"])
        elif entity == "member":
            obj = await session.get(FamilyMember, match["row_id"])
        else:
            # 无命中:有姓名则新建成员
            name = next((it["value"] for it in field_items
                         if it.get("column") == "name" and it.get("value")), None)
            if name:
                obj = FamilyMember(client_id=client_id, relation="待确认", name=name,
                                   created_at=datetime.now(), updated_at=datetime.now())
                session.add(obj)
                await session.flush()
                entity = "member"
                stats["member_created"] = 1
                mapped.append({"key": "name", "column": "name", "entity": "member",
                               "entity_id": obj.id, "action": "written"})
                stats["member_fields"] += 1
        if obj is None:
            return {"mapped": mapped, "write_stats": stats}

        allowed = CLIENT_COLUMNS if entity == "client" else MEMBER_COLUMNS
        date_cols = _CLIENT_DATE_COLUMNS if entity == "client" else _MEMBER_DATE_COLUMNS
        stats["entity"] = entity
        stats["entity_id"] = obj.id

        for it in field_items:
            key, value, column = it.get("key"), it.get("value"), it.get("column")
            entry = {"key": key, "column": column, "entity": entity, "entity_id": obj.id}
            if not value:
                continue
            if is_masked(value):
                mapped.append({**entry, "action": "skipped_masked"})
                continue
            if not column or column not in allowed:
                mapped.append({**entry, "action": "unmapped"})
                continue
            if column == "name" and stats["member_created"]:
                continue  # 新建成员时已写过 name
            if column == "id_number" and not valid_id_number(value):
                mapped.append({**entry, "action": "skipped_invalid"})
                continue
            # clients.id_number 有 unique 约束:别的客户已占用则跳过
            if entity == "client" and column == "id_number":
                conflict = await session.scalar(
                    select(Client.id).where(Client.id_number == value, Client.id != obj.id))
                if conflict:
                    mapped.append({**entry, "action": "skipped_conflict"})
                    continue
            current = getattr(obj, column, None)
            if current is not None and str(current).strip():
                mapped.append({**entry, "action": "skipped_filled"})
                continue
            if column in date_cols:
                parsed = _parse_date(value)
                if parsed is None:
                    mapped.append({**entry, "action": "skipped_invalid"})
                    continue
                setattr(obj, column, parsed)
            else:
                setattr(obj, column, _clean_str(value))
            mapped.append({**entry, "action": "written"})
            stats["client_fields" if entity == "client" else "member_fields"] += 1

        obj.updated_at = datetime.now()
        await session.commit()
    return {"mapped": mapped, "write_stats": stats}


async def update_result_review(result_id: int, *, status: str, corrected=None,
                               reviewed_by: Optional[str] = None) -> None:
    """复核结果:更新 review_status/corrected/reviewed_by/reviewed_at。"""
    async with async_session_maker() as session:
        row = await session.get(DocExtractResult, result_id)
        if row:
            row.review_status = status
            if corrected is not None:
                row.corrected = corrected
            row.reviewed_by = reviewed_by
            row.reviewed_at = datetime.now()
            await session.commit()


async def get_latest_result_for_file(customer_file_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        row = (await session.execute(
            select(DocExtractResult)
            .where(DocExtractResult.customer_file_id == customer_file_id)
            .order_by(DocExtractResult.id.desc()).limit(1)
        )).scalars().first()
        return _to_result_dict(row) if row else None
