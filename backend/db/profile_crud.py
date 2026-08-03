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
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

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
    "spouse_name": ("配偶姓名", "verified"),
    "no_crime_cert_no": ("无犯罪记录证明编号", "verified"),
    "no_crime_issue_date": ("无犯罪证明开具日期", "verified"),
    "approval_no": ("批复号/获批卡号", "verified"),
    "approval_date": ("批复/签发日期", "verified"),
    "approval_expiry_date": ("准证/批复有效期至", "verified"),
    "sponsor_name": ("主卡/主签持证人", "verified"),
    "id_card_expiry_date": ("身份证有效期至", "verified"),
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
               "marriage_date", "graduation_date", "no_crime_issue_date", "approval_date",
               "approval_expiry_date", "id_card_expiry_date"}

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


def plausible_latin_name(value) -> bool:
    """拉丁名合理性(建人门槛):字母/空格/.'- 组成,2-4 个词,每词 ≥2 字母,总长 5-40。

    用于英文证件(批复/准证/护照)无中文名时按 name_en 建卡。要求 ≥2 词
    (姓+名结构),防 OCR 把 "PASSPORT"/"SGWORKPASS" 这类单词噪声建成人。
    """
    if not value:
        return False
    text = str(value).strip()
    if not (5 <= len(text) <= 40):
        return False
    words = [w for w in re.split(r"\s+", text) if w]
    if not (2 <= len(words) <= 4):
        return False
    return all(len(re.sub(r"[^A-Za-z]", "", w)) >= 2 for w in words) and \
        bool(re.fullmatch(r"[A-Za-z .'\-]+", text))


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
        "customer_code": h.customer_code,
        "crm_oid": h.crm_oid,
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

async def get_or_create_household(name: str, legacy_client_id: Optional[int] = None,
                                  customer_code: Optional[str] = None,
                                  crm_oid: Optional[str] = None) -> dict:
    """按名称找家庭(繁→简折叠去重,防客户名简/繁变体建重复家庭),没有则创建(同时建主申请人:relation=户主,is_main=True)。

    customer_code/crm_oid 为接口属性:新建写入,已存在只补空(不覆盖)。
    """
    async with async_session_maker() as session:
        folded = _fold_cjk(name)
        h = None
        for cand in (await session.execute(
                select(ProfileHousehold).order_by(ProfileHousehold.id))).scalars().all():
            if _fold_cjk(cand.name) == folded:
                h = cand
                break
        if not h:
            h = ProfileHousehold(name=name, legacy_client_id=legacy_client_id,
                                 customer_code=customer_code or None, crm_oid=crm_oid or None,
                                 created_at=datetime.now(), updated_at=datetime.now())
            session.add(h)
            await session.flush()
            main, _, _ = await _create_person_in_session(
                session, h.id, name, relation="户主", is_main=True)
            h.main_person_id = main.id
        else:
            dirty = False
            if legacy_client_id and not h.legacy_client_id:
                h.legacy_client_id = legacy_client_id
                dirty = True
            if customer_code and not h.customer_code:
                h.customer_code = customer_code
                dirty = True
            if crm_oid and not h.crm_oid:
                h.crm_oid = crm_oid
                dirty = True
            if dirty:
                h.updated_at = datetime.now()
        await session.commit()
        await session.refresh(h)
        return _to_household_dict(h)


