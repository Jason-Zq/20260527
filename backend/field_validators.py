"""提取字段校验与自动修正(纯函数,可单测)。

身份证号 GB 11643 校验位(18 位):
  前 17 位按权重加权和 mod 11 → 校验码 '10X98765432'。
  校验不过时尝试单字符修复:逐位替换为其 OCR 形近字,候选须同时满足
  校验位通过 + 内嵌出生日期合法 + 地区码前两位合法 + 与提取的
  birth_date/gender 交叉一致(若提供)→ 唯一候选才自动改,否则记 flag。

派生(证件号合法时,含 15 位老证):补缺失的 birth_date(7-14 位)/gender(顺序码奇偶)。
日期合理性:出生日期在未来 / 签发日期晚于有效期至 / 日期无法解析 → flag。
"""
from datetime import date

# GB 11643:前 17 位权重 + 校验码映射
ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CHECK_CHARS = "10X98765432"

# 身份证前两位合法地区码(11-15/21-23/31-37/41-46/50-54/61-65/71/81/82)
REGION_PREFIXES = frozenset(
    [f"{n:02d}" for n in (
        11, 12, 13, 14, 15, 21, 22, 23, 31, 32, 33, 34, 35, 36, 37,
        41, 42, 43, 44, 45, 46, 50, 51, 52, 53, 54, 61, 62, 63, 64, 65,
        71, 81, 82)]
)

BIRTH_YEAR_MIN = 1900

# OCR 数字常见形近误识表(经验值,对称闭包):修复只换形近字,防"数学上成立但离谱"的修正
_OCR_CONFUSABLE = {
    "0": "689X", "1": "27", "2": "17", "3": "589", "4": "9",
    "5": "368", "6": "058", "7": "12", "8": "03569", "9": "0348",
    "X": "0",
}

# 需要做日期合理性检查的 person 字段(column 名)
_PERSON_DATE_COLUMNS = frozenset({
    "birth_date", "passport_issue_date", "passport_expiry_date",
    "marriage_date", "no_crime_issue_date", "approval_date",
})


def id_check_char(first17: str) -> str:
    """按 GB 11643 计算 18 位身份证校验码(输入前 17 位数字)。"""
    return ID_CHECK_CHARS[sum(int(d) * w for d, w in zip(first17, ID_WEIGHTS)) % 11]


def id_checksum_ok(s: str) -> bool:
    """18 位身份证校验位是否通过(格式不合法也返回 False)。"""
    s = (s or "").strip().upper()
    if len(s) != 18 or not s[:17].isdigit():
        return False
    if not (s[17].isdigit() or s[17] == "X"):
        return False
    return id_check_char(s[:17]) == s[17]


def _parse_ymd(s: str):
    """严格解析 YYYY-MM-DD → date;失败返回 None。"""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return None
    try:
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return None


def id_embedded_birth(s: str):
    """证件号内嵌出生日期(18 位取 7-14 位;15 位老证补 19 前缀取 7-12 位)。

    返回 'YYYY-MM-DD';日期不合法/超出 1900-今天范围返回 None。
    """
    s = (s or "").strip().upper()
    if len(s) == 18 and s[:17].isdigit():
        y, m, d = int(s[6:10]), int(s[10:12]), int(s[12:14])
    elif len(s) == 15 and s.isdigit():
        y, m, d = BIRTH_YEAR_MIN + int(s[6:8]), int(s[8:10]), int(s[10:12])
    else:
        return None
    try:
        b = date(y, m, d)
    except ValueError:
        return None
    if b.year < BIRTH_YEAR_MIN or b > date.today():
        return None
    return b.isoformat()


def id_embedded_gender(s: str):
    """证件号内嵌性别(顺序码末位奇男偶女;18 位取第 17 位,15 位取第 15 位)。"""
    s = (s or "").strip().upper()
    if len(s) == 18 and s[16].isdigit():
        return "男" if int(s[16]) % 2 == 1 else "女"
    if len(s) == 15 and s[14].isdigit():
        return "男" if int(s[14]) % 2 == 1 else "女"
    return None


def repair_id_number(id_number: str, birth_date: str = None, gender: str = None) -> dict:
    """校验位不过的 18 位证尝试单字符形近修复。

    候选 = 某一位换成其 OCR 形近字,且同时满足:校验位通过 + 内嵌出生日期合法
    + 地区码前两位合法 + 与提取的 birth_date/gender 一致(若提供且可解析)。
    返回 {"repaired": bool, "value": str|None, "candidates": int};
    唯一候选才 repaired=True,多候选/无候选不动原值(交由调用方 flag 复核)。
    """
    s = (id_number or "").strip().upper()
    if len(s) != 18 or not s[:17].isdigit() or not (s[17].isdigit() or s[17] == "X"):
        return {"repaired": False, "value": None, "candidates": 0}
    if id_checksum_ok(s):
        return {"repaired": False, "value": s, "candidates": 0}  # 本来就合法

    birth_ref = _parse_ymd(birth_date or "")
    cands = set()
    for pos in range(18):
        # 前 17 位只换 OCR 形近字;校验位(pos 17)是计算位、误识/录入错误高发,
        # 放开全字符集——让"只修校验位"始终与其他位置的修复竞争,多数决不出才不改。
        alphabet = _OCR_CONFUSABLE.get(s[pos], "") if pos < 17 else "0123456789X"
        for ch in alphabet:
            if ch == s[pos]:
                continue
            t = s[:pos] + ch + s[pos + 1:]
            if t[:2] not in REGION_PREFIXES:
                continue
            if not id_checksum_ok(t):
                continue
            b = id_embedded_birth(t)
            if b is None:
                continue
            if birth_ref and b != birth_ref.isoformat():
                continue
            g = id_embedded_gender(t)
            if gender in ("男", "女") and g != gender:
                continue
            cands.add(t)
    if len(cands) == 1:
        return {"repaired": True, "value": next(iter(cands)), "candidates": 1}
    return {"repaired": False, "value": None, "candidates": len(cands)}


