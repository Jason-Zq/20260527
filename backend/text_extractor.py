"""
统一文字提取（通用，可被任何接口复用）。

按文件类型分发：
  .pdf            → ocr_service.process_file（自动判断文字型/图片型）
  .docx           → python-docx 抽段落+表格（不走 OCR）
  .png/.jpg/...   → ocr_service.extract_image_file

返回统一格式：
  {
    "text": "全文",
    "source": "pdf_text|pdf_ocr|image_ocr|docx_text",
    "page_count": int,
    "char_count": int,
  }
"""

import os
import re
import asyncio
from typing import Optional

import ocr_service


_DOCX_EXT = ".docx"
_PDF_EXT = ".pdf"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}


def _extract_docx(file_path: str) -> dict:
    """python-docx 抽 docx 全文：段落 + 表格 cell 文字。"""
    from docx import Document
    doc = Document(file_path)

    parts: list[str] = []

    # 段落
    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            parts.append(t)

    # 表格
    for tbl_idx, table in enumerate(doc.tables, 1):
        parts.append(f"--- 表 {tbl_idx} ---")
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            cells = [c for c in cells if c]
            if cells:
                parts.append(" | ".join(cells))

    text = "\n".join(parts).strip()
    return {
        "text": text,
        "source": "docx_text",
        "page_count": 1,                 # docx 没有"页"概念，记 1
        "char_count": len(text),
    }


def _extract_pdf(file_path: str) -> dict:
    """复用 ocr_service.process_file，自动选择文字型/图片型路径。"""
    # task_id 仅用于图片型 PDF 落盘 OCR 渲染图。摘要场景不需要这些图，
    # 取一个临时 ID，后续随 temp/ 清理一起带走（图片实际不在 temp/ 而在 output/，但摘要场景我们不存图）。
    # 简化：传一个 fetched_<basename> 作为 task_id，避免污染主 task 命名空间。
    base = os.path.splitext(os.path.basename(file_path))[0]
    task_id = f"fetched_{base}"

    pdf_type = ocr_service.detect_pdf_type(file_path)
    pages = ocr_service.process_file(file_path, task_id, max_ocr_pages=0)
    text = "\n\n".join(p.get("text", "") for p in pages).strip()
    return {
        "text": text,
        "source": "pdf_text" if pdf_type == "text" else "pdf_ocr",
        "page_count": len(pages),
        "char_count": len(text),
    }


def _extract_image(file_path: str) -> dict:
    """图片走 PaddleOCR。"""
    base = os.path.splitext(os.path.basename(file_path))[0]
    task_id = f"fetched_{base}"
    pages = ocr_service.extract_image_file(file_path, task_id)
    text = "\n".join(p.get("text", "") for p in pages).strip()
    return {
        "text": text,
        "source": "image_ocr",
        "page_count": len(pages),
        "char_count": len(text),
    }


async def extract_text(file_path: str, mime_type: Optional[str] = None) -> dict:
    """统一文字提取入口（异步，把同步阻塞代码扔到线程池）。

    抛出：
      ValueError - 不支持的扩展名
      FileNotFoundError
      其他异常 - 透传给调用方
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    if ext == _DOCX_EXT:
        return await asyncio.to_thread(_extract_docx, file_path)

    if ext == ".doc":
        # 旧二进制 doc 格式不支持
        raise ValueError("不支持 .doc 格式，请转换为 .docx 后重试")

    if ext == _PDF_EXT:
        return await asyncio.to_thread(_extract_pdf, file_path)

    if ext in _IMAGE_EXTS:
        return await asyncio.to_thread(_extract_image, file_path)

    raise ValueError(f"不支持的文件类型: {ext}（支持 .pdf/.docx/{'/'.join(sorted(_IMAGE_EXTS))}）")


def normalize_text(text: str, max_chars: Optional[int] = None) -> str:
    """规整文本：合并多余空白、可选截断。
    给 LLM 用之前调一下，避免无意义 token 浪费。
    """
    if not text:
        return ""
    # 合并多余空白行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()

    if max_chars and len(text) > max_chars:
        head = max_chars // 2
        tail = max_chars - head
        text = text[:head] + f"\n\n...[省略 {len(text) - max_chars} 字]...\n\n" + text[-tail:]
    return text
