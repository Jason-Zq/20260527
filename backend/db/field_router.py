"""
OCR 字段路由表（数据驱动）

职责：把 OCR 抽取出的字段（中/英文键名）路由到具体的：
- 表（clients / family / assets / client_info[KV 兜底]）
- 列名（强 schema 列）

设计原则：
- 只放映射，不掺业务逻辑
- 一个 OCR 字段名（alias）只能映射到一个目标列
- 多个 alias 可以指向同一目标列（"姓名"和"Name"都映射到 name）
- 未命中映射的字段返回到 unmapped 字典，调用方决定写 client_info KV 还是丢弃
"""

from typing import Optional, Tuple

# ============== doc_type → 默认归属 ==============
# 取值：
#   "clients"                   - 写主申主表
#   ("family", relation)        - 写 family_members 表，默认 relation
#   ("assets", asset_type)      - 写 assets 表，默认 asset_type
DOC_TYPE_TO_ENTITY: dict[str, object] = {
    # 主申文件
    "身份证": "clients",
    "毕业证书": "clients",
    "毕业证": "clients",
    "学位证书": "clients",
    "学位证": "clients",
    "结婚证": "clients",
    "护照": "clients",
    "户口本": "clients",
    # 配偶文件
    "配偶身份证": ("family", "配偶"),
    "配偶毕业证书": ("family", "配偶"),
    "配偶毕业证": ("family", "配偶"),
    "配偶学位证书": ("family", "配偶"),
    "配偶学位证": ("family", "配偶"),
    "配偶护照": ("family", "配偶"),
    # 子女文件
    "出生医学证": ("family", "子"),
    "出生证": ("family", "子"),
    # 资产文件
    "房产证": ("assets", "房产"),
    "不动产权证": ("assets", "房产"),
    "存款证明": ("assets", "存款"),
    "银行流水": ("assets", "银行流水"),
    "银行对账单": ("assets", "银行流水"),
    "股票账户": ("assets", "股票"),
    "行驶证": ("assets", "车辆"),
    "车辆登记证": ("assets", "车辆"),
}


