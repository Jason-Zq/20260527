"""profile 域 CRUD: 家庭(profile_households) / 人(profile_persons) / 字段级档案(profile_person_fields)。

画像 v2 独立领域模型:不写 clients/family_members 老表,仅 legacy_client_id 软关联。
字段写入语义(证据链 + 人工主权 + 可信度分层):
  - 已有字段 status 为 confirmed/corrected(人工) → 一律不覆盖(skipped_confirmed)
  - declared(自报)与 verified(官方证件)同等对待,不因来源层跳过(layer 列仅作信息留存)
  - 其余情况 AI 值可更新 AI 值(written/updated)
  - 复核修正(correct_person_field) → 永远覆盖,status='corrected'
"""
from datetime import date, datetime
from typing import Optional

import re
from sqlalchemy import func, select

from db.ai_api_call_crud import _clean_text
from db.client_profile_crud import _parse_date, _clean_str
from db.doc_extract_crud import is_masked, valid_id_number
from db.engine import async_session_maker
from db.models import CustomerFile, DocExtractResult, ProfileAsset, ProfileCase, ProfileHousehold, ProfileImportTask, ProfilePerson, ProfilePersonField


# ==================== 字段字典(规则 target 的取值范围 + 默认可信度层) ====================

PROFILE_FIELDS: dict[str, tuple[str, str]] = {
    # verified 层(官方证件来源)
    "name": ("姓名", "verified"),
    "name_en": ("拼音/英文名", "verified"),
    "gender": ("性别", "verified"),
    "birth_date": ("出生日期", "verified"),
    "birth_place": ("出生地", "verified"),
    "nationality": ("国籍", "verified"),
    "ethnicity": ("民族", "verified"),
    "id_number": ("身份证号", "verified"),
    "hukou_address": ("户籍地址", "verified"),
    "marital_status": ("婚姻状况", "verified"),
    "passport_no": ("护照号", "verified"),
    "passport_issue_date": ("护照签发日期", "verified"),
    "passport_expiry_date": ("护照到期日期", "verified"),
    "birth_cert_no": ("出生医学证编号", "verified"),
    "birth_hospital": ("出生医院", "verified"),
    "marriage_date": ("结婚登记日期", "verified"),
    "marriage_authority": ("结婚登记机关", "verified"),
    "marriage_cert_no": ("结婚证编号", "verified"),
    "no_crime_cert_no": ("无犯罪记录证明编号", "verified"),
    "no_crime_issue_date": ("无犯罪证明开具日期", "verified"),
    "approval_no": ("批复号/获批卡号", "verified"),
    "approval_date": ("批复/签发日期", "verified"),
    "school_name": ("学校名", "verified"),
    "major": ("专业", "verified"),
    "degree": ("学位", "verified"),
    "graduation_date": ("毕业日期", "verified"),
    "graduation_cert_no": ("毕业证编号", "verified"),
    "degree_cert_no": ("学位证编号", "verified"),
    # declared 层(客户自报,如 KYC 表)
    "phone": ("手机", "declared"),
    "email": ("邮箱", "declared"),
    "current_address": ("现住址", "declared"),
    "postal_code": ("邮政编码", "declared"),
    "occupation": ("职务", "declared"),
    "employer": ("公司/雇主", "declared"),
    "business_nature": ("公司性质", "declared"),
    "annual_income": ("年收入", "declared"),
    "shareholding": ("持股情况", "declared"),
    "source_of_funds": ("资产来源", "declared"),
    "planned_deposit": ("预计存款", "declared"),
    "residence_plan": ("居住地计划", "declared"),
}

DATE_FIELDS = {"birth_date", "passport_issue_date", "passport_expiry_date",
               "marriage_date", "graduation_date", "no_crime_issue_date", "approval_date"}

_HUMAN_STATUSES = ("confirmed", "corrected")

# 特殊字段:_relation 不入 person_fields,而是写 person.relation_to_main(仅当前为"待确认"时落地)
_RELATION_FIELD = "_relation"
_VALID_RELATIONS = ("户主", "配偶", "子", "女", "父", "母")
_RELATION_ALIASES = {
    "户主": "户主", "本人": "户主", "配偶": "配偶", "妻": "配偶", "妻子": "配偶",
    "夫": "配偶", "丈夫": "配偶", "爱人": "配偶",
    "子": "子", "儿子": "子", "长子": "子", "次子": "子",
    "女": "女", "女儿": "女", "长女": "女", "次女": "女",
    "父": "父", "父亲": "父", "母": "母", "母亲": "母",
}


def normalize_relation(value) -> Optional[str]:
    """关系归一化:儿子→子、妻子→配偶 等;无法识别返回 None。"""
    if not value:
        return None
    s = str(value).strip()
    return _RELATION_ALIASES.get(s)


_NAME_RE = None  # 延迟编译(见 plausible_person_name)


