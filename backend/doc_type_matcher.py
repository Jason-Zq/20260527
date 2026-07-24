"""证件类型关键词分类器(纯函数,零 LLM 零 IO)。

从客户文件库几十个文件中低成本精准筛出可提取证件:
身份证/户口本/学位证/出生证明/护照/KYC表/结婚证/房产证/无犯罪/批复。
文件夹/文件名/相对路径作线索加分,
OCR 文本头部关键词评分为主;置信不足返回 doc_type=None,由调用方走 LLM 兜底。

打分: score(t) = clamp(40*strong命中数 + 15*positive命中数 + 线索分 - 25*negative命中数, 0, 100)
  - 每个关键词在同一文本里只计一次(kw in text)
  - 线索分: folder_name+rel_path+filename 拼接后命中该类型任一 strong/positive 词 +CLUE_BONUS(每类型一次)
判定: 最高分 >= ACCEPT_THRESHOLD 且领先第二名 >= TIE_MARGIN → 定类;否则 None(LLM 兜底)。
"""
from typing import Optional

DOC_TYPES = ("id_card", "hukou", "degree_cert", "birth_cert", "passport", "kyc_form",
             "marriage_cert", "property_cert", "no_crime", "approval",
             "submission", "receipt")

ACCEPT_THRESHOLD = 60  # 达到则直接定类
TIE_MARGIN = 10        # 前两名分差小于此值视为不确定
CLUE_BONUS = 20        # 文件夹/文件名/相对路径命中线索(每类型至多一次)
OCR_HEAD_CHARS = 3000  # 调用方截断约定(此处仅作文档说明)

_RULES = {
    "id_card": {
        "strong": ["中华人民共和国居民身份证", "公民身份号码"],
        "positive": ["身份证", "居民身份证", "签发机关", "有效期限"],
        "negative": ["户口簿", "常住人口登记卡", "学位证书", "毕业证书", "出生医学证明", "结婚证", "护照"],
    },
    "hukou": {
        "strong": ["居民户口簿", "常住人口登记卡"],
        "positive": ["户口簿", "户口", "户主", "户号", "户籍", "家庭户", "集体户", "户口登记机关"],
        "negative": ["居民身份证", "公民身份号码", "出生医学证明", "结婚证", "学位证书"],
    },
    "degree_cert": {
        "strong": ["学位证书", "学士学位", "硕士学位", "博士学位"],
        "positive": ["学位", "授予", "学位评定委员会", "证书编号"],
        "negative": ["毕业证书", "毕业证", "准予毕业", "结业", "修完", "成绩单"],  # 消歧: 毕业证 ≠ 学位证
    },
    "birth_cert": {
        "strong": ["出生医学证明"],
        "positive": ["新生儿姓名", "出生孕周", "出生体重", "出生身长", "助产机构", "母亲姓名", "父亲姓名"],
        "negative": ["结婚证", "婚姻登记", "持证人", "户口簿", "死亡"],  # 消歧: 结婚证也含"出生日期"
    },
    "passport": {
        "strong": ["中华人民共和国护照", "护照号", "Passport No"],
        "positive": ["护照", "Passport", "国家码", "Country Code", "签发地点", "Place of issue",
                     "Date of issue", "Date of expiry", "签发日期", "有效期至"],
        "negative": ["居民身份证", "公民身份号码", "户口簿", "CDD", "KYC", "信息收集表",
                     "尽职调查", "申请表", "APPLICATION FORM", "批复", "RESIDENCY"],
    },
    "kyc_form": {
        "strong": ["Reason for CDD", "Customer's NRIC", "KYC信息收集表", "客户尽职调查"],
        "positive": ["CDD", "KYC", "信息收集表", "客户姓名", "尽职调查", "Account Opening",
                     "资产来源", "预计存款", "Source of Funds"],
        "negative": ["出生医学证明", "户口簿", "常住人口登记卡", "居民身份证", "公民身份号码"],
    },
    "marriage_cert": {
        "strong": ["结婚证", "结婚证字号"],
        "positive": ["持证人", "婚姻登记", "予以登记", "登记日期", "婚姻法"],
        "negative": ["离婚", "出生医学证明", "居民身份证", "公民身份号码", "户口簿", "护照"],
    },
    "property_cert": {
        "strong": ["不动产权证书", "房屋所有权证", "不动产登记簿", "Real Estate Ownership"],
        "positive": ["不动产", "房屋坐落", "建筑面积", "权利人", "产权证", "房地产权证", "查册", "房产证"],
        "negative": ["居民身份证", "公民身份号码", "户口簿", "护照", "出生医学证明"],
    },
    "no_crime": {
        "strong": ["无犯罪记录证明", "无犯罪记录"],
        "positive": ["未发现", "犯罪记录", "被查询人", "3个月内有效", "电子证照库"],
        "negative": ["户口簿", "护照", "出生医学证明", "学位证书"],
    },
    "approval": {
        # 批复/获批文件(永居卡/批复函);OCR 常粘连空格,关键词备粘连变体
        "strong": ["PERMANENTRESIDENCY", "PERMANENT RESIDENCY", "批复", "Approval"],
        "positive": ["RESIDENCY", "Residency Status", "ResidencyStatus", "永居", "获批",
                     "VISACARD", "VISA CARD"],
        "negative": ["护照", "Passport No", "户口簿", "出生医学证明", "CDD"],
    },
    "submission": {
        # 递交申请包(签证申请表+材料合订);OCR 常粘连空格
        "strong": ["永久居留权申请表", "ApplicationforPermanentResidence",
                   "Application for Permanent Residence", "APPLICATION FORM"],
        "positive": ["申请表", "申請日期", "ApplicationDate", "申请人签字", "递交"],
        "negative": ["VISA CARD", "VISACARD", "签收函", "批复函"],
    },
    "receipt": {
        "strong": ["签收函", "签收回执", "重要文件签收"],
        "positive": ["签收", "交付", "回执", "签收日期", "项目名称"],
        "negative": ["APPLICATION", "Application", "申请表", "VISA CARD", "VISACARD"],
    },
}