# ============== 字段名 → 目标列 ==============
# (entity, alias) → column_name
# entity ∈ {"clients", "family", "assets"}
# alias 为 OCR/LLM 输出的字段名（中文/英文/带空格变体）
FIELD_TO_COLUMN: dict[Tuple[str, str], str] = {
    # ====================== clients ======================
    # 身份
    ("clients", "姓名"): "name",
    ("clients", "Name"): "name",
    ("clients", "name"): "name",
    ("clients", "申请人姓名"): "name",
    ("clients", "中文姓名"): "name",
    ("clients", "拼音"): "name_en",
    ("clients", "拼音姓名"): "name_en",
    ("clients", "英文姓名"): "name_en",
    ("clients", "Pinyin"): "name_en",
    ("clients", "曾用名"): "former_name",
    ("clients", "性别"): "gender",
    ("clients", "Gender"): "gender",
    ("clients", "Sex"): "gender",
    ("clients", "出生日期"): "birth_date",
    ("clients", "Date of Birth"): "birth_date",
    ("clients", "DOB"): "birth_date",
    ("clients", "生日"): "birth_date",
    ("clients", "出生地"): "birth_place",
    ("clients", "出生地点"): "birth_place",
    ("clients", "民族"): "ethnicity",
    ("clients", "国籍"): "nationality",
    ("clients", "Nationality"): "nationality",
    ("clients", "身份证号"): "id_number",
    ("clients", "身份证号码"): "id_number",
    ("clients", "公民身份号码"): "id_number",
    ("clients", "ID Number"): "id_number",
    ("clients", "户籍地址"): "hukou_address",
    ("clients", "住址"): "hukou_address",  # 身份证背面"住址"=户籍地址
    ("clients", "婚姻状况"): "marital_status",

    # 联系方式
    ("clients", "手机"): "phone",
    ("clients", "手机号"): "phone",
    ("clients", "电话"): "phone",
    ("clients", "Phone"): "phone",
    ("clients", "Tel"): "phone",
    ("clients", "邮箱"): "email",
    ("clients", "Email"): "email",
    ("clients", "现家庭住址"): "current_address",
    ("clients", "现居地址"): "current_address",
    ("clients", "现居住址"): "current_address",
    ("clients", "Current residence"): "current_address",
    ("clients", "Address"): "current_address",

    # 护照
    ("clients", "护照号"): "passport_no",
    ("clients", "护照号码"): "passport_no",
    ("clients", "Passport No"): "passport_no",
    ("clients", "Passport Number"): "passport_no",
    ("clients", "签发日期"): "passport_issue_date",
    ("clients", "Issue Date"): "passport_issue_date",
    ("clients", "Date of Issue"): "passport_issue_date",
    ("clients", "有效期"): "passport_expiry_date",
    ("clients", "有效期至"): "passport_expiry_date",
    ("clients", "Expiry Date"): "passport_expiry_date",
    ("clients", "Date of Expiry"): "passport_expiry_date",
    ("clients", "签发机关"): "passport_issuing_authority",
    ("clients", "发证机关"): "passport_issuing_authority",
    ("clients", "Issuing Authority"): "passport_issuing_authority",

    # 教育
    ("clients", "学校"): "school_name",
    ("clients", "学校名称"): "school_name",
    ("clients", "毕业院校"): "school_name",
    ("clients", "毕业学校"): "school_name",
    ("clients", "School"): "school_name",
    ("clients", "英文校名"): "school_name_en",
    ("clients", "专业"): "major",
    ("clients", "Major"): "major",
    ("clients", "学位"): "degree",
    ("clients", "学位等级"): "degree",
    ("clients", "Degree"): "degree",
    ("clients", "毕业日期"): "graduation_date",
    ("clients", "毕业时间"): "graduation_date",
    ("clients", "Graduation Date"): "graduation_date",
    ("clients", "毕业证编号"): "graduation_cert_no",
    ("clients", "毕业证书编号"): "graduation_cert_no",
    ("clients", "学位证编号"): "degree_cert_no",
    ("clients", "学位证书编号"): "degree_cert_no",

    # 工作
    ("clients", "公司"): "company_name",
    ("clients", "公司名称"): "company_name",
    ("clients", "工作单位"): "company_name",
    ("clients", "雇主"): "company_name",
    ("clients", "Company"): "company_name",
    ("clients", "Employer"): "company_name",
    ("clients", "职位"): "position",
    ("clients", "Title"): "position",
    ("clients", "Position"): "position",
    ("clients", "入职日期"): "employment_start_date",
    ("clients", "入职时间"): "employment_start_date",
    ("clients", "月薪"): "monthly_salary",
    ("clients", "月收入"): "monthly_salary",
    ("clients", "Salary"): "monthly_salary",

    # 婚姻（结婚证）
    ("clients", "登记日期"): "marriage_date",
    ("clients", "结婚日期"): "marriage_date",
    ("clients", "登记机关"): "marriage_authority",
    ("clients", "结婚证编号"): "marriage_cert_no",
    ("clients", "结婚证字号"): "marriage_cert_no",

    # ====================== family ======================
    # 基本（与 clients 同名 alias 共用）
    ("family", "姓名"): "name",
    ("family", "Name"): "name",
    ("family", "拼音"): "name_en",
    ("family", "英文姓名"): "name_en",
    ("family", "性别"): "gender",
    ("family", "Gender"): "gender",
    ("family", "出生日期"): "birth_date",
    ("family", "Date of Birth"): "birth_date",
    ("family", "国籍"): "nationality",
    ("family", "Nationality"): "nationality",
    ("family", "身份证号"): "id_number",
    ("family", "身份证号码"): "id_number",
    ("family", "公民身份号码"): "id_number",
    ("family", "手机"): "phone",
    ("family", "电话"): "phone",
    ("family", "Tel"): "phone",
    ("family", "Phone"): "phone",
    # POA 必需
    ("family", "护照号"): "passport_no",
    ("family", "Passport No"): "passport_no",
    ("family", "邮箱"): "email",
    ("family", "Email"): "email",
    ("family", "现家庭住址"): "current_address",
    ("family", "现居地址"): "current_address",
    ("family", "Current residence"): "current_address",
    ("family", "公司"): "company_name",
    ("family", "公司名称"): "company_name",
    ("family", "工作单位"): "company_name",
    ("family", "Company"): "company_name",
    ("family", "职位"): "position",
    ("family", "Title"): "position",
    # 配偶教育
    ("family", "学校"): "school_name",
    ("family", "毕业院校"): "school_name",
    ("family", "School"): "school_name",
    ("family", "专业"): "major",
    ("family", "学位"): "degree",
    ("family", "Degree"): "degree",
    ("family", "毕业日期"): "graduation_date",
    ("family", "毕业证编号"): "graduation_cert_no",
    ("family", "学位证编号"): "degree_cert_no",
    # 子女出生
    ("family", "出生医学证编号"): "birth_cert_no",
    ("family", "出生证编号"): "birth_cert_no",
    ("family", "出生医院"): "birth_hospital",
    ("family", "出生地"): "birth_place",
    ("family", "出生地点"): "birth_place",

    # ====================== assets ======================
    # 通用
    ("assets", "权利人"): "owner_name",
    ("assets", "户名"): "owner_name",
    ("assets", "持有人"): "owner_name",
    ("assets", "Owner"): "owner_name",
    ("assets", "共有人"): "co_owners",
    ("assets", "金额"): "value_amount",
    ("assets", "存款金额"): "value_amount",
    ("assets", "Amount"): "value_amount",
    ("assets", "币种"): "currency",
    ("assets", "Currency"): "currency",
    ("assets", "产权证号"): "certificate_no",
    ("assets", "不动产权证号"): "certificate_no",
    ("assets", "存单号"): "certificate_no",
    ("assets", "证明编号"): "certificate_no",
    # 房产专用
    ("assets", "坐落"): "location_address",
    ("assets", "房产地址"): "location_address",
    ("assets", "面积"): "area_sqm",
    ("assets", "建筑面积"): "area_sqm",
    ("assets", "套内面积"): "area_sqm",
    ("assets", "用途"): "usage_type",
    ("assets", "房屋用途"): "usage_type",
    ("assets", "取得日期"): "acquired_date",
    ("assets", "取得时间"): "acquired_date",
    # 银行专用
    ("assets", "银行"): "bank_name",
    ("assets", "银行名称"): "bank_name",
    ("assets", "开户行"): "bank_name",
    ("assets", "Bank"): "bank_name",
    ("assets", "账号"): "account_no",
    ("assets", "账户号"): "account_no",
    ("assets", "Account"): "account_no",
    ("assets", "起息日"): "period_start",
    ("assets", "存入日期"): "period_start",
    ("assets", "起始日期"): "period_start",
    ("assets", "到期日"): "period_end",
    ("assets", "到期日期"): "period_end",
    ("assets", "结束日期"): "period_end",
    ("assets", "冻结期"): "frozen_until",
    ("assets", "冻结至"): "frozen_until",
}


