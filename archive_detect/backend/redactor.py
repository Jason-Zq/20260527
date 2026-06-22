"""
敏感信息脱敏（通用，留底检测专用）。

按顺序应用正则：金额（前缀符号 → 数字+单位）→ 银行卡 → 身份证 → 手机号 → 座机。
顺序很重要：银行卡（16-19 位连续数字）必须先于身份证（18 位）匹配，
身份证必须先于座机（7-8 位）匹配，否则会被短数字段误吞。

只对返回前端的字符串字段调用：reason / summary / title / key_points 列表。
不对 OCR 原文 / DB 写入的原始 LLM 响应做脱敏（OCR 原文根本不返回前端，无需脱敏）。
"""
import re
from typing import Iterable


# (pattern, replacement) — 顺序敏感
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 货币金额 1：前缀符号 ¥ / $ / € / £
    (re.compile(r"[¥￥$€£]\s*\d[\d,]*(?:\.\d+)?"), "[金额]"),
    # 货币金额 2：数字 + 中英文货币/单位
    (re.compile(
        r"\d[\d,]*(?:\.\d+)?\s*(?:万亿|亿|万|千|元|人民币|RMB|CNY|USD|EUR|欧元|美元|港币|HKD)",
        re.IGNORECASE,
    ), "[金额]"),
    # 身份证号：18 位（含末位 X）— 必须先于银行卡，否则 18 位 ID 被当成银行卡吃掉
    (re.compile(
        r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
    ), "[身份证]"),
    # 银行卡 / 账号：15-20 位连续数字（允许中间空格或短横）
    (re.compile(r"\b(?:\d[ -]?){14,19}\d\b"), "[银行卡]"),
    # 手机号：1[3-9]xxxxxxxxx
    (re.compile(r"\b1[3-9]\d{9}\b"), "[手机号]"),
    # 座机：可选区号 + 7-8 位
    (re.compile(r"(?:\(?0\d{2,3}\)?[-\s]?)?\b\d{7,8}\b"), "[座机]"),
]


def redact(text) -> str:
    """对单段字符串脱敏；非字符串原样返回。"""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact_list(items: Iterable) -> list:
    """对 list 中的字符串元素逐个脱敏（其他类型原样保留）。"""
    if not items:
        return []
    return [redact(x) if isinstance(x, str) else x for x in items]


# 默认要脱敏的 dict 字符串字段
_REDACT_STR_KEYS = ("reason", "summary", "title", "relevance_reason")
# 默认要脱敏的 dict 列表字段（元素是字符串）
_REDACT_LIST_KEYS = ("key_points",)


def redact_dict(d: dict) -> dict:
    """对 LLM 返回的 dict 做防御性脱敏：返回新 dict，原 dict 不动。

    覆盖字段：reason / summary / title / relevance_reason（字符串）+ key_points（列表）。
    其他字段（is_archival / confidence / doc_category 等）原样保留。
    """
    if not d:
        return d
    out = dict(d)
    for k in _REDACT_STR_KEYS:
        if k in out and isinstance(out[k], str):
            out[k] = redact(out[k])
    for k in _REDACT_LIST_KEYS:
        if k in out and isinstance(out[k], list):
            out[k] = redact_list(out[k])
    return out