def plausible_person_name(value) -> bool:
    """人名合理性:纯 CJK(可含间隔号·),2-15 字。

    防乱码建人:实测歪扫户口页产出"钅 lil蝴哪"这种 部首+拉丁 混合假名。
    中文名(含少数民族间隔号,如 阿不都·外力)能通过;夹拉丁/数字/符号的一律否。
    """
    global _NAME_RE
    if _NAME_RE is None:
        import re
        _NAME_RE = re.compile(r"^[一-鿿·]{2,15}$")
    if not value:
        return False
    return bool(_NAME_RE.match(str(value).strip()))


def field_label(field: str) -> str:
    return PROFILE_FIELDS.get(field, (field, ""))[0]


def default_layer(field: str) -> str:
    return PROFILE_FIELDS.get(field, ("", "verified"))[1]


# ==================== 序列化 ====================

def _to_household_dict(h: ProfileHousehold) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "legacy_client_id": h.legacy_client_id,
        "main_person_id": h.main_person_id,
        "created_at": h.created_at.strftime("%Y-%m-%d %H:%M:%S") if h.created_at else "",
        "updated_at": h.updated_at.strftime("%Y-%m-%d %H:%M:%S") if h.updated_at else "",
    }


def _to_field_dict(f: ProfilePersonField) -> dict:
    return {
        "field": f.field,
        "label": field_label(f.field),
        "value": f.value,
        "layer": f.layer,
        "source_file_id": f.source_file_id,
        "source_result_id": f.source_result_id,
        "status": f.status,
        "updated_by": f.updated_by,
        "updated_at": f.updated_at.strftime("%Y-%m-%d %H:%M:%S") if f.updated_at else "",
    }


def _to_person_dict(p: ProfilePerson, fields: Optional[list] = None) -> dict:
    return {
        "id": p.id,
        "household_id": p.household_id,
        "name": p.name,
        "relation_to_main": p.relation_to_main,
        "is_main": p.is_main,
        "avatar_file_id": p.avatar_file_id,
        "fields": fields if fields is not None else [],
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else "",
        "updated_at": p.updated_at.strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else "",
    }


# ==================== 家庭 / 人 ====================

async def get_or_create_household(name: str, legacy_client_id: Optional[int] = None) -> dict:
    """按名称找家庭,没有则创建(同时建主申请人:relation=户主,is_main=True)。"""
    async with async_session_maker() as session:
        h = await session.scalar(
            select(ProfileHousehold).where(ProfileHousehold.name == name)
            .order_by(ProfileHousehold.id))
        if not h:
            h = ProfileHousehold(name=name, legacy_client_id=legacy_client_id,
                                 created_at=datetime.now(), updated_at=datetime.now())
            session.add(h)
            await session.flush()
            main = ProfilePerson(household_id=h.id, name=name, relation_to_main="户主",
                                 is_main=True, created_at=datetime.now(), updated_at=datetime.now())
            session.add(main)
            await session.flush()
            h.main_person_id = main.id
        elif legacy_client_id and not h.legacy_client_id:
            h.legacy_client_id = legacy_client_id
            h.updated_at = datetime.now()
        await session.commit()
        await session.refresh(h)
        return _to_household_dict(h)