# ============== 公共 API ==============

def get_default_entity(doc_type: Optional[str]) -> Optional[object]:
    """返回 doc_type 对应的默认归属：
    - "clients"
    - ("family", relation)
    - ("assets", asset_type)
    - None（未识别 doc_type，前端需手动选）
    """
    if not doc_type:
        return None
    return DOC_TYPE_TO_ENTITY.get(doc_type)


def get_target_column(entity: str, field_alias: str) -> Optional[str]:
    """单字段查找。entity ∈ {clients, family, assets}。
    找不到返回 None（调用方应进 KV 兜底）。
    """
    if not field_alias:
        return None
    # 先精确匹配
    col = FIELD_TO_COLUMN.get((entity, field_alias))
    if col:
        return col
    # 再去掉空格后比对（OCR 输出常带空格）
    stripped = str(field_alias).strip()
    if stripped != field_alias:
        col = FIELD_TO_COLUMN.get((entity, stripped))
        if col:
            return col
    # case-insensitive 兜底（仅对 ASCII 别名有效）
    if all(ord(c) < 128 for c in stripped):
        for (e, a), c in FIELD_TO_COLUMN.items():
            if e == entity and a.lower() == stripped.lower():
                return c
    return None


def route_fields(entity: str, ocr_fields: dict) -> Tuple[dict, dict]:
    """把 OCR fields 字典按 entity 路由到列名。

    入参：
      entity: "clients" / "family" / "assets"
      ocr_fields: { 字段名: 值 } 或 { 字段名: {"value": "...", "confidence": 0.9} }

    出参：
      (mapped, unmapped)
        mapped:    { column_name: value_str }   命中 schema 的字段
        unmapped:  { 原字段名: value_str }       未命中（调用方决定写 KV 还是丢弃）

    值的提取：
      - 如果 fval 是 dict（含 value 键），取 fval["value"]
      - 否则直接当字符串
      - 空字符串/None 跳过
    """
    mapped: dict = {}
    unmapped: dict = {}
    for fkey, fval in (ocr_fields or {}).items():
        if not fkey:
            continue
        # 取 value
        if isinstance(fval, dict):
            value = fval.get("value")
        else:
            value = fval
        if value is None:
            continue
        value_str = str(value).strip()
        if not value_str:
            continue

        col = get_target_column(entity, str(fkey))
        if col:
            mapped[col] = value_str
        else:
            unmapped[str(fkey)] = value_str

    return mapped, unmapped


def list_all_aliases(entity: str) -> list[str]:
    """列出某 entity 支持的全部字段别名（前端字段映射展示用）。"""
    return sorted({a for (e, a) in FIELD_TO_COLUMN if e == entity})


def list_doc_types() -> list[str]:
    """所有已知 doc_type（前端 DocTypeSelector 下拉用）。"""
    return sorted(DOC_TYPE_TO_ENTITY.keys())
