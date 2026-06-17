"""
PDF 拆分服务模块
按 LLM 给出的页码范围把一份多证件 PDF 切成多份独立 PDF。

调用顺序:
  llm_service.detect_page_ranges(per_page_texts)  -> raw_ranges
  split_service.normalize_ranges(raw_ranges, total_pages, doc_types)  -> ranges
  split_service.split_pdf_by_ranges(pdf_path, ranges, output_dir, task_id) -> results
"""

import os
import re
from typing import TypedDict

from pypdf import PdfReader, PdfWriter

# Windows 文件名禁用字符
_FILENAME_FORBIDDEN_RE = re.compile(r'[\\/:*?"<>|]')


class SplitRange(TypedDict):
    """规整后的页码范围(1-based, 含端点)。"""
    doc_type: str
    page_start: int
    page_end: int
    fields: dict


class SplitResult(SplitRange):
    """split_pdf_by_ranges 的输出:SplitRange 加上文件路径/名。"""
    file_path: str
    filename: str


def _safe_filename_part(name: str) -> str:
    """剥掉 Windows 文件名禁用字符,空串归为'未知'。"""
    cleaned = _FILENAME_FORBIDDEN_RE.sub("_", (name or "").strip())
    return cleaned or "未知"


def normalize_ranges(
    raw_ranges: list[dict],
    total_pages: int,
    doc_types: list[str],
) -> list[SplitRange]:
    """规整 LLM 输出:夹页号到 [1,total_pages]、丢重叠、补 gap、合并连续同类型。

    硬约束都在这里完成,LLM prompt 只做软约束。

    Args:
        raw_ranges: LLM 返回的 ranges 列表,每项需含 page_start/page_end/doc_type
        total_pages: PDF 总页数(用于裁剪越界 + 末尾补 gap)
        doc_types: 证件白名单。空列表/None 表示不做白名单过滤,信任 LLM 输出

    Returns:
        规整后的 SplitRange 列表,保证:
          - 按 page_start 升序
          - 无重叠
          - 覆盖 [1, total_pages] 全部页
          - 相邻同 doc_type 已合并
    """
    if total_pages <= 0:
        return []

    seen: set[int] = set()
    cleaned: list[tuple[int, int, str, dict]] = []   # (start, end, doc_type, fields)

    for r in raw_ranges or []:
        if not isinstance(r, dict):
            continue
        try:
            s = int(r["page_start"])
            e = int(r["page_end"])
        except (KeyError, TypeError, ValueError):
            continue
        s = max(1, min(s, total_pages))
        e = max(1, min(e, total_pages))
        if s > e:
            s, e = e, s
        # 重叠拦截:首个分配胜出,后续丢弃
        if any(p in seen for p in range(s, e + 1)):
            continue
        for p in range(s, e + 1):
            seen.add(p)
        dt = r.get("doc_type", "未知")
        if not isinstance(dt, str) or not dt.strip():
            dt = "未知"
        elif doc_types and dt not in doc_types:
            # 仅当显式传入白名单时才过滤,空白名单 = 信任 LLM 输出
            dt = "未知"
        fields = r.get("fields") if isinstance(r.get("fields"), dict) else {}
        cleaned.append((s, e, dt, fields))

    cleaned.sort(key=lambda x: x[0])

    # 补 gap:相邻 range 之间缺的页归为'未知'
    filled: list[SplitRange] = []
    cursor = 1
    for s, e, dt, flds in cleaned:
        if s > cursor:
            filled.append({"doc_type": "未知", "page_start": cursor, "page_end": s - 1, "fields": {}})
        filled.append({"doc_type": dt, "page_start": s, "page_end": e, "fields": flds})
        cursor = e + 1
    if cursor <= total_pages:
        filled.append({"doc_type": "未知", "page_start": cursor, "page_end": total_pages, "fields": {}})

    # 合并相邻同类型(户口本常占连续 2 页,LLM 偶尔会切成两个 range)
    merged: list[SplitRange] = []
    for r in filled:
        if (
            merged
            and merged[-1]["doc_type"] == r["doc_type"]
            and merged[-1]["page_end"] + 1 == r["page_start"]
        ):
            merged[-1]["page_end"] = r["page_end"]
            # 字段合并,新的覆盖旧的
            merged[-1]["fields"].update(r["fields"])
        else:
            merged.append(dict(r))
    return merged


def split_pdf_by_ranges(
    pdf_path: str,
    ranges: list[SplitRange],
    output_dir: str,
    task_id: str,
) -> list[SplitResult]:
    """按 ranges 切分 PDF,每段一份子 PDF 写到 output_dir 下。

    ranges 必须已经过 normalize_ranges 规整(1-based、不重叠、按序)。

    Args:
        pdf_path: 源 PDF 文件路径
        ranges: 规整后的页码范围
        output_dir: 输出目录,不存在会自动创建
        task_id: 用于文件名前缀

    Returns:
        每段一项 SplitResult,带 file_path / filename
    """
    if not ranges:
        return []

    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(pdf_path)
    total_pdf_pages = len(reader.pages)

    results: list[SplitResult] = []
    for idx, rng in enumerate(ranges, start=1):
        writer = PdfWriter()
        # pypdf 是 0-based,LLM/normalize_ranges 是 1-based
        for p in range(rng["page_start"] - 1, rng["page_end"]):
            if 0 <= p < total_pdf_pages:
                writer.add_page(reader.pages[p])

        safe_type = _safe_filename_part(rng["doc_type"])
        filename = f"{task_id}_{idx:02d}_{safe_type}.pdf"
        out_path = os.path.join(output_dir, filename)
        with open(out_path, "wb") as f:
            writer.write(f)

        results.append({
            "doc_type": rng["doc_type"],
            "page_start": rng["page_start"],
            "page_end": rng["page_end"],
            "fields": rng.get("fields", {}),
            "file_path": out_path,
            "filename": filename,
        })

    return results