async def get_household(household_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        h = await session.get(ProfileHousehold, household_id)
        return _to_household_dict(h) if h else None


async def list_persons(household_id: int) -> list[dict]:
    """家庭成员列表,每人带字段档案(按 is_main 优先、id 排序)。"""
    async with async_session_maker() as session:
        persons = (await session.execute(
            select(ProfilePerson).where(ProfilePerson.household_id == household_id)
            .order_by(ProfilePerson.is_main.desc(), ProfilePerson.id)
        )).scalars().all()
        if not persons:
            return []
        frows = (await session.execute(
            select(ProfilePersonField)
            .where(ProfilePersonField.person_id.in_([p.id for p in persons]))
            .order_by(ProfilePersonField.id)
        )).scalars().all()
        by_person: dict[int, list] = {}
        for f in frows:
            by_person.setdefault(f.person_id, []).append(_to_field_dict(f))
        return [_to_person_dict(p, by_person.get(p.id, [])) for p in persons]


# ==================== 护照到期提醒 ====================

PASSPORT_EXPIRY_WARNING_DAYS = 180  # 移民递签惯例:护照剩余有效期需 ≥6 个月


def passport_expiry_info(fields: Optional[list], today: Optional[date] = None) -> Optional[dict]:
    """从 person 的 fields 里取护照到期日,算剩余天数与级别。无数据/解析失败返回 None。"""
    value = next((f.get("value") for f in (fields or [])
                  if f.get("field") == "passport_expiry_date"), None)
    expiry = _parse_date(value)
    if not expiry:
        return None
    today = today or date.today()
    days_left = (expiry - today).days
    level = "expired" if days_left < 0 else (
        "expiring" if days_left <= PASSPORT_EXPIRY_WARNING_DAYS else "ok")
    return {"date": expiry.isoformat(), "days_left": days_left, "level": level}


def attach_passport_expiry(persons: list[dict], today: Optional[date] = None) -> list[dict]:
    """给 list_persons 结果每人挂 passport_expiry(画像弹窗到期提醒展示用)。"""
    for p in persons:
        p["passport_expiry"] = passport_expiry_info(p.get("fields"), today)
    return persons


# ==================== 交叉验证(多来源同名字段比对,只提示不改值) ====================

# 只比 verified 身份字段;KYC declared 字段(电话/职业等)自报易变,比对只会产生噪音
_CROSS_CHECK_FIELDS = {"name", "name_en", "gender", "birth_date", "id_number",
                       "passport_no", "nationality", "marital_status"}

_GENDER_MAP = {"男": "M", "男性": "M", "m": "M", "male": "M",
               "女": "F", "女性": "F", "f": "F", "female": "F"}


def _normalize_field_value(field: str, value) -> str:
    """字段值归一化(交叉验证比对用)。空/脱敏 → "";解析不了的日期保留原文(仅提示场景,可接受)。"""
    text = str(value or "").strip()
    if not text or is_masked(text):
        return ""
    if field in DATE_FIELDS:
        d = _parse_date(text)
        return d.isoformat() if d else text
    if field in ("name", "name_en"):
        # 拉丁字母名(拼音):忽略大小写与空格/粘连差异(NI ZHAOHUI == NIZhaohui),词序仍参与比对
        if re.fullmatch(r"[A-Za-z .\-']+", text):
            return re.sub(r"[^A-Za-z]", "", text).upper()
        return re.sub(r"\s+", "", text)
    if field == "gender":
        return _GENDER_MAP.get(text, _GENDER_MAP.get(text.lower(), text))
    if field in ("id_number", "passport_no"):
        return re.sub(r"\s+", "", text).upper()
    return re.sub(r"\s+", " ", text)


def collect_field_conflicts(samples: list) -> dict:
    """纯函数:样本 (person_id, field, raw_value, source, doc_type) → 冲突明细。

    同一人同一字段出现 ≥2 个不同归一化值即冲突;values 按归一化值分组列出全部来源。
    """
    by_key: dict[tuple, dict] = {}
    for person_id, field, raw_value, source, doc_type, cfid in samples:
        if not person_id or field not in _CROSS_CHECK_FIELDS:
            continue
        norm = _normalize_field_value(field, raw_value)
        if not norm:
            continue
        variants = by_key.setdefault((person_id, field), {})
        entry = variants.setdefault(norm, {"value": str(raw_value).strip(), "sources": []})
        s = {"source": source, "doc_type": doc_type, "customer_file_id": cfid}
        if s not in entry["sources"]:
            entry["sources"].append(s)
    out: dict[int, dict] = {}
    for (pid, field), variants in by_key.items():
        if len(variants) < 2:
            continue
        out.setdefault(pid, {})[field] = {
            "label": field_label(field),
            "values": [{"value": v["value"], "sources": v["sources"]} for v in variants.values()],
        }
    return out


async def attach_field_conflicts(persons: list[dict], household_id: int) -> list[dict]:
    """给每人挂 field_conflicts:该家庭全部 done 提取结果里,同名字段多来源值不一致的明细。"""
    async with async_session_maker() as session:
        task_ids = (await session.execute(
            select(ProfileImportTask.id).where(ProfileImportTask.household_id == household_id)
        )).scalars().all()
        results = []
        fnames: dict[int, str] = {}
        if task_ids:
            results = (await session.execute(
                select(DocExtractResult).where(DocExtractResult.import_task_id.in_(task_ids),
                                               DocExtractResult.status == "done")
            )).scalars().all()
            file_ids = {r.customer_file_id for r in results}
            if file_ids:
                fnames = dict((await session.execute(
                    select(CustomerFile.id, CustomerFile.filename)
                    .where(CustomerFile.id.in_(file_ids))
                )).all())
    samples = []
    for r in results:
        pid = (r.write_stats or {}).get("person_id")
        if not pid:
            continue
        extracted = r.extracted or {}
        source = fnames.get(r.customer_file_id) or r.file_id
        for m in (r.mapped or []):
            field = m.get("field")
            if field not in _CROSS_CHECK_FIELDS:
                continue
            if m.get("action") in ("skipped_masked", "skipped_invalid"):
                continue
            key = m.get("key")
            if key not in extracted:
                continue
            samples.append((pid, field, extracted.get(key), source, r.doc_type, r.customer_file_id))
    conflicts = collect_field_conflicts(samples)
    for p in persons:
        p["field_conflicts"] = conflicts.get(p["id"], {})
    return persons


def _normalize_name_en(value) -> str:
    """拼音名归一化:大写 + 按非字母切词 + 词序无关(NI ZHAOHUI == ZHAOHUI NI)。"""
    if not value:
        return ""
    import re
    tokens = re.findall(r"[A-Za-z]+", str(value).upper())
    return " ".join(sorted(tokens))


async def find_person_match(household_id: int, id_number: Optional[str] = None,
                            name: Optional[str] = None,
                            name_en: Optional[str] = None) -> dict:
    """家庭内归因:证件号(走 person_fields)→ 姓名(走 persons.name)→ 拼音名(走 person_fields,词序无关)。"""
    async with async_session_maker() as session:
        if id_number:
            row = (await session.execute(
                select(ProfilePerson)
                .join(ProfilePersonField, ProfilePersonField.person_id == ProfilePerson.id)
                .where(ProfilePerson.household_id == household_id,
                       ProfilePersonField.field == "id_number",
                       ProfilePersonField.value == id_number)
            )).scalars().first()
            if row:
                return {"person_id": row.id, "matched_by": "id_number"}
        if name:
            row = await session.scalar(
                select(ProfilePerson).where(ProfilePerson.household_id == household_id,
                                            ProfilePerson.name == name)
                .order_by(ProfilePerson.id))
            if row:
                return {"person_id": row.id, "matched_by": "name"}
        norm = _normalize_name_en(name_en)
        if norm:
            frows = (await session.execute(
                select(ProfilePersonField)
                .join(ProfilePerson, ProfilePerson.id == ProfilePersonField.person_id)
                .where(ProfilePerson.household_id == household_id,
                       ProfilePersonField.field == "name_en")
            )).scalars().all()
            for f in frows:
                if _normalize_name_en(f.value) == norm:
                    return {"person_id": f.person_id, "matched_by": "name_en"}
    return {"person_id": None, "matched_by": None}


async def create_person(household_id: int, name: str, relation: str = "待确认") -> dict:
    async with async_session_maker() as session:
        p = ProfilePerson(household_id=household_id, name=name, relation_to_main=relation,
                          is_main=False, created_at=datetime.now(), updated_at=datetime.now())
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return _to_person_dict(p)


async def set_person_relation(person_id: int, relation: str) -> None:
    async with async_session_maker() as session:
        p = await session.get(ProfilePerson, person_id)
        if p:
            p.relation_to_main = relation
            p.updated_at = datetime.now()
            await session.commit()


# ==================== 字段写入(AI 提取) ====================

async def apply_extracted_fields_v2(household_id: int, match: dict, field_items: list, *,
                                    source_file_id: Optional[int] = None,
                                    source_result_id: Optional[int] = None) -> dict:
    """把提取字段写入 profile_person_fields。

    match: {"person_id": int|None, "matched_by": ...};person_id 为 None 且有 name → 新建 person。
    field_items: [{key, value, column(=profile 字段名), layer(可选,默认按字段字典)}]
    返回 {person_id, mapped, write_stats}
    """
    mapped: list[dict] = []
    stats = {"matched_by": match.get("matched_by"), "written": 0, "updated": 0,
             "person_created": 0}

    async with async_session_maker() as session:
        person_id = match.get("person_id")
        if person_id is None:
            name = next((it["value"] for it in field_items
                         if it.get("column") == "name" and it.get("value")), None)
            if name and not plausible_person_name(name):
                name = None  # 乱码假名不建人(如 "钅 lil蝴哪")
            if name:
                p = ProfilePerson(household_id=household_id, name=name,
                                  relation_to_main="待确认", is_main=False,
                                  created_at=datetime.now(), updated_at=datetime.now())
                session.add(p)
                await session.flush()
                person_id = p.id
                stats["person_created"] = 1
        if person_id is None:
            return {"person_id": None, "mapped": mapped, "write_stats": stats}
        stats["person_id"] = person_id

        person = await session.get(ProfilePerson, person_id)
        existing = {
            f.field: f
            for f in (await session.execute(
                select(ProfilePersonField).where(ProfilePersonField.person_id == person_id)
            )).scalars().all()
        }

        for it in field_items:
            key, value, field = it.get("key"), it.get("value"), it.get("column")
            entry = {"key": key, "field": field, "person_id": person_id}
            if not value or not field:
                continue
            if is_masked(value):
                mapped.append({**entry, "action": "skipped_masked"})
                continue
            # 特殊通道:与户主关系 → 写 person.relation_to_main(仅在"待确认"时落地)
            if field == _RELATION_FIELD:
                rel = normalize_relation(value)
                if not rel:
                    mapped.append({**entry, "action": "skipped_invalid"})
                    continue
                if person is not None and person.relation_to_main and person.relation_to_main != "待确认":
                    mapped.append({**entry, "action": "skipped_filled"})
                    continue
                if person is not None:
                    person.relation_to_main = rel
                    person.updated_at = datetime.now()
                    mapped.append({**entry, "action": "relation_written"})
                    stats["written"] += 1
                continue
            layer = it.get("layer") or default_layer(field)
            if field == "id_number" and not valid_id_number(value):
                mapped.append({**entry, "action": "skipped_invalid"})
                continue
            if field in DATE_FIELDS:
                parsed = _parse_date(value)
                if parsed is None:
                    mapped.append({**entry, "action": "skipped_invalid"})
                    continue
                value = parsed.isoformat()
            else:
                value = _clean_str(value)
            if not value:
                continue

            row = existing.get(field)
            if row is not None and row.status in _HUMAN_STATUSES:
                mapped.append({**entry, "action": "skipped_confirmed"})
                continue
            if row is not None and (row.value or "") == value:
                mapped.append({**entry, "action": "skipped_same"})
                continue

            if row is None:
                row = ProfilePersonField(
                    person_id=person_id, field=field, value=value, layer=layer,
                    source_file_id=source_file_id, source_result_id=source_result_id,
                    status="ai", updated_by="AI",
                    created_at=datetime.now(), updated_at=datetime.now())
                session.add(row)
                existing[field] = row
                mapped.append({**entry, "action": "written"})
                stats["written"] += 1
            else:
                row.value = value
                row.layer = layer
                row.source_file_id = source_file_id
                row.source_result_id = source_result_id
                row.status = "ai"
                row.updated_by = "AI"
                row.updated_at = datetime.now()
                mapped.append({**entry, "action": "updated"})
                stats["updated"] += 1
        await session.commit()
    return {"person_id": person_id, "mapped": mapped, "write_stats": stats}


# ==================== 字段修正(人工复核) ====================

async def correct_person_field(person_id: int, field: str, value: Optional[str], *,
                               corrected_by: Optional[str] = None,
                               source_result_id: Optional[int] = None) -> dict:
    """人工修正:永远覆盖,status='corrected'。value 为空表示清除该字段。"""
    async with async_session_maker() as session:
        row = await session.scalar(
            select(ProfilePersonField).where(ProfilePersonField.person_id == person_id,
                                             ProfilePersonField.field == field))
        clean = _clean_str(value)
        if row is None:
            if clean is None:
                return {"ok": True, "action": "noop"}
            row = ProfilePersonField(person_id=person_id, field=field, created_at=datetime.now())
            session.add(row)
        if clean is None:
            if row.id is not None:
                await session.delete(row)
            await session.commit()
            return {"ok": True, "action": "cleared"}
        if field in DATE_FIELDS:
            parsed = _parse_date(clean)
            if parsed is None:
                raise ValueError(f"日期格式无法识别: {clean}")
            clean = parsed.isoformat()
        row.value = clean
        row.layer = row.layer or default_layer(field)
        row.source_result_id = source_result_id or row.source_result_id
        row.status = "corrected"
        row.updated_by = corrected_by or "复核员"
        row.updated_at = datetime.now()
        await session.commit()
        return {"ok": True, "action": "corrected"}


async def count_persons(household_id: int) -> int:
    async with async_session_maker() as session:
        return await session.scalar(
            select(func.count(ProfilePerson.id)).where(ProfilePerson.household_id == household_id)
        ) or 0


# ==================== 资产写入(AI 提取,entity=asset) ====================

async def apply_extracted_asset(household_id: int, owner_person_id: Optional[int],
                                asset_items: list, *, asset_type: str = "房产",
                                source_file_id: Optional[int] = None) -> dict:
    """把 entity=asset 的提取字段写入 profile_assets(attrs JSONB 存 key:value)。

    去重(方案 C:纯 AI 判定):
      - 家庭内同 asset_type 无候选 → 直接新建(0 次 LLM)
      - 有候选 → llm_service.judge_asset_duplicate 判定,match_id + confidence≥60 才合并
      - LLM 失败/无结果 → 保底新建(不阻塞主流程)
      - 已有行 status='ai' 才可被 AI 更新(人工 confirmed/corrected 不动)
    返回 {asset_id, mapped, stats}
    """
    attrs: dict[str, str] = {}
    for it in asset_items:
        key, value = it.get("key"), it.get("value")
        if not key or value is None or is_masked(str(value)):
            continue
        clean = _clean_str(value)
        if clean:
            attrs[key] = clean
    stats = {"asset_created": 0, "asset_updated": 0}
    if not attrs:
        return {"asset_id": None, "mapped": [], "stats": stats}

    name = attrs.get("address") or attrs.get("cert_no") or asset_type
    mapped = [{"key": k, "field": f"asset:{k}", "action": "asset_field"} for k in attrs]

    async with async_session_maker() as session:
        # 拉家庭内同类型全部候选
        candidates = list((await session.scalars(
            select(ProfileAsset).where(
                ProfileAsset.household_id == household_id,
                ProfileAsset.asset_type == asset_type))).all())

        row = None
        dedup_info: dict = {}
        if candidates:
            # 调 LLM 判定是否与已有资产重复(同步函数,包在 to_thread 里避免阻塞事件循环)
            import asyncio
            import llm_service
            dedup_info = await asyncio.to_thread(
                llm_service.judge_asset_duplicate, attrs, candidates,
                task_id=str(source_file_id) if source_file_id else None)
            match_id = dedup_info.get("match_id")
            confidence = dedup_info.get("confidence") or 0
            if match_id and confidence >= 60:
                row = next((c for c in candidates if c.id == match_id), None)
            mapped.append({
                "key": "asset", "field": "asset:dedup",
                "action": "dedup_matched" if row else "dedup_new",
                "match_id": match_id, "confidence": confidence,
                "reason": dedup_info.get("reason", ""),
            })

        if row is None:
            row = ProfileAsset(
                household_id=household_id, owner_person_id=owner_person_id,
                asset_type=asset_type, name=name, attrs=attrs,
                source_file_id=source_file_id, status="ai",
                created_at=datetime.now(), updated_at=datetime.now())
            session.add(row)
            await session.flush()
            stats["asset_created"] = 1
            mapped.append({"key": "asset", "field": "asset", "action": "asset_created"})
        elif row.status == "ai":
            row.attrs = {**(row.attrs or {}), **attrs}
            row.name = name
            if owner_person_id:
                row.owner_person_id = owner_person_id
            row.source_file_id = source_file_id
            row.updated_at = datetime.now()
            stats["asset_updated"] = 1
            mapped.append({"key": "asset", "field": "asset", "action": "asset_updated"})
        else:
            mapped.append({"key": "asset", "field": "asset", "action": "asset_skipped_confirmed"})
        await session.commit()
        return {"asset_id": row.id, "mapped": mapped, "stats": stats}


def _to_asset_dict(a: ProfileAsset) -> dict:
    return {
        "id": a.id,
        "household_id": a.household_id,
        "owner_person_id": a.owner_person_id,
        "asset_type": a.asset_type,
        "name": a.name,
        "attrs": a.attrs or {},
        "source_file_id": a.source_file_id,
        "status": a.status,
        "updated_at": a.updated_at.strftime("%Y-%m-%d %H:%M:%S") if a.updated_at else "",
    }


async def list_assets(household_id: int) -> list[dict]:
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(ProfileAsset).where(ProfileAsset.household_id == household_id)
            .order_by(ProfileAsset.id)
        )).scalars().all()
        return [_to_asset_dict(a) for a in rows]