_STRONG_SCORE = 40
_POSITIVE_SCORE = 15
_NEGATIVE_SCORE = 25


def _count_hits(keywords: list, text: str) -> int:
    return sum(1 for kw in keywords if kw in text)


def _score(doc_type: str, text: str, clue_text: str) -> int:
    rules = _RULES[doc_type]
    score = (
        _STRONG_SCORE * _count_hits(rules["strong"], text)
        + _POSITIVE_SCORE * _count_hits(rules["positive"], text)
        - _NEGATIVE_SCORE * _count_hits(rules["negative"], text)
    )
    if clue_text and any(kw in clue_text for kw in rules["strong"] + rules["positive"]):
        score += CLUE_BONUS
    return max(0, min(100, score))


def classify(folder_name: Optional[str], filename: Optional[str],
             ocr_head: Optional[str], rel_path: Optional[str] = None) -> dict:
    """返回 {doc_type, score, by, scores}。

    doc_type 为已知类型之一或 None(置信不足,调用方走 LLM 兜底);
    by = 'keyword' | 'none';scores 为各类型得分(可观测/调参用)。
    """
    text = (ocr_head or "")
    if not text.strip():
        return {"doc_type": None, "score": 0, "by": "none",
                "scores": {t: 0 for t in DOC_TYPES}}

    clue_text = " ".join(p for p in (folder_name, rel_path, filename) if p)
    scores = {t: _score(t, text, clue_text) for t in DOC_TYPES}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_type, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    if top_score >= ACCEPT_THRESHOLD and (top_score - second_score) >= TIE_MARGIN:
        return {"doc_type": top_type, "score": top_score, "by": "keyword", "scores": scores}
    return {"doc_type": None, "score": top_score, "by": "none", "scores": scores}