def validate_field_items(field_items: list) -> tuple:
    """对单人的提取字段做校验/自动修正(原地修改 field_items)。

    返回 (field_items, repairs, flags):
      repairs: [{key, from, to, reason}] 已应用的修正(审计留痕,进 write_stats);
      flags:   [{key, reason, detail}]  未自动改的疑点(进复核队列)。
    脱敏值(占位词/星号)不修不标。
    """
    from db.doc_extract_crud import is_masked  # 延迟导入,保持模块轻依赖

    repairs: list = []
    flags: list = []

    def _item(col):
        return next((it for it in field_items if it.get("column") == col), None)

    # ---- 1) 身份证号:归一化 + 校验位修复 ----
    id_item = _item("id_number")
    if id_item is not None and not is_masked(id_item.get("value")):
        raw = str(id_item["value"])
        norm = raw.strip().upper().replace(" ", "")
        if norm != raw:
            repairs.append({"key": id_item["key"], "from": raw, "to": norm,
                            "reason": "normalize"})
            id_item["value"] = norm
        if len(norm) == 18 and not id_checksum_ok(norm):
            rep = repair_id_number(
                norm,
                birth_date=(_item("birth_date") or {}).get("value"),
                gender=(_item("gender") or {}).get("value"))
            if rep["repaired"]:
                repairs.append({"key": id_item["key"], "from": norm,
                                "to": rep["value"], "reason": "checksum_repair"})
                id_item["value"] = rep["value"]
            else:
                flags.append({
                    "key": id_item["key"], "reason": "checksum_fail",
                    "detail": (f"{rep['candidates']} 个候选,未自动改"
                               if rep["candidates"] else "无可行修复")})

    # ---- 2) 合法证件号派生缺失字段 / 交叉冲突标记 ----
    valid_id = None
    if id_item is not None and not is_masked(id_item.get("value")):
        v = str(id_item["value"])
        if (len(v) == 18 and id_checksum_ok(v)) or (len(v) == 15 and v.isdigit()):
            valid_id = v
    if valid_id:
        emb_birth = id_embedded_birth(valid_id)
        emb_gender = id_embedded_gender(valid_id)
        birth_item, gender_item = _item("birth_date"), _item("gender")
        if emb_birth:
            if birth_item is None:
                field_items.append({
                    "key": "birth_date", "label": "出生日期", "value": emb_birth,
                    "column": "birth_date", "layer": None, "entity": "person"})
                repairs.append({"key": "birth_date", "from": None, "to": emb_birth,
                                "reason": "derived_from_id"})
            else:
                bv = _parse_ymd(str(birth_item["value"]))
                if bv and bv.isoformat() != emb_birth:
                    flags.append({"key": birth_item["key"], "reason": "id_birth_mismatch",
                                  "detail": f"字段 {birth_item['value']} vs 证件号内嵌 {emb_birth}"})
        if emb_gender:
            if gender_item is None:
                field_items.append({
                    "key": "gender", "label": "性别", "value": emb_gender,
                    "column": "gender", "layer": None, "entity": "person"})
                repairs.append({"key": "gender", "from": None, "to": emb_gender,
                                "reason": "derived_from_id"})
            else:
                gv = str(gender_item["value"]).strip()
                if gv in ("男", "女") and gv != emb_gender:
                    flags.append({"key": gender_item["key"], "reason": "id_gender_mismatch",
                                  "detail": f"字段 {gv} vs 证件号内嵌 {emb_gender}"})

    # ---- 3) 日期合理性 ----
    today = date.today()
    for it in field_items:
        col = it.get("column")
        if col not in _PERSON_DATE_COLUMNS:
            continue
        dv = _parse_ymd(str(it.get("value") or ""))
        if dv is None:
            flags.append({"key": it["key"], "reason": "bad_date_format",
                          "detail": str(it.get("value"))[:40]})
        elif col == "birth_date" and dv > today:
            flags.append({"key": it["key"], "reason": "future_birth_date",
                          "detail": str(it["value"])})
    issue_item, expiry_item = _item("passport_issue_date"), _item("passport_expiry_date")
    if issue_item is not None and expiry_item is not None:
        di = _parse_ymd(str(issue_item["value"]))
        de = _parse_ymd(str(expiry_item["value"]))
        if di and de and di > de:
            flags.append({"key": expiry_item["key"], "reason": "date_order",
                          "detail": f"签发 {issue_item['value']} 晚于有效期至 {expiry_item['value']}"})

    return field_items, repairs, flags