async def merge_assets(household_id: int, groups: list[dict]) -> tuple[int, int]:
    """执行资产合并,返回 (合并的组数, 删除的行数)。

    groups: [{keep_id, merge_ids, merged_attrs, merged_name}]
    安全校验:
      - keep_id / merge_ids 必须都属于该 household
      - 所有 keep_id / merge_ids 都必须是 status='ai' (人工修正过的不动)
      - 同一条不得在多个组出现(每个资产最多参与一次)
      - 每个组至少有 1 个 merge_id
    """
    if not groups:
        return 0, 0

    async with async_session_maker() as session:
        all_ids = set()
        for g in groups:
            all_ids.add(g["keep_id"])
            all_ids.update(g["merge_ids"])
        all_rows = (await session.execute(
            select(ProfileAsset).where(ProfileAsset.id.in_(all_ids))
        )).scalars().all()
        by_id = {a.id: a for a in all_rows}

        # 校验
        if len(all_rows) != len(all_ids):
            missing = all_ids - set(by_id.keys())
            raise ValueError(f"部分资产 id 不存在: {sorted(missing)}")
        for a in all_rows:
            if a.household_id != household_id:
                raise ValueError(f"资产 {a.id} 不属于目标家庭")
            if a.status != "ai":
                raise ValueError(f"资产 {a.id} status={a.status},非 AI 生成,禁止合并(人工修正过的需手动处理)")

        seen = set()
        for g in groups:
            if not g["merge_ids"]:
                raise ValueError(f"组 {g['keep_id']} 无合并目标")
            ids_in_group = {g["keep_id"]} | set(g["merge_ids"])
            if seen & ids_in_group:
                raise ValueError(f"资产 id 重复出现在多个组: {sorted(seen & ids_in_group)}")
            seen |= ids_in_group

        # 执行
        merged_groups = 0
        deleted_rows = 0
        for g in groups:
            keep = by_id[g["keep_id"]]
            keep.attrs = g.get("merged_attrs") or {}
            keep.name = g.get("merged_name") or keep.name
            keep.updated_at = datetime.now()
            for mid in g["merge_ids"]:
                await session.delete(by_id[mid])
                deleted_rows += 1
            merged_groups += 1
        await session.commit()
        return merged_groups, deleted_rows