async def get_household(household_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        h = await session.get(ProfileHousehold, household_id)
        return _to_household_dict(h) if h else None


async def list_household_ids() -> list[int]:
    """全部家庭 id(升序),全量同名合并扫描用。"""
    async with async_session_maker() as session:
        return list((await session.execute(
            select(ProfileHousehold.id).order_by(ProfileHousehold.id))).scalars().all())


async def get_person(person_id: int) -> Optional[dict]:
    """单个人员(不含字段档案),手动合并端点校验用。"""
    async with async_session_maker() as session:
        p = await session.get(ProfilePerson, person_id)
        return _to_person_dict(p) if p else None


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


# ==================== 全库证件到期提醒 ====================

# 参与到期提醒的字段 → 证件显示名(新增到期类字段时在此登记)
EXPIRY_FIELD_TYPES = {
    "passport_expiry_date": "护照",
    "approval_expiry_date": "准证/批复",
    "id_card_expiry_date": "身份证",
}


async def list_expiry_reminders(days: int = PASSPORT_EXPIRY_WARNING_DAYS,
                                include_ok: bool = False,
                                keyword: Optional[str] = None,
                                limit: int = 50, offset: int = 0) -> dict:
    """全库证件到期提醒:扫 profile_person_fields 到期类字段,按剩余天数升序。

    续签/换证 = 移民服务的 recurring revenue 入口。level: expired(<0) / expiring(≤days) / ok;
    默认只回 active(expired+expiring),include_ok=True 带全部。keyword 模糊家庭名/成员名。
    Python 层过滤(数据量小,与 sales_crud 同模式),total 为过滤后全量。
    """
    today = date.today()
    async with async_session_maker() as session:
        rows = (await session.execute(
            select(ProfilePersonField, ProfilePerson, ProfileHousehold)
            .join(ProfilePerson, ProfilePerson.id == ProfilePersonField.person_id)
            .join(ProfileHousehold, ProfileHousehold.id == ProfilePerson.household_id)
            .where(ProfilePersonField.field.in_(list(EXPIRY_FIELD_TYPES)))
            .order_by(ProfilePersonField.id)
        )).all()
    kw = (keyword or "").strip().lower()
    items: list[dict] = []
    for f, p, h in rows:
        expiry = _parse_date(f.value)
        if not expiry:
            continue  # 无法解析的日期(历史脏数据)不提醒
        days_left = (expiry - today).days
        level = "expired" if days_left < 0 else (
            "expiring" if days_left <= days else "ok")
        if level == "ok" and not include_ok:
            continue
        if kw and kw not in (h.name or "").lower() and kw not in (p.name or "").lower():
            continue
        items.append({
            "household_id": h.id, "household_name": h.name,
            "customer_code": h.customer_code,
            "person_id": p.id, "person_name": p.name,
            "relation_to_main": p.relation_to_main,
            "credential_type": EXPIRY_FIELD_TYPES[f.field], "field": f.field,
            "expiry_date": expiry.isoformat(), "days_left": days_left, "level": level,
            "field_status": f.status, "source_file_id": f.source_file_id,
        })
    items.sort(key=lambda x: x["days_left"])
    return {"total": len(items), "items": items[offset:offset + limit]}


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


async def _collect_field_provenance(household_id: int) -> tuple:
    """家庭全部 done 提取结果 → 字段来源样本 [(pid, field, raw_value, source, doc_type, cfid)] 与文件名表。

    冲突检测(collect_field_conflicts,仅 _CROSS_CHECK_FIELDS)与可信度打分
    (attach_field_credibility,全字段)共用同一采样,只查一次 DB;
    masked/无效提取动作(skipped_masked/skipped_invalid)不采样。
    """
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
        pids = _result_person_ids(r.write_stats)
        if not pids:
            continue
        extracted = r.extracted or {}
        source = fnames.get(r.customer_file_id) or r.file_id
        # 多人模式:extracted 为 {"persons":[...]},mapped 条目自带 person_id 与 value;
        # 单人模式:值从顶层 extracted[key] 取,归因到 write_stats 顶层 person_id
        is_multi = isinstance(extracted, dict) and isinstance(extracted.get("persons"), list)
        for m in (r.mapped or []):
            field = m.get("field")
            if not field:
                continue
            if m.get("action") in ("skipped_masked", "skipped_invalid"):
                continue
            key = m.get("key")
            if is_multi:
                pid = m.get("person_id")
                if pid and m.get("value") is not None:
                    samples.append((pid, field, m["value"], source, r.doc_type, r.customer_file_id))
                continue
            if key not in extracted:
                continue
            for pid in pids:
                samples.append((pid, field, extracted.get(key), source, r.doc_type, r.customer_file_id))
    return samples, fnames


async def attach_field_conflicts(persons: list[dict], household_id: int,
                                 provenance: tuple = None) -> list[dict]:
    """给每人挂 field_conflicts:该家庭全部 done 提取结果里,同名字段多来源值不一致的明细。"""
    if provenance is None:
        provenance = await _collect_field_provenance(household_id)
    samples, _fnames = provenance
    conflicts = collect_field_conflicts(samples)
    for p in persons:
        p["field_conflicts"] = conflicts.get(p["id"], {})
    return persons


async def attach_field_credibility(persons: list[dict], household_id: int,
                                   provenance: tuple = None) -> list[dict]:
    """给每人 fields[] 挂 credibility(读时打分:来源层/确认状态/多文件互证/冲突扣分)。

    provenance 可复用 _collect_field_provenance 结果(与 attach_field_conflicts 共用一次采样)。
    """
    import credibility as _cred
    if provenance is None:
        provenance = await _collect_field_provenance(household_id)
    samples, _fnames = provenance
    by_key: dict[tuple, list] = {}
    for pid, field, raw_value, source, doc_type, cfid in samples:
        by_key.setdefault((pid, field), []).append(
            {"value": raw_value, "source": source, "doc_type": doc_type,
             "customer_file_id": cfid})
    for p in persons:
        for f in p.get("fields") or []:
            f["credibility"] = _cred.compute_field_credibility(
                layer=f.get("layer"), status=f.get("status"),
                current_value=f.get("value"), field=f.get("field"),
                samples=by_key.get((p["id"], f.get("field")), []))
    return persons


def _normalize_name_en(value) -> str:
    """拼音名归一化:大写 + 按非字母切词 + 词序无关(NI ZHAOHUI == ZHAOHUI NI)。"""
    if not value:
        return ""
    import re
    tokens = re.findall(r"[A-Za-z]+", str(value).upper())
    return " ".join(sorted(tokens))


_opencc_t2s = None  # 懒加载单例(OpenCC 构造要读词典,避免每次新建)


def _fold_cjk(value) -> str:
    """繁→简折叠 + 去全部空白与间隔号(简体输入原样通过;非 CJK 字符不变)。

    去间隔号: 阿不都·外力 == 阿不都外力(新疆/少数民族姓名 OCR 常丢间隔号)。
    """
    global _opencc_t2s
    if not value:
        return ""
    if _opencc_t2s is None:
        from opencc import OpenCC
        _opencc_t2s = OpenCC("t2s")
    return re.sub(r"[\s·•・‧∙]+", "", _opencc_t2s.convert(str(value)))


def person_name_fold(value) -> str:
    """建卡去重键: CJK 名=_fold_cjk(繁→简+去空白/间隔号); 拉丁名=_normalize_name_en(大写词序无关)。

    两字母表不相交,不会中英误撞;空返回 ""。中英互中仍是 find_person_match 拼音路的职责,
    本函数只产出"完全相同"兜底键(name_folded 列)。
    """
    folded = _fold_cjk(value)
    if not folded:
        return ""
    if re.search(r"[一-鿿]", folded):
        return folded
    return _normalize_name_en(value)


def _normalize_id_number(value) -> str:
    """证件号归一化:去非字母数字 + 大写(容忍 OCR 空格/小写 x)。"""
    if not value:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()


def _pinyin_glued_variants(value) -> set:
    """名 → 拼音连写变体集(全大写无分隔,含姓前/姓后两序),用于中文名 ↔ 英文证件名互比。

    连写而非按词比较的原因:证件拼音常把名字音节粘连(ZHAOHUI),与按字切词不可逆;
    连写后两序变体覆盖 倪朝晖 = NIZHAOHUI = ZHAOHUINI。多音字取 pypinyin 默认音(已知限制)。
    """
    if not value:
        return set()
    folded = _fold_cjk(value)
    if not re.search(r"[一-鿿]", folded):
        norm = re.sub(r"[^A-Za-z]", "", str(value)).upper()
        return {norm} if norm else set()
    from pypinyin import Style, pinyin
    tokens = [t.upper() for (t,) in pinyin(folded, style=Style.NORMAL)
              if re.fullmatch(r"[A-Za-z]+", t)]
    if not tokens:
        return set()
    variants = {"".join(tokens)}
    if len(tokens) >= 2:
        variants.add("".join(tokens[1:] + tokens[:1]))
    return variants


def _result_person_ids(write_stats: Optional[dict]) -> list:
    """write_stats → 归因 person_id 列表(兼容多人模式 persons 明细与单人顶层 person_id)。"""
    ws = write_stats or {}
    ids = [p.get("person_id") for p in ws.get("persons") or [] if p.get("person_id")]
    if not ids and ws.get("person_id"):
        ids = [ws["person_id"]]
    return ids


async def _find_person_by_fold(session, household_id: int, folded: str):
    """会话内按 (household_id, name_folded) 查已有人(id 最小者);folded 空返回 None。"""
    if not folded:
        return None
    return (await session.execute(
        select(ProfilePerson)
        .where(ProfilePerson.household_id == household_id,
               ProfilePerson.name_folded == folded)
        .order_by(ProfilePerson.id))).scalars().first()


async def _create_person_in_session(session, household_id: int, name: str,
                                    relation: str = "待确认", is_main: bool = False):
    """会话内建人(折叠键 upsert): name_folded 命中已有卡直接返回不新建;
    唯一索引(024)兜底并发 —— IntegrityError 时重查返回并发胜出者。

    返回 (person, created, matched_by);matched_by="name_folded" 表示折叠键命中。
    """
    folded = person_name_fold(name)
    existing = await _find_person_by_fold(session, household_id, folded)
    if existing is not None:
        return existing, False, "name_folded"
    p = ProfilePerson(household_id=household_id, name=name, relation_to_main=relation,
                      is_main=is_main, name_folded=folded or None,
                      created_at=datetime.now(), updated_at=datetime.now())
    try:
        async with session.begin_nested():
            session.add(p)
            await session.flush()
    except IntegrityError:
        session.expunge(p)  # 失败 INSERT 的 pending 对象必须移除,否则后续 autoflush 重放毒化事务
        winner = await _find_person_by_fold(session, household_id, folded)
        if winner is not None:
            return winner, False, "name_folded"
        raise
    return p, True, None


async def find_person_match(household_id: int, id_number: Optional[str] = None,
                            name: Optional[str] = None,
                            name_en: Optional[str] = None) -> dict:
    """家庭内归因(去重口径:简体/繁体/拼音 同一人不重复建卡)。

    顺序:证件号(归一化,容忍空格/大小写)→ 姓名(繁→简折叠)→ name_en(词序无关)
    → 拼音互转(matched_by="pinyin",连写两序变体;英文证件命中中文卡/中文名命中英文卡)。
    """
    async with async_session_maker() as session:
        # 1) 证件号(归一化比较)
        norm_id = _normalize_id_number(id_number)
        if norm_id:
            rows = (await session.execute(
                select(ProfilePersonField)
                .join(ProfilePerson, ProfilePerson.id == ProfilePersonField.person_id)
                .where(ProfilePerson.household_id == household_id,
                       ProfilePersonField.field == "id_number")
            )).scalars().all()
            for f in rows:
                if _normalize_id_number(f.value) == norm_id:
                    return {"person_id": f.person_id, "matched_by": "id_number"}
        # 2) 姓名(繁→简折叠)
        folded = _fold_cjk(name)
        norm_en = _normalize_name_en(name_en)
        persons = []
        if folded or norm_en:
            persons = (await session.execute(
                select(ProfilePerson).where(ProfilePerson.household_id == household_id)
                .order_by(ProfilePerson.id))).scalars().all()
        if folded:
            for p in persons:
                if _fold_cjk(p.name) == folded:
                    return {"person_id": p.id, "matched_by": "name"}
        # 3) name_en 词序无关
        en_rows = []
        if norm_en or folded:
            en_rows = (await session.execute(
                select(ProfilePersonField)
                .join(ProfilePerson, ProfilePerson.id == ProfilePersonField.person_id)
                .where(ProfilePerson.household_id == household_id,
                       ProfilePersonField.field == "name_en")
            )).scalars().all()
        if norm_en:
            for f in en_rows:
                if _normalize_name_en(f.value) == norm_en:
                    return {"person_id": f.person_id, "matched_by": "name_en"}
        # 4) 拼音互转:英文输入 ↔ 中文名连写拼音;中文输入 ↔ 已存 name_en 连写形式
        glued_in = re.sub(r"[^A-Za-z]", "", str(name_en or "")).upper()
        name_variants = _pinyin_glued_variants(name) if folded else set()
        if glued_in or name_variants:
            for p in persons:
                pv = _pinyin_glued_variants(p.name)
                if (glued_in and glued_in in pv) or (name_variants and pv & name_variants):
                    return {"person_id": p.id, "matched_by": "pinyin"}
            for f in en_rows:
                fv = _pinyin_glued_variants(f.value)
                if (glued_in and glued_in in fv) or (name_variants and fv & name_variants):
                    return {"person_id": f.person_id, "matched_by": "pinyin"}
    return {"person_id": None, "matched_by": None}


async def create_person(household_id: int, name: str, relation: str = "待确认") -> dict:
    """新建成员;先按去重口径(繁简/间隔号/拼音 + name_folded 折叠键)查重,命中直接返回已有人(deduped=True)不重复建卡。"""
    name = (name or "").strip()
    is_latin = bool(re.fullmatch(r"[A-Za-z .'\-]+", name))
    match = await find_person_match(household_id, name=None if is_latin else name,
                                    name_en=name if is_latin else None)
    if match.get("person_id"):
        async with async_session_maker() as session:
            p = await session.get(ProfilePerson, match["person_id"])
            d = _to_person_dict(p)
            d["deduped"] = True
            d["matched_by"] = match.get("matched_by")
            return d
    async with async_session_maker() as session:
        p, created, matched_by = await _create_person_in_session(
            session, household_id, name, relation=relation)
        await session.commit()
        await session.refresh(p)
        d = _to_person_dict(p)
        if not created:
            d["deduped"] = True
            d["matched_by"] = matched_by
        return d


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

    match: {"person_id": int|None, "matched_by": ...};person_id 为 None 且有 name → 新建 person;
    无中文名但有合法 name_en(≥2 词拉丁名)→ 按 name_en 建卡(英文证件场景,如新加坡 DP 卡)。
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
            if not name:
                # 英文证件(批复/准证/护照)无中文名:按 name_en 建卡,人名卡先用拉丁名,
                # 后续中文证件到达时经 find_person_match 拼音路归并到同一张卡
                name_en = next((it["value"] for it in field_items
                                if it.get("column") == "name_en" and it.get("value")), None)
                if name_en and not is_masked(name_en) and plausible_latin_name(name_en):
                    name = " ".join(str(name_en).split())  # 规整连续空格
            if name:
                p, created, fold_matched_by = await _create_person_in_session(
                    session, household_id, name)
                person_id = p.id
                if created:
                    stats["person_created"] = 1
                else:
                    stats["matched_by"] = fold_matched_by  # name_folded 折叠键命中已有卡
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
                # 户口本户主 ≠ 画像主申请人:非主申请人的"户主"卡不落,避免多个"户主"
                if rel == "户主" and person is not None and not person.is_main:
                    mapped.append({**entry, "action": "skipped_filled"})
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
        # 姓名特殊通道:同步名片 profile_persons.name + name_folded(人员卡标题/头像/合并分组都读骨架名);
        # 折叠键撞上家庭内另一人时拒绝(会绕过建卡去重口径造出双卡),提示走人员合并
        if field == "name":
            person = await session.get(ProfilePerson, person_id)
            if person is not None and clean != person.name:
                folded = person_name_fold(clean)
                conflict = await _find_person_by_fold(session, person.household_id, folded)
                if conflict is not None and conflict.id != person_id:
                    raise ValueError(f"姓名「{clean}」与家庭内另一成员「{conflict.name}」重复,请改用人员合并")
                person.name = clean
                person.name_folded = folded or None
                person.updated_at = datetime.now()
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


def _parse_project_time(s: Optional[str]) -> Optional[datetime]:
    """接口项目 create_time('2026-07-27 18:22:56')-> datetime;解析失败返回 None(不杀导入)。"""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return None


async def upsert_project_cases(household_id: int, projects: list) -> dict:
    """导入时为本批全部项目建/联案件壳(一个售后项目=一个案件)。

    projects: parse_api_manifest 的 projects[](affter_entryoid 非空才处理)。
    新建: case_type=二级项目名||一级项目名||占位符, status=进行中, milestones=[];
    已存在: 项目 6 列只补空(里程碑/状态不碰)。返回 {created, updated}。
    """
    created = 0
    updated = 0
    async with async_session_maker() as session:
        for p in projects or []:
            entryoid = (p.get("affter_entryoid") or "").strip()
            if not entryoid:
                continue
            case = await session.scalar(
                select(ProfileCase)
                .where(ProfileCase.household_id == household_id,
                       ProfileCase.affter_entryoid == entryoid)
                .order_by(ProfileCase.id).limit(1))
            vals = {
                "projectno": (p.get("projectno") or "").strip() or None,
                "projectname": (p.get("projectname") or "").strip() or None,
                "projectno_detailed": (p.get("projectno_detailed") or "").strip() or None,
                "projectname_detailed": (p.get("projectname_detailed") or "").strip() or None,
                "project_created_at": _parse_project_time(p.get("project_create_time")),
            }
            if case is None:
                case = ProfileCase(
                    household_id=household_id,
                    case_type=vals["projectname_detailed"] or vals["projectname"] or CASE_PLACEHOLDER_TYPE,
                    status="进行中", milestones=[],
                    affter_entryoid=entryoid, **vals,
                    created_at=datetime.now(), updated_at=datetime.now())
                session.add(case)
                created += 1
            else:
                dirty = False
                if case.case_type == CASE_PLACEHOLDER_TYPE and (vals["projectname_detailed"] or vals["projectname"]):
                    case.case_type = vals["projectname_detailed"] or vals["projectname"]
                    dirty = True
                for k, v in vals.items():
                    if v is not None and getattr(case, k) is None:
                        setattr(case, k, v)
                        dirty = True
                if dirty:
                    case.updated_at = datetime.now()
                    updated += 1
        await session.commit()
    return {"created": created, "updated": updated}


async def apply_case_milestones(household_id: int, case_items: list, *,
                                source_file_id: Optional[int] = None,
                                affter_entryoid: Optional[str] = None,
                                project_name_hint: Optional[str] = None) -> dict:
    """把 entity=case 的提取字段写入 profile_cases(按项目案件,v2)。

    路由: affter_entryoid 非空 → (household_id, entryoid) 的项目案件;
    空 → 默认案件(entryoid IS NULL,承接扁平形态/旧数据)。
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

    entryoid = (affter_entryoid or "").strip() or None
    async with async_session_maker() as session:
        cond = (ProfileCase.affter_entryoid == entryoid) if entryoid else ProfileCase.affter_entryoid.is_(None)
        case = await session.scalar(
            select(ProfileCase).where(ProfileCase.household_id == household_id, cond)
            .order_by(ProfileCase.id.desc()).limit(1))
        if case is None:
            case = ProfileCase(household_id=household_id,
                               case_type=project_name_hint or case_type_hint or CASE_PLACEHOLDER_TYPE,
                               status="进行中", milestones=[],
                               affter_entryoid=entryoid,
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
                "affter_entryoid": c.affter_entryoid,
                "projectno": c.projectno,
                "projectname": c.projectname,
                "projectno_detailed": c.projectno_detailed,
                "projectname_detailed": c.projectname_detailed,
                "project_created_at": c.project_created_at.strftime("%Y-%m-%d %H:%M:%S") if c.project_created_at else None,
                "updated_at": c.updated_at.strftime("%Y-%m-%d %H:%M:%S") if c.updated_at else "",
            })
        return out


# ==================== 家庭关系交叉推导(自动写,不建人) ====================

# 只写 relation_to_main='待确认' 的人(走 _relation 通道),人工已确认的关系永不回改;
# 只匹配已有 person,绝不因推导建人。幂等:二次跑全部被 skipped_filled 挡掉。

_CHILD_MIN_AGE_GAP_DAYS = 15 * 365  # 启发式:户主至少年长 15 岁才推 子/女


async def _list_household_extract_results(household_id: int, doc_types: tuple) -> list:
    """按家庭查 done 提取结果(走 import_tasks.household_id,同 attach_field_conflicts 路径)。"""
    async with async_session_maker() as session:
        task_ids = (await session.execute(
            select(ProfileImportTask.id).where(ProfileImportTask.household_id == household_id)
        )).scalars().all()
        if not task_ids:
            return []
        return list((await session.execute(
            select(DocExtractResult).where(
                DocExtractResult.import_task_id.in_(task_ids),
                DocExtractResult.status == "done",
                DocExtractResult.doc_type.in_(doc_types))
            .order_by(DocExtractResult.id)
        )).scalars().all())


async def _match_person_safe(household_id: int, id_number, name) -> dict:
    """find_person_match 的安全封装:masked/假名/非法证件号先剔除,不命中不建人。"""
    id_number = (id_number or "").strip() or None
    name = (name or "").strip() or None
    if id_number and (is_masked(id_number) or not valid_id_number(id_number)):
        id_number = None
    if name and (is_masked(name) or not plausible_person_name(name)):
        name = None
    if not id_number and not name:
        return {"person_id": None, "matched_by": None}
    return await find_person_match(household_id, id_number, name)


async def _write_relation(household_id: int, person_id: int, relation: str, *,
                          basis: str, source_result_id, inferred: list) -> None:
    """走 _relation 通道写关系(仅'待确认'时落地);真写入时追加推导记录。"""
    write = await apply_extracted_fields_v2(
        household_id, {"person_id": person_id, "matched_by": "inferred"},
        [{"key": "relation", "value": relation, "column": _RELATION_FIELD}])
    action = next((m.get("action") for m in write["mapped"]
                   if m.get("field") == _RELATION_FIELD), None)
    if action == "relation_written":
        inferred.append({"person_id": person_id, "relation": relation,
                         "basis": basis, "source_result_id": source_result_id})


async def _infer_from_birth_cert(household_id: int, main_id: int, ex: dict,
                                 result_id, inferred: list) -> None:
    """出生证:父/母一方命中户主本人,另一方命中家庭已有 person → 另一方写'配偶'。"""
    father = await _match_person_safe(household_id,
                                      ex.get("father_id_number"), ex.get("father_name"))
    mother = await _match_person_safe(household_id,
                                      ex.get("mother_id_number"), ex.get("mother_name"))
    for hit, other, tag in ((father, mother, "father_is_main"),
                            (mother, father, "mother_is_main")):
        if hit.get("person_id") != main_id:
            continue  # 命中非户主或父母都未命中 → 无法确定与户主关系,不写
        opid = other.get("person_id")
        if not opid or opid == main_id:
            continue  # 另一方未建档 → 不建人,跳过
        await _write_relation(household_id, opid, "配偶",
                              basis=f"birth_cert:{tag}", source_result_id=result_id,
                              inferred=inferred)


async def _infer_from_marriage_cert(household_id: int, main_id: int, holder_pid,
                                    ex: dict, result_id, inferred: list) -> None:
    """结婚证:持证人与配偶一方为户主,另一方已建档且待确认 → 写'配偶'。

    双格式兼容:
    - 多人模式(rule v2 起): extracted={"persons":[{cert_role,name,id_number,...},...]},
      按 cert_role 定位持证人/配偶,配偶带身份证号 → 证件号优先匹配(强归因);
    - 旧单人模式(rule v1): extracted 顶层 spouse_name,仅按名匹配(历史数据)。
    holder_pid 取 write_stats.person_id(多人模式=persons[0],prompt 约定=持证人;
    cert_role 显示 persons[0] 非持证人时按持证人对象现查兜底)。
    """
    persons = ex.get("persons") if isinstance(ex, dict) else None
    if isinstance(persons, list) and persons:
        holder_ex = next((p for p in persons if (p.get("cert_role") or "") == "持证人"),
                         persons[0])
        spouse_ex = next((p for p in persons if p is not holder_ex), None) or {}
        spouse = await _match_person_safe(household_id, spouse_ex.get("id_number"),
                                          spouse_ex.get("name"))
        if holder_ex is not persons[0]:
            holder = await _match_person_safe(household_id, holder_ex.get("id_number"),
                                              holder_ex.get("name"))
            holder_pid = holder.get("person_id") or holder_pid
    else:
        spouse = await _match_person_safe(household_id, None, ex.get("spouse_name"))
    spid = spouse.get("person_id")
    pairs = []
    if holder_pid == main_id and spid and spid != main_id:
        pairs.append((spid, "holder_is_main"))
    if spid == main_id and holder_pid and holder_pid != main_id:
        pairs.append((holder_pid, "spouse_is_main"))
    for pid, tag in pairs:
        await _write_relation(household_id, pid, "配偶",
                              basis=f"marriage_cert:{tag}", source_result_id=result_id,
                              inferred=inferred)


async def _infer_children_heuristic(household_id: int, main_id: int,
                                    inferred: list) -> None:
    """启发式:同姓 + 户主年长>15岁 + (双方都有户籍地址时一致) → 按性别写 子/女。"""
    persons = await list_persons(household_id)
    main = next((p for p in persons if p["id"] == main_id), None)
    if not main:
        return
    fmain = {f["field"]: f.get("value") for f in main.get("fields") or []}
    main_birth = _parse_date(fmain.get("birth_date"))
    main_addr = re.sub(r"\s+", "", fmain.get("hukou_address") or "")
    main_name = (main.get("name") or "").strip()
    if not main_birth or not main_name:
        return
    for p in persons:
        if p["id"] == main_id or p.get("relation_to_main") != "待确认":
            continue
        name = (p.get("name") or "").strip()
        if not name or name[0] != main_name[0]:
            continue  # 不同姓
        fp = {f["field"]: f.get("value") for f in p.get("fields") or []}
        p_birth = _parse_date(fp.get("birth_date"))
        if not p_birth or (p_birth - main_birth).days <= _CHILD_MIN_AGE_GAP_DAYS:
            continue  # 缺生日或户主年长不足 15 岁
        p_addr = re.sub(r"\s+", "", fp.get("hukou_address") or "")
        if main_addr and p_addr and p_addr != main_addr:
            continue  # 双方都有户籍地址但不一致
        g = (fp.get("gender") or "").strip()
        gnorm = _GENDER_MAP.get(g) or _GENDER_MAP.get(g.lower())
        rel = {"M": "子", "F": "女"}.get(gnorm)
        if not rel:
            continue  # 性别未知不推
        await _write_relation(
            household_id, p["id"], rel,
            basis=f"heuristic:surname+age_gap+{'addr' if p_addr and main_addr else 'no_addr'}",
            source_result_id=None, inferred=inferred)


async def infer_family_relations(household_id: int) -> dict:
    """家庭关系交叉推导:出生证父母/结婚证配偶/同姓年长差启发式。

    幂等(_relation 通道只在'待确认'时写);只匹配已有 person,绝不建人。
    返回 {"checked_results": n, "inferred": [{person_id, relation, basis, source_result_id}]}
    """
    inferred: list[dict] = []
    household = await get_household(household_id)
    if not household or not household.get("main_person_id"):
        return {"checked_results": 0, "inferred": []}
    main_id = household["main_person_id"]

    results = await _list_household_extract_results(
        household_id, ("birth_cert", "marriage_cert"))
    for r in results:
        ex = r.extracted or {}
        if r.doc_type == "birth_cert":
            await _infer_from_birth_cert(household_id, main_id, ex, r.id, inferred)
        elif r.doc_type == "marriage_cert":
            holder_pid = (r.write_stats or {}).get("person_id")
            await _infer_from_marriage_cert(household_id, main_id, holder_pid,
                                            ex, r.id, inferred)

    # 证件明示关系(DP/LTVP 主签持证人)先于纯启发式:证据强度更高
    await _infer_from_sponsor(household_id, main_id, inferred)
    # C1/C2 先于 C3:配偶落定后,启发式只处理剩余待确认
    await _infer_children_heuristic(household_id, main_id, inferred)
    return {"checked_results": len(results), "inferred": inferred}


async def _infer_from_sponsor(household_id: int, main_id: int,
                              inferred: list) -> None:
    """家属准证主签持证人(DP/LTVP 卡面 MAIN PASS HOLDER)→ 持证人=户主且年龄差>15岁 → 按性别写 子/女。

    sponsor_name 字段由 approval 规则 v3 起随提取落库(verified 层,带 source_result_id 证据链)。
    年龄差不足/性别未知时不写(DP 本身区分不了配偶与子女,不猜);只处理'待确认'的人。
    """
    persons = await list_persons(household_id)
    main = next((p for p in persons if p["id"] == main_id), None)
    if not main:
        return
    fmain = {f["field"]: f.get("value") for f in main.get("fields") or []}
    main_birth = _parse_date(fmain.get("birth_date"))
    if not main_birth:
        return
    for p in persons:
        if p["id"] == main_id or p.get("relation_to_main") != "待确认":
            continue
        sponsor_f = next((f for f in p.get("fields") or []
                          if f["field"] == "sponsor_name" and f.get("value")), None)
        if not sponsor_f:
            continue
        sponsor = sponsor_f["value"].strip()
        if is_masked(sponsor):
            continue
        # sponsor 拉丁名走 name_en 通道,中文名走 name 通道(折叠/拼音互转在 find_person_match 内)
        if re.search(r"[一-鿿]", sponsor):
            match = await find_person_match(household_id, None, sponsor, None)
        else:
            match = await find_person_match(household_id, None, None, sponsor)
        if match.get("person_id") != main_id:
            continue  # 主签持证人不是本家庭户主 → 表达不了与户主关系,跳过
        fp = {f["field"]: f.get("value") for f in p.get("fields") or []}
        p_birth = _parse_date(fp.get("birth_date"))
        if not p_birth or (p_birth - main_birth).days <= _CHILD_MIN_AGE_GAP_DAYS:
            continue  # 户主年长不足 15 岁(可能是配偶/同龄亲属)→ 不猜
        g = (fp.get("gender") or "").strip()
        gnorm = _GENDER_MAP.get(g) or _GENDER_MAP.get(g.lower())
        rel = {"M": "子", "F": "女"}.get(gnorm)
        if not rel:
            continue
        await _write_relation(household_id, p["id"], rel,
                              basis="sponsor:main_pass_holder",
                              source_result_id=sponsor_f.get("source_result_id"),
                              inferred=inferred)


# ==================== 同名人员合并 ====================

async def find_duplicate_person_groups(household_id: int) -> list[dict]:
    """按 person_name_fold 分组找同名重复组(>1 人成组)。

    keep 选择: is_main 优先 > 人工字段数多者 > id 小者。
    守卫: 组内 ≥2 人各持归一化后互不相同的合法 id_number → skipped_reason='conflict_id_number'
    (防同名父子真两人;gender 冲突不阻塞——间隔号/OCR 微差异双卡正是本场景)。
    返回 [{folded, person_ids, keep_id, drop_ids, skipped_reason?}]
    """
    async with async_session_maker() as session:
        persons = (await session.execute(
            select(ProfilePerson).where(ProfilePerson.household_id == household_id)
            .order_by(ProfilePerson.id))).scalars().all()
        groups: dict[str, list] = {}
        for p in persons:
            folded = person_name_fold(p.name)
            if folded:
                groups.setdefault(folded, []).append(p)

        out: list[dict] = []
        for folded, members in groups.items():
            if len(members) < 2:
                continue
            ids = [p.id for p in members]
            fields = (await session.execute(
                select(ProfilePersonField).where(ProfilePersonField.person_id.in_(ids))
            )).scalars().all()
            human_cnt = {pid: 0 for pid in ids}
            id_numbers: dict[int, set] = {}
            for f in fields:
                if f.status in _HUMAN_STATUSES:
                    human_cnt[f.person_id] = human_cnt.get(f.person_id, 0) + 1
                if f.field == "id_number" and f.value and valid_id_number(f.value):
                    norm = _normalize_id_number(f.value)
                    if norm:
                        id_numbers.setdefault(f.person_id, set()).add(norm)
            entry = {
                "folded": folded,
                "person_ids": ids,
                "keep_id": sorted(members, key=lambda p: (
                    not p.is_main, -human_cnt.get(p.id, 0), p.id))[0].id,
            }
            entry["drop_ids"] = [pid for pid in ids if pid != entry["keep_id"]]
            distinct_values = {v for s in id_numbers.values() for v in s}
            holders = {pid for pid, s in id_numbers.items() if s}
            if len(distinct_values) >= 2 and len(holders) >= 2:
                entry["skipped_reason"] = "conflict_id_number"
            out.append(entry)
        return out


async def merge_persons(household_id: int, keep_id: int, drop_id: int, *,
                        keep_name: Optional[str] = None) -> dict:
    """把 drop 人并入 keep 人(单 session 单事务)。

    字段仲裁((person_id, field) 唯一约束下逐 field):
      keep 无该 field → drop 行直接迁(证据链原样);人工(confirmed/corrected)永远胜 AI;
      双人工 keep 胜(主卡人工值=用户最后意志);双 AI → updated_at 晚者胜(后续覆盖前面,相等 keep 胜)。
      drop 胜时值/层/来源/status/updated_by/updated_at 整体拷到 keep 行(保留 keep 行 id)。
    败方字段快照进返回 fields_lost(事件留痕,可恢复性唯一凭据)。
    person.name 缺省恒保持 keep 原值;keep_name 显式指定时(手动合并让用户选保留哪个人名)
      在 drop 删除 flush 后改名 + 重算 name_folded,撞家庭内第三人折叠键报 ValueError。
    relation/avatar 只补空;drop.is_main 交接给 keep;
    customer_files.person_id / profile_assets.owner_person_id / doc_extract_results
    write_stats(顶层+persons[]+mapped[]) 先重挂再删人(assets FK 是 SET NULL)。
    """
    if keep_id == drop_id:
        raise ValueError("keep_id 与 drop_id 不能相同")
    async with async_session_maker() as session:
        keep = await session.get(ProfilePerson, keep_id)
        drop = await session.get(ProfilePerson, drop_id)
        if not keep or not drop:
            raise ValueError("人员不存在")
        if keep.household_id != household_id or drop.household_id != household_id:
            raise ValueError("人员不属于目标家庭")
        household = await session.get(ProfileHousehold, household_id)

        stats = {"keep_id": keep_id, "drop_id": drop_id, "fields_moved": 0,
                 "fields_arbitrated": 0, "fields_lost": [], "files_repointed": 0,
                 "assets_repointed": 0, "results_rewritten": 0}

        # 1) 骨架交接
        if drop.is_main:
            keep.is_main = True
            keep.relation_to_main = "户主"
        if household and household.main_person_id == drop_id:
            household.main_person_id = keep_id
        if keep.relation_to_main in (None, "", "待确认") and \
                drop.relation_to_main not in (None, "", "待确认"):
            keep.relation_to_main = drop.relation_to_main
        if not keep.avatar_file_id and drop.avatar_file_id:
            keep.avatar_file_id = drop.avatar_file_id

        # 2) 字段仲裁
        keep_fields = {f.field: f for f in (await session.execute(
            select(ProfilePersonField).where(ProfilePersonField.person_id == keep_id)
        )).scalars().all()}
        drop_fields = (await session.execute(
            select(ProfilePersonField).where(ProfilePersonField.person_id == drop_id)
        )).scalars().all()

        def _snapshot(f) -> dict:
            return {"field": f.field, "value": f.value, "status": f.status,
                    "layer": f.layer, "source_file_id": f.source_file_id,
                    "source_result_id": f.source_result_id, "updated_by": f.updated_by}

        for df in drop_fields:
            kf = keep_fields.get(df.field)
            if kf is None:
                df.person_id = keep_id
                keep_fields[df.field] = df
                stats["fields_moved"] += 1
                continue
            kf_human = kf.status in _HUMAN_STATUSES
            df_human = df.status in _HUMAN_STATUSES
            drop_wins = (df_human and not kf_human) or (
                not df_human and not kf_human and
                (df.updated_at or datetime.min) > (kf.updated_at or datetime.min))
            stats["fields_arbitrated"] += 1
            if drop_wins:
                stats["fields_lost"].append(_snapshot(kf))
                kf.value = df.value
                kf.layer = df.layer
                kf.source_file_id = df.source_file_id
                kf.source_result_id = df.source_result_id
                kf.status = df.status
                kf.updated_by = df.updated_by
                kf.updated_at = df.updated_at or datetime.now()
            else:
                stats["fields_lost"].append(_snapshot(df))
            await session.delete(df)

        # 3) 重挂(先重挂再删人)
        stats["files_repointed"] = (await session.execute(
            update(CustomerFile).where(CustomerFile.person_id == drop_id)
            .values(person_id=keep_id))).rowcount
        stats["assets_repointed"] = (await session.execute(
            update(ProfileAsset).where(ProfileAsset.owner_person_id == drop_id)
            .values(owner_person_id=keep_id))).rowcount

        # 4) write_stats/mapped 归因回写(查询条件同 list_person_files 三路并集中的后两路;
        #    mapped[].person_id 必与 write_stats 顶层/persons[] 同步出现,两条件足够覆盖)
        task_ids = (await session.execute(
            select(ProfileImportTask.id).where(ProfileImportTask.household_id == household_id)
        )).scalars().all()
        if task_ids:
            results = (await session.execute(
                select(DocExtractResult).where(
                    DocExtractResult.import_task_id.in_(task_ids),
                    or_(
                        DocExtractResult.write_stats["person_id"].as_string() == str(drop_id),
                        DocExtractResult.write_stats.contains({"persons": [{"person_id": drop_id}]}),
                    ))
            )).scalars().all()
            for r in results:
                changed = False
                ws = dict(r.write_stats or {})
                if ws.get("person_id") == drop_id:
                    ws["person_id"] = keep_id
                    changed = True
                if isinstance(ws.get("persons"), list):
                    persons_list = []
                    for p in ws["persons"]:
                        if isinstance(p, dict) and p.get("person_id") == drop_id:
                            p = {**p, "person_id": keep_id}
                            changed = True
                        persons_list.append(p)
                    ws["persons"] = persons_list
                if changed:
                    r.write_stats = ws
                if isinstance(r.mapped, list):
                    mapped_list = []
                    mapped_changed = False
                    for m in r.mapped:
                        if isinstance(m, dict) and m.get("person_id") == drop_id:
                            m = {**m, "person_id": keep_id}
                            mapped_changed = True
                        mapped_list.append(m)
                    if mapped_changed:
                        r.mapped = mapped_list
                        changed = True
                if changed:
                    stats["results_rewritten"] += 1

        # 5) 删 drop(person_fields 残余行靠 DB CASCADE;正常已空)
        keep.updated_at = datetime.now()
        await session.delete(drop)
        await session.flush()
        # 6) 可选改名:手动合并用户选了保留 drop 的名字。drop 行已 flush 删除,
        #    不会撞 (household_id, name_folded) 唯一索引;撞家庭内第三人则拒绝
        clean_name = (keep_name or "").strip()
        if clean_name and clean_name != keep.name:
            folded = person_name_fold(clean_name)
            conflict = await _find_person_by_fold(session, household_id, folded)
            if conflict is not None and conflict.id != keep_id:
                raise ValueError(f"保留名「{clean_name}」与家庭内另一成员「{conflict.name}」重复,无法使用")
            stats["name_changed"] = {"from": keep.name, "to": clean_name}
            keep.name = clean_name
            keep.name_folded = folded or None
        await session.commit()
        return stats


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

    人档关联:① 归因(write_stats + customer_files.person_id 手动归属) ② 文件名/文件夹含人名 ③ household 列家庭共享。
    """
    from db import customer_file_crud, doc_extract_crud

    household_id = task.get("household_id")
    if not household_id:
        return {"persons": [], "columns": MATRIX_COLUMNS, "cells": {}}
    persons = await list_persons(household_id)
    files, _ = await customer_file_crud.list_task_files(task["id"], limit=1000)
    results, _ = await doc_extract_crud.list_results(import_task_id=task["id"], limit=1000)

    # ① 归因关联:person_id -> file ids(提取结果 write_stats + customer_files.person_id 手动归属)
    linked: dict[int, set] = {}
    for r in results:
        for pid in _result_person_ids(r.get("write_stats")):
            linked.setdefault(pid, set()).add(r["customer_file_id"])
    for f in files:
        if f.get("person_id"):
            linked.setdefault(f["person_id"], set()).add(f["id"])

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
