"""
客户字段字典 —— 系统认识的客户字段单一来源。

v1 硬编码在文件里。v2 计划做管理员维护页（DB 化）。

每个字段定义：
    key          : 系统内部 key（唯一，anchor.field_hint 用这个）
    display      : 中文展示名（前端下拉显示）
    aliases      : 别名列表（LLM prompt 里给候选；description 文本匹配时也用）
    source       : 数据来源：
                     "person.{field}"        画像人员字段 profile_person_fields.field;
                                             多候选用 "|" 连接,依次取第一个非空
                     "computed.{name}"       计算字段(如 today)
                     "none"                  画像无对应数据源,恒 unmatched
    dtype        : 数据类型："str" | "date" | "currency"
    fmt          : 默认 format（喂给 anchor.format_value）

20 项字段，覆盖常见 POA / 证件类业务场景。

2026-08 数据源迁移:旧 clients/client_info 客户档案体系已删除,填值数据源改为
客户画像 v2 的 profile_person_fields(db/profile_crud.PROFILE_FIELDS 的 field 键)。
原 client_info 的「签发机关/紧急联系人/签字地点/金额」与主表「顾问/备注」
画像无对应字段,标记 none(恒 unmatched,模板里手填)。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDef:
    key: str
    display: str
    aliases: tuple[str, ...]
    source: str   # "person.name" | "person.passport_expiry_date|id_card_expiry_date" | "computed.today" | "none"
    dtype: str    # "str" | "date" | "currency"
    fmt: str | None  # 默认 format，可被 anchor.format 覆盖


FIELD_DICTIONARY: list[FieldDef] = [
    FieldDef("name",              "姓名",         ("姓名", "Name", "名字", "申请人"),                       "person.name",            "str",  None),
    FieldDef("id_number",         "证件号",       ("证件号", "身份证号", "护照号", "ID Number", "Passport No"), "person.id_number",       "str",  None),
    FieldDef("gender",            "性别",         ("性别", "Sex", "Gender"),                               "person.gender",          "str",  None),
    FieldDef("birth_date",        "出生日期",     ("出生日期", "Date of Birth", "DOB"),                     "person.birth_date",      "date", "YYYY-MM-DD"),
    FieldDef("nationality",       "国籍",         ("国籍", "Nationality"),                                 "person.nationality",     "str",  None),
    FieldDef("consultant",        "顾问",         ("顾问", "客户经理"),                                     "none",                   "str",  None),
    FieldDef("issuing_authority", "签发机关",     ("签发机关", "发证机关"),                                 "none",                   "str",  None),
    FieldDef("issue_date",        "签发日期",     ("签发日期", "Issue Date"),                               "person.passport_issue_date", "date", "YYYY-MM-DD"),
    FieldDef("expiry_date",       "有效期至",     ("有效期", "Expiry Date", "有效期限"),                     "person.passport_expiry_date|id_card_expiry_date", "date", "YYYY-MM-DD"),
    FieldDef("address",           "地址",         ("地址", "住址", "Address"),                              "person.current_address|hukou_address", "str",  None),
    FieldDef("phone",             "电话",         ("电话", "手机", "联系方式", "Phone"),                    "person.phone",           "str",  None),
    FieldDef("email",             "邮箱",         ("邮箱", "Email", "电子邮件"),                            "person.email",           "str",  None),
    FieldDef("occupation",        "职业",         ("职业", "Occupation"),                                   "person.occupation",      "str",  None),
    FieldDef("employer",          "工作单位",     ("工作单位", "Employer", "雇主"),                          "person.employer",        "str",  None),
    FieldDef("marital_status",    "婚姻状况",     ("婚姻状况", "Marital Status"),                            "person.marital_status",  "str",  None),
    FieldDef("emergency_contact", "紧急联系人",   ("紧急联系人", "Emergency Contact"),                       "none",                   "str",  None),
    FieldDef("today",             "今日日期",     ("今日", "今天", "签字日期", "Date"),                      "computed.today",         "date", "YYYY-MM-DD"),
    FieldDef("signature_place",   "签字地点",     ("签字地点", "地点"),                                      "none",                   "str",  None),
    FieldDef("amount",            "金额",         ("金额", "费用", "Amount"),                                "none",                   "str",  None),
    FieldDef("notes",             "备注",         ("备注", "Notes", "说明"),                                 "none",                   "str",  None),
]


# 按 key 索引（高频查找）
_BY_KEY: dict[str, FieldDef] = {fd.key: fd for fd in FIELD_DICTIONARY}


def get_field(key: str) -> FieldDef | None:
    """按 key 取 FieldDef，未找到返回 None。"""
    return _BY_KEY.get(key)


def find_field_by_text(text: str) -> FieldDef | None:
    """按 alias 文本模糊匹配 FieldDef。text 是用户写的 description/label。

    优先完全匹配（去标点），其次子串匹配。返回第一个匹配项。
    """
    if not text:
        return None
    norm = text.replace(" ", "").replace("　", "")
    # 完全匹配
    for fd in FIELD_DICTIONARY:
        all_aliases = (fd.display, *fd.aliases)
        for alias in all_aliases:
            if norm == alias.replace(" ", ""):
                return fd
    # 子串匹配（description 包含别名）
    for fd in FIELD_DICTIONARY:
        all_aliases = (fd.display, *fd.aliases)
        for alias in all_aliases:
            if alias.replace(" ", "") in norm:
                return fd
    return None


def list_for_llm_prompt() -> list[dict]:
    """生成喂给 LLM 的字段清单：[{key, display, aliases}]。"""
    out = []
    for fd in FIELD_DICTIONARY:
        out.append({
            "key": fd.key,
            "display": fd.display,
            "aliases": list(fd.aliases),
        })
    return out