# ==================== 案件时间线(AI 提取,entity=case) ====================

CASE_PLACEHOLDER_TYPE = "未命名案件"
# 阶段优先级:命中靠前者为案件当前状态
_CASE_STATUS_PROGRESSION = [("签收", "已签收"), ("交付", "已交付"), ("获批", "已获批"), ("递交", "已递交")]


async def apply_case_milestones(household_id: int, case_items: list, *,
                                source_file_id: Optional[int] = None) -> dict:
    """把 entity=case 的提取字段写入 profile_cases(家庭单案件,v1)。

    case_items: [{key, label, value}];key='case_type' → 案件类型(仅占位时可被覆盖);
    其余 → 里程碑 {name=label, date=value(需可解析), source_file_id},按 name upsert。
    状态从里程碑派生:签收>交付>获批>递交。返回 {case_id, mapped, stats}
    """
    milestones_in: list[tuple[str, str]] = []
    case_type_hint: Optional[str] = None
    mapped: list[dict] = []
    stats = {"case_created": 0, "milestone_created": 0, "milestone_updated": 0}

    for it in case_items:
        key, value = it.get("key"), it.get("value")
        if not key or value is None or is_masked(str(value)):
            continue
        if key == "case_type":
            hint = _clean_str(value)
            if hint:
                case_type_hint = hint
            continue
        parsed = _parse_date(str(value).strip())
        if parsed is None:
            mapped.append({"key": key, "field": f"case:{key}", "action": "skipped_invalid"})
            continue
        milestones_in.append((it.get("label") or key, parsed.isoformat()))

    if not milestones_in and not case_type_hint:
        return {"case_id": None, "mapped": mapped, "stats": stats}

    async with async_session_maker() as session:
        case = await session.scalar(
            select(ProfileCase).where(ProfileCase.household_id == household_id)
            .order_by(ProfileCase.id.desc()).limit(1))
        if case is None:
            case = ProfileCase(household_id=household_id,
                               case_type=case_type_hint or CASE_PLACEHOLDER_TYPE,
                               status="进行中", milestones=[],
                               created_at=datetime.now(), updated_at=datetime.now())
            session.add(case)
            await session.flush()
            stats["case_created"] = 1
            mapped.append({"key": "case", "field": "case", "action": "case_created"})
        elif case_type_hint and case.case_type == CASE_PLACEHOLDER_TYPE:
            case.case_type = case_type_hint

        ms = [dict(m) for m in (case.milestones or [])]
        by_name = {m.get("name"): m for m in ms}
        for name, date in milestones_in:
            existing = by_name.get(name)
            entry = {"key": name, "field": f"case:{name}"}
            if existing is not None and existing.get("date") == date:
                mapped.append({**entry, "action": "skipped_same"})
            elif existing is not None:
                existing["date"] = date
                existing["source_file_id"] = source_file_id
                stats["milestone_updated"] += 1
                mapped.append({**entry, "action": "milestone_updated"})
            else:
                ms.append({"name": name, "date": date, "source_file_id": source_file_id})
                stats["milestone_created"] += 1
                mapped.append({**entry, "action": "milestone_created"})

        names = {m.get("name") for m in ms}
        case.status = next((st for key, st in _CASE_STATUS_PROGRESSION if key in names), "进行中")
        ms.sort(key=lambda m: m.get("date") or "")
        case.milestones = ms  # JSONB 整体重赋值(原地改不触发变更检测)
        case.updated_at = datetime.now()
        await session.commit()
        return {"case_id": case.id, "mapped": mapped, "stats": stats}


