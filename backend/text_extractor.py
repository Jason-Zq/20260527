"""
统一文字提取（通用，可被任何接口复用）。

按文件类型分发：
  .pdf            → ocr_service.process_file（自动判断文字型/图片型）
  .docx           → python-docx 抽段落+表格（不走 OCR）
  .xlsx           → openpyxl 抽 sheet/cell 文本
  .pptx           → python-pptx 抽 slide 文本
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
import llm_service
import event_service


# 大文件 early-exit 阈值:扫描版 PDF 总页数 > 此值才启动 LLM 初判 + 采样
# 小文件全文 OCR 也就几十秒,LLM 初判反而不划算
OCR_EARLY_EXIT_THRESHOLD = int(os.getenv("OCR_EARLY_EXIT_THRESHOLD", "10"))


_DOCX_EXT = ".docx"
_XLS_EXT = ".xls"
_XLSX_EXT = ".xlsx"
_PPTX_EXT = ".pptx"
_PDF_EXT = ".pdf"
_GIF_EXT = ".gif"
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


def _extract_xlsx(file_path: str) -> dict:
    """openpyxl 抽 xlsx 文本：逐 sheet 读取非空 cell。"""
    from openpyxl import load_workbook

    wb = load_workbook(file_path, read_only=True, data_only=True)
    parts: list[str] = []
    max_lines = 5000  # 防止超大表格抽取过多无效文本；LLM 层还会二次截断

    sheet_count = len(wb.sheetnames)
    try:
        for ws in wb.worksheets:
            if len(parts) >= max_lines:
                break
            parts.append(f"--- Sheet: {ws.title} ---")
            for row in ws.iter_rows():
                cells = []
                for cell in row:
                    if cell.value is None:
                        continue
                    value = str(cell.value).strip()
                    if value:
                        cells.append(f"{cell.coordinate}: {value}")
                if cells:
                    parts.append(" | ".join(cells))
                if len(parts) >= max_lines:
                    parts.append("...[表格内容过长，已截断]...")
                    break
    finally:
        wb.close()

    text = "\n".join(parts).strip()
    return {
        "text": text,
        "source": "xlsx_text",
        "page_count": sheet_count,
        "char_count": len(text),
    }


def _extract_xls(file_path: str) -> dict:
    """xlrd 抽旧版 xls 文本：逐 sheet 读取非空 cell。"""
    import xlrd

    book = xlrd.open_workbook(file_path)
    parts: list[str] = []
    max_lines = 5000

    for sheet in book.sheets():
        if len(parts) >= max_lines:
            break
        parts.append(f"--- Sheet: {sheet.name} ---")
        for r in range(sheet.nrows):
            cells = []
            for c in range(sheet.ncols):
                value = sheet.cell_value(r, c)
                if value is None:
                    continue
                value = str(value).strip()
                if value:
                    # xlrd 用 0-based,展示成 R/C 避免复杂列号转换
                    cells.append(f"R{r + 1}C{c + 1}: {value}")
            if cells:
                parts.append(" | ".join(cells))
            if len(parts) >= max_lines:
                parts.append("...[表格内容过长，已截断]...")
                break

    text = "\n".join(parts).strip()
    return {
        "text": text,
        "source": "xls_text",
        "page_count": book.nsheets,
        "char_count": len(text),
    }
def _extract_pptx(file_path: str) -> dict:
    """python-pptx 抽 pptx 文本：逐 slide 抽 shape.text。"""
    from pptx import Presentation

    prs = Presentation(file_path)
    parts: list[str] = []
    for idx, slide in enumerate(prs.slides, 1):
        slide_lines = []
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text and text.strip():
                slide_lines.append(text.strip())
        if slide_lines:
            parts.append(f"--- Slide {idx} ---")
            parts.extend(slide_lines)

    text = "\n".join(parts).strip()
    return {
        "text": text,
        "source": "pptx_text",
        "page_count": len(prs.slides),
        "char_count": len(text),
    }


def _cleanup_ocr_dir(task_id: str) -> None:
    """删除 OCR 渲染中间产物 output/{task_id}/。
    业务审核/摘要场景下,PNG 只是 OCR 中间产物,文字抽完即可丢弃。
    失败不抛(磁盘清理是 best-effort)。
    """
    try:
        import shutil
        d = os.path.join(ocr_service.OUTPUT_DIR, task_id)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    except Exception as e:
        print(f"[text_extractor] 清理 {task_id} 失败(忽略): {e}")


def _ocr_single_page(file_path: str, task_id: str, page_index_0based: int) -> str:
    """只 OCR 指定页(0-based 索引)。用于大文件 early-exit 时抓末页盖章/合计。

    内联实现,不污染 ocr_service 接口。
    复用 ocr_service.run_ocr 引擎锁,跨线程池安全。
    """
    import pypdfium2
    img_dir = os.path.join(ocr_service.OUTPUT_DIR, task_id, "images")
    os.makedirs(img_dir, exist_ok=True)

    pdf = pypdfium2.PdfDocument(file_path)
    try:
        page = pdf[page_index_0based]
        bitmap = page.render(scale=ocr_service.OCR_RENDER_SCALE)
        pil_image = bitmap.to_pil()
        pil_image, _ = ocr_service._downscale_if_too_large(pil_image)
        img_filename = f"page_{page_index_0based + 1}.png"
        img_path = os.path.join(img_dir, img_filename)
        pil_image.save(img_path, "PNG")
    finally:
        pdf.close()

    # OCR 这一张图
    ocr_result = ocr_service.run_ocr(img_path, cls=True)
    lines = []
    if ocr_result and ocr_result[0]:
        for line in ocr_result[0]:
            text = line[1][0]
            conf = float(line[1][1])
            if conf > 0.3:
                lines.append(text)
    return "\n".join(lines)


def _extract_pdf(file_path: str) -> dict:
    """PDF 文字提取,带大文件 early-exit 优化。

    流程:
    1. 文字型 PDF(有文字层) → pdfplumber 全文(秒级)
    2. 扫描版 ≤ OCR_EARLY_EXIT_THRESHOLD 页 → 走原 OCR 全文
    3. 扫描版 > 阈值页 → 先 OCR 前 2 页 → LLM 初判
        - 是大表类(流水/社保/证券): 再 OCR 末页 = 3 页采样返回
        - 否则: OCR 第 3 页起的剩余页 = 全文返回
        - LLM 抽风: 也走采样(激进保速度)
    """
    import uuid
    base = os.path.splitext(os.path.basename(file_path))[0]
    task_id = f"fetched_{base}_{uuid.uuid4().hex[:8]}"

    try:
        # === 1. 文字型 PDF ===
        pdf_type = ocr_service.detect_pdf_type(file_path)
        if pdf_type == "text":
            pages = ocr_service.extract_text_pdf(file_path)
            text = "\n\n".join(p.get("text", "") for p in pages).strip()
            return {
                "text": text,
                "source": "pdf_text",
                "page_count": len(pages),
                "char_count": len(text),
            }

        # === 2. 拿总页数 ===
        import pypdfium2
        pdf = pypdfium2.PdfDocument(file_path)
        try:
            total_pages = len(pdf)
        finally:
            pdf.close()

        # === 3. 小文件直接全 OCR ===
        if total_pages <= OCR_EARLY_EXIT_THRESHOLD:
            pages = ocr_service.extract_image_pdf(file_path, task_id, max_ocr_pages=0)
            text = "\n\n".join(p.get("text", "") for p in pages).strip()
            return {
                "text": text,
                "source": "pdf_ocr",
                "page_count": len(pages),
                "char_count": len(text),
            }

        # === 4. 大文件 early-exit:先 OCR 前 2 页 ===
        head_pages = ocr_service.extract_image_pdf(file_path, task_id, max_ocr_pages=2)
        head_text = "\n\n".join(p.get("text", "") for p in head_pages[:2]).strip()

        # === 5. LLM 初判 ===
        verdict = llm_service.detect_large_table_doc(head_text)
        is_large_table = bool(verdict.get("is_large_table"))
        doc_type = verdict.get("doc_type") or "unknown"
        is_fallback = bool(verdict.get("_fallback"))

        # === 6a. 是大表类(或 LLM 抽风) → 加一页末页就返回 ===
        if is_large_table:
            try:
                tail_text = _ocr_single_page(file_path, task_id, total_pages - 1)
            except Exception as e:
                print(f"[text_extractor] 末页 OCR 失败,只用前 2 页: {e}")
                tail_text = ""

            sampled_text = head_text
            if tail_text:
                sampled_text += "\n\n--- 中间页未识别 ---\n\n" + tail_text
            sampled_text += (
                f"\n\n[已采样 OCR: 共 {total_pages} 页, 实际识别第 1,2,{total_pages} 页. "
                f"判定为 {doc_type}{'(LLM 降级)' if is_fallback else ''}]"
            )

            # 记一条事件,事件流可观测采样命中率
            try:
                event_service.log_event(
                    event_service.INFO,
                    event_service.CATEGORY_FILE_OCR_SAMPLED,
                    f"文件采样 OCR:共 {total_pages} 页,识别 3 页({doc_type})",
                    context={
                        "filename": os.path.basename(file_path),
                        "total_pages": total_pages,
                        "sampled_pages": [1, 2, total_pages],
                        "doc_type": doc_type,
                        "llm_fallback": is_fallback,
                        "confidence": verdict.get("confidence", 0),
                    },
                )
            except Exception:
                pass

            return {
                "text": sampled_text,
                "source": "pdf_ocr_sampled",
                "page_count": total_pages,
                "char_count": len(sampled_text),
            }

        # === 6b. 不是大表类 → OCR 全文(继续把剩余页 OCR 完) ===
        # 直接重新跑一次全 OCR(前 2 页缓存重复跑,代价小,逻辑简单)
        pages = ocr_service.extract_image_pdf(file_path, task_id, max_ocr_pages=0)
        text = "\n\n".join(p.get("text", "") for p in pages).strip()
        return {
            "text": text,
            "source": "pdf_ocr",
            "page_count": len(pages),
            "char_count": len(text),
        }

    finally:
        _cleanup_ocr_dir(task_id)


def _extract_image(file_path: str) -> dict:
    """图片走 PaddleOCR。"""
    import uuid
    base = os.path.splitext(os.path.basename(file_path))[0]
    task_id = f"fetched_{base}_{uuid.uuid4().hex[:8]}"
    try:
        pages = ocr_service.extract_image_file(file_path, task_id)
        text = "\n".join(p.get("text", "") for p in pages).strip()
        return {
            "text": text,
            "source": "image_ocr",
            "page_count": len(pages),
            "char_count": len(text),
        }
    finally:
        _cleanup_ocr_dir(task_id)


def _extract_gif(file_path: str) -> dict:
    """GIF 只取第一帧转 PNG 后走 OCR。"""
    from PIL import Image
    import tempfile

    base = os.path.splitext(os.path.basename(file_path))[0]
    tmp_path = os.path.join(tempfile.gettempdir(), f"{base}_gif_first_frame.png")
    try:
        with Image.open(file_path) as im:
            im.seek(0)
            im.convert("RGB").save(tmp_path, "PNG")
        result = _extract_image(tmp_path)
        result["source"] = "gif_first_frame_ocr"
        return result
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


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

    if ext == _XLSX_EXT:
        return await asyncio.to_thread(_extract_xlsx, file_path)

    if ext == _PPTX_EXT:
        return await asyncio.to_thread(_extract_pptx, file_path)

    if ext == _XLS_EXT:
        return await asyncio.to_thread(_extract_xls, file_path)

    if ext == _GIF_EXT:
        return await asyncio.to_thread(_extract_gif, file_path)

    if ext == ".doc":
        # 旧二进制 doc 格式不支持
        raise ValueError("暂不支持旧版 Word(.doc)，请转换为 .docx 后上传")

    if ext == _PDF_EXT:
        return await asyncio.to_thread(_extract_pdf, file_path)

    if ext in _IMAGE_EXTS:
        return await asyncio.to_thread(_extract_image, file_path)

    raise ValueError(f"不支持的文件类型: {ext}（支持 .pdf/.docx/.xls/.xlsx/.pptx/.gif/{'/'.join(sorted(_IMAGE_EXTS))}）")


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
