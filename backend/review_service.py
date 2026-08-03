"""复核与质量评级(纯规则,不调 LLM;打分部分为纯函数可单测)。

质量分 quality_score 0-100,越小越急需复核:
  P1(0-20): 无文本/OCR 乱码/提取异常/no_person —— 数据不可用或无法归属
  P2(30-50): 文本过短/证件号脱敏/字段校验疑点/分类置信低 —— 数据可用但需人工看一眼
  none(100): 无待复核项

证件照类文件夹(folder 含"证件照")无文本是预期,不判待复核。
"""
from typing import Optional

# 乱码判定:RapidOCR 对歪/糊扫描件的典型输出混杂希腊/拉丁/符号噪声。
# 单字符标记不可靠(实测乱码样本 〓 仅 2.2%),改用组合判定:
#   CJK 汉字占比低(不像中文文档) 且 怪字符(〓/Φ/□/■ 等)占比高 → garbled
_GARBLED_CHARS = ("�", "〓", "Φ", "φ", "□", "■", "△", "▲", "§")
_GARBLED_CJK_MAX = 0.35      # CJK 占比低于此值
_GARBLED_WEIRD_MIN = 0.03    # 怪字符占比高于此值(与 CJK 低组合判定)
_GARBLED_WEIRD_HARD = 0.08   # 怪字符占比超过此值直接判乱码(实测:乱码件 8-13%,英文件≈0)
_SHORT_TEXT_CHARS = 30       # 有效文本短于此判 ocr_short
_LOW_CONFIDENCE = 60         # LLM 分类置信低于此判 low_confidence
_PHOTO_FOLDER_HINT = "证件照"


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def cjk_ratio(text: str) -> float:
    """CJK 汉字占比(忽略空白)。"""
    body = [ch for ch in text if not ch.isspace()]
    if not body:
        return 0.0
    return sum(1 for ch in body if _is_cjk(ch)) / len(body)


def garbled_ratio(text: str) -> float:
    """怪字符(〓/Φ/□/■ 等)占比(忽略空白)。"""
    body = [ch for ch in text if not ch.isspace()]
    if not body:
        return 0.0
    return sum(1 for ch in body if ch in _GARBLED_CHARS) / len(body)


def is_garbled(text: str) -> bool:
    """OCR 乱码判定:怪字符占比 >8% 直接判;否则 CJK 占比低 + 怪字符 >3% 组合判。"""
    weird = garbled_ratio(text)
    if weird > _GARBLED_WEIRD_HARD:
        return True
    return cjk_ratio(text) < _GARBLED_CJK_MAX and weird > _GARBLED_WEIRD_MIN


def evaluate_file_quality(*, ocr_text: Optional[str], folder_name: Optional[str] = None,
                          doc_type: Optional[str] = None,
                          classify_by: Optional[str] = None,
                          classify_score: Optional[int] = None,
                          extract_status: Optional[str] = None,
                          extract_skip_reason: Optional[str] = None,
                          id_masked: bool = False,
                          validation_flags: int = 0) -> dict:
    """评估单个文件的质量与复核需求。

    validation_flags: 提取字段校验未自动修的疑点数(field_validators flags;
    校验位唯一候选已自动修复的不计入)。

    返回 {"quality_score": int, "review_status": "none"|"needs_review",
          "review_reason": str|None}
    """
    text = (ocr_text or "").strip()
    is_photo_folder = bool(folder_name and _PHOTO_FOLDER_HINT in folder_name)

    if not text:
        if is_photo_folder:
            return {"quality_score": 90, "review_status": "none", "review_reason": None}
        return {"quality_score": 0, "review_status": "needs_review", "review_reason": "no_text"}
    if is_garbled(text):
        return {"quality_score": 10, "review_status": "needs_review", "review_reason": "garbled"}
    if len(text) < _SHORT_TEXT_CHARS:
        if is_photo_folder:
            return {"quality_score": 90, "review_status": "none", "review_reason": None}
        return {"quality_score": 30, "review_status": "needs_review", "review_reason": "ocr_short"}
    if extract_status == "error":
        return {"quality_score": 15, "review_status": "needs_review", "review_reason": "extract_error"}
    if extract_skip_reason == "no_person":
        return {"quality_score": 20, "review_status": "needs_review", "review_reason": "no_person"}
    if id_masked:
        return {"quality_score": 40, "review_status": "needs_review", "review_reason": "masked_id"}
    if validation_flags > 0:
        return {"quality_score": 45, "review_status": "needs_review", "review_reason": "field_validation"}
    if classify_by == "llm" and (classify_score or 0) < _LOW_CONFIDENCE:
        return {"quality_score": 50, "review_status": "needs_review", "review_reason": "low_confidence"}
    return {"quality_score": 100, "review_status": "none", "review_reason": None}