async def list_cases(household_id: int) -> list[dict]:
    """案件列表(里程碑带来源文件名,前端时间线用)。"""
    async with async_session_maker() as session:
        cases = (await session.execute(
            select(ProfileCase).where(ProfileCase.household_id == household_id)
            .order_by(ProfileCase.id)
        )).scalars().all()
        if not cases:
            return []
        file_ids = {m.get("source_file_id") for c in cases for m in (c.milestones or [])}
        file_ids.discard(None)
        fname: dict[int, str] = {}
        if file_ids:
            rows = (await session.execute(
                select(CustomerFile.id, CustomerFile.filename)
                .where(CustomerFile.id.in_(file_ids)))).all()
            fname = {fid: fn for fid, fn in rows}
        out = []
        for c in cases:
            milestones = [
                {**m, "source_filename": fname.get(m.get("source_file_id"))}
                for m in (c.milestones or [])
            ]
            milestones.sort(key=lambda m: m.get("date") or "")
            out.append({
                "id": c.id,
                "household_id": c.household_id,
                "case_type": c.case_type,
                "status": c.status,
                "milestones": milestones,
                "updated_at": c.updated_at.strftime("%Y-%m-%d %H:%M:%S") if c.updated_at else "",
            })
        return out


# ==================== 完备度矩阵(人 × 材料类型) ====================

