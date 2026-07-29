"""字段可信度打分(纯函数,读时计算,不落库)。

输入: 字段的 layer/status/当前值 + 该字段全部来源样本(来自 doc_extract_results 的读时溯源,
见 db.profile_crud._collect_field_provenance)。

打分规则(确定性,0-100):
  1. status ∈ (confirmed, corrected) → 100/高(人工已确认/修正,短路);
  2. 基底: layer=verified 70 / declared 50(AI 提取未经人工确认);
  3. 互证: 与当前值归一化相同的不同来源文件数 ≥2 → +15,≥3 → 再 +5(封顶 +20);
     一致来源跨 ≥2 种证件类型 → +5;
  4. 冲突: 与当前值不同的归一化值种数 ≥1 → -25;
  5. clamp [0,100];level: ≥80 高 / 50-79 中 / <50 低。

典型落点: 单来源官方证件 AI=70(中);两证件一致=90(高);自报单来源=50(中);有冲突=45(低)。
"""

_HUMAN_STATUSES = ("confirmed", "corrected")


def compute_field_credibility(*, layer: str, status: str, current_value,
                              field: str, samples: list) -> dict:
    """samples: [{value, source, doc_type, customer_file_id}](该人该字段全部来源)。

    返回 {score, level, reasons, corroboration, conflict_count, sources},
    sources 每条带 agrees(是否与当前值一致)供前端标 一致/不一致。
    """
    # 函数级 import 避免循环: db.profile_crud.attach_field_credibility 调本函数
    from db.profile_crud import _normalize_field_value

    cur_norm = _normalize_field_value(field, current_value)
    agree_files: set = set()
    agree_types: set = set()
    conflict_values: dict = {}
    sources: list = []
    for s in samples:
        raw = str(s.get("value") or "").strip()
        norm = _normalize_field_value(field, raw)
        if not norm:
            continue
        agrees = bool(cur_norm) and norm == cur_norm
        if agrees:
            if s.get("customer_file_id"):
                agree_files.add(s["customer_file_id"])
            if s.get("doc_type"):
                agree_types.add(s["doc_type"])
        else:
            conflict_values.setdefault(norm, raw)
        sources.append({"value": raw, "agrees": agrees,
                        "source": s.get("source"), "doc_type": s.get("doc_type"),
                        "customer_file_id": s.get("customer_file_id")})

    corroboration = len(agree_files)
    conflict_count = len(conflict_values)

    if status in _HUMAN_STATUSES:
        return {"score": 100, "level": "高",
                "reasons": ["人工已确认" if status == "confirmed" else "人工已修正"],
                "corroboration": corroboration, "conflict_count": conflict_count,
                "sources": sources}

    score = 70 if layer == "verified" else 50
    reasons = ["官方证件来源" if layer == "verified" else "客户自报来源",
               "AI 提取,未经人工确认"]
    if corroboration >= 2:
        score += 15
        reasons.append(f"{corroboration} 个文件取值一致")
        if corroboration >= 3:
            score += 5
    if len(agree_types) >= 2:
        score += 5
        reasons.append("跨证件类型互证")
    if conflict_count:
        score -= 25
        examples = "、".join(list(conflict_values.values())[:3])
        reasons.append(f"存在 {conflict_count} 种不一致取值: {examples}")
    score = max(0, min(100, score))
    level = "高" if score >= 80 else ("中" if score >= 50 else "低")
    return {"score": score, "level": level, "reasons": reasons,
            "corroboration": corroboration, "conflict_count": conflict_count,
            "sources": sources}