# 矩阵列: (type_key, 中文, scope)
#   scope=person: 按人核查;household: 家庭级(有即全家算有);couple: 仅户主/配偶行核查
MATRIX_COLUMNS = [
    ("id_card", "身份证", "person"),
    ("passport", "护照", "person"),
    ("hukou", "户口本", "household"),
    ("birth_cert", "出生证明", "person"),
    ("marriage_cert", "结婚证", "couple"),
    ("no_crime", "无犯罪记录", "person"),
    ("degree_cert", "学位证", "person"),
]

# 文件类型解析:doc_type 不在矩阵类型里时,按文件名/文件夹提示词归并
_TYPE_FILENAME_HINTS = [
    ("birth_cert", ["出生证", "出生医学证明"]),
    ("no_crime", ["无犯罪"]),
    ("marriage_cert", ["结婚证"]),
    ("degree_cert", ["学位证", "学位证书"]),
]
_TYPE_FOLDER_HINTS = [
    ("no_crime", ["无犯罪"]),
    ("marriage_cert", ["结婚证", "婚姻状态"]),
    ("birth_cert", ["出生证明", "出生证"]),
    ("passport", ["护照"]),
    ("hukou", ["户口本", "户口簿"]),
]

_MATRIX_TYPE_KEYS = {k for k, _, _ in MATRIX_COLUMNS}


def resolve_matrix_type(file_row: dict) -> Optional[str]:
    """把 customer_files 行归并到矩阵类型(归不进返回 None)。"""
    dt = file_row.get("doc_type")
    if dt in _MATRIX_TYPE_KEYS:
        return dt
    name = (file_row.get("filename") or "")
    for t, hints in _TYPE_FILENAME_HINTS:
        if any(h in name for h in hints):
            return t
    folder = (file_row.get("folder_name") or "") + (file_row.get("rel_path") or "")
    for t, hints in _TYPE_FOLDER_HINTS:
        if any(h in folder for h in hints):
            return t
    return None


async def build_completeness_matrix(task: dict) -> dict:
    """完备度矩阵:行=家庭成员,列=材料类型,格=ok(好)/warn(有待复核)/missing(缺)/na(不适用)。

    人档关联:① 提取结果归因(write_stats.person_id) ② 文件名/文件夹含人名 ③ household 列家庭共享。
    """
    from db import customer_file_crud, doc_extract_crud

    household_id = task.get("household_id")
    if not household_id:
        return {"persons": [], "columns": MATRIX_COLUMNS, "cells": {}}
    persons = await list_persons(household_id)
    files, _ = await customer_file_crud.list_task_files(task["id"], limit=1000)
    results, _ = await doc_extract_crud.list_results(import_task_id=task["id"], limit=1000)

    # ① 归因关联:person_id -> file ids
    linked: dict[int, set] = {}
    for r in results:
        pid = ((r.get("write_stats") or {}).get("person_id"))
        if pid:
            linked.setdefault(pid, set()).add(r["customer_file_id"])

    # ② 人名关联 + 类型归并
    cells: dict[int, dict] = {}
    household_files_by_type: dict[str, list] = {}
    for f in files:
        t = resolve_matrix_type(f)
        if t:
            household_files_by_type.setdefault(t, []).append(f)

    for p in persons:
        pid, pname = p["id"], p["name"]
        person_cells = {}
        my_ids = linked.get(pid, set())
        for type_key, _label, scope in MATRIX_COLUMNS:
            if scope == "couple" and p["relation_to_main"] not in ("户主", "配偶"):
                person_cells[type_key] = {"status": "na", "files": []}
                continue
            if scope == "household" or scope == "couple":
                # 家庭/夫妻共用件(户口本/结婚证),不按人名过滤
                fs = household_files_by_type.get(type_key, [])
            else:
                fs = [f for f in household_files_by_type.get(type_key, [])
                      if f["id"] in my_ids
                      or pname in (f.get("filename") or "")
                      or pname in (f.get("folder_name") or "")]
            if not fs:
                person_cells[type_key] = {"status": "missing", "files": []}
                continue
            # 有任一可用文件即算齐(ok);全部待复核才 warn(没有可用的)
            has_usable = any(f.get("review_status") != "needs_review" for f in fs)
            person_cells[type_key] = {
                "status": "ok" if has_usable else "warn",
                "files": [{"id": f["id"], "filename": f.get("filename"),
                           "file_code": f.get("file_code"),
                           "review_status": f.get("review_status"),
                           "review_reason": f.get("review_reason")} for f in fs],
            }
        cells[pid] = person_cells

    return {
        "persons": [{"id": p["id"], "name": p["name"], "relation_to_main": p["relation_to_main"],
                     "is_main": p["is_main"]} for p in persons],
        "columns": [{"key": k, "label": l, "scope": s} for k, l, s in MATRIX_COLUMNS],
        "cells": cells,
    }
