"""
OCR 服务模块
从 PDF/图片中提取文字，并保留每行文字的坐标框信息。
"""

import os
import uuid
import pdfplumber
import pypdfium2
from paddleocr import PaddleOCR

# PDF类型检测的文字长度阈值
TEXT_LENGTH_THRESHOLD = 100

# 全局 OCR 引擎（懒加载）
_ocr_engine = None

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")


def _get_ocr_engine():
    """懒加载 PaddleOCR 引擎。"""
    global _ocr_engine
    if _ocr_engine is None:
        print("正在初始化 PaddleOCR 引擎...")
        os.environ["FLAGS_use_mkldnn"] = "0"
        _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    return _ocr_engine


def detect_pdf_type(pdf_path: str) -> str:
    """检测 PDF 类型：文字型还是图片型。"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_text = ""
            for page in pdf.pages:
                text = page.extract_text() or ""
                total_text += text
            effective_length = len(total_text.strip().replace(" ", "").replace("\n", ""))
            if effective_length >= TEXT_LENGTH_THRESHOLD:
                return "text"
            else:
                return "image"
    except Exception:
        return "image"


def extract_text_pdf(pdf_path: str) -> list:
    """
    从文字型 PDF 中提取文本。
    返回格式: [{"page": 1, "text": "...", "image": None}]
    """
    results = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            tables = page.extract_tables()
            table_text = ""
            for table in tables:
                for row in table:
                    row_str = " | ".join([cell or "" for cell in row])
                    table_text += row_str + "\n"
            page_text = text
            if table_text.strip():
                page_text += "\n--- 表格内容 ---\n" + table_text
            results.append({"page": i + 1, "text": page_text, "image": None})
    return results


def extract_image_pdf(pdf_path: str, task_id: str, max_ocr_pages: int = 0) -> list:
    """
    从图片型 PDF 中通过 OCR 识别文字，并保留坐标框信息。
    每页渲染为图片保存到 output/{task_id}/images/ 目录。
    max_ocr_pages: 最多OCR的页数，0表示全部OCR。超出的页面只保存切图不OCR。
    返回格式: [{"page": 1, "text": "...", "image": "xxx/page_1.png", "ocr_details": [...]}]
    """
    ocr = _get_ocr_engine()
    pdf = pypdfium2.PdfDocument(pdf_path)
    total_pages = len(pdf)

    # 创建任务图片目录: output/{task_id}/images/
    img_dir = os.path.join(OUTPUT_DIR, task_id, "images")
    os.makedirs(img_dir, exist_ok=True)

    # 确定实际OCR页数
    ocr_page_limit = total_pages if max_ocr_pages <= 0 else min(max_ocr_pages, total_pages)
    if ocr_page_limit < total_pages:
        print(f"  智能截取: 共{total_pages}页，只OCR前{ocr_page_limit}页")

    results = []
    for i in range(total_pages):
        page = pdf[i]
        bitmap = page.render(scale=300 / 72)
        pil_image = bitmap.to_pil()

        img_filename = f"page_{i + 1}.png"
        img_path = os.path.join(img_dir, img_filename)
        pil_image.save(img_path, "PNG")

        # 只有前N页做OCR，剩余页面只保存切图
        if i < ocr_page_limit:
            ocr_result = ocr.ocr(img_path, cls=True)
            page_text_lines = []
            ocr_details = []
            if ocr_result and ocr_result[0]:
                for line in ocr_result[0]:
                    bbox = line[0]
                    text = line[1][0]
                    confidence = float(line[1][1])
                    if confidence > 0.3:
                        page_text_lines.append(text)
                        ocr_details.append({
                            "text": text,
                            "confidence": round(confidence, 4),
                            "bbox": bbox
                            })

            page_text = "\n".join(page_text_lines)
        else:
            # 超出限制的页面，不OCR，只保存切图
            page_text = ""
            ocr_details = []

        results.append({
            "page": i + 1,
            "text": page_text,
            "image": f"{task_id}/images/{img_filename}",
            "ocr_details": ocr_details
        })

    pdf.close()
    return results


def extract_image_file(image_path: str, task_id: str) -> list:
    """
    对单张图片进行 OCR 识别，保留坐标框信息。
    返回格式同 extract_image_pdf。
    """
    ocr = _get_ocr_engine()

    # 复制图片到 output/{task_id}/images/
    img_dir = os.path.join(OUTPUT_DIR, task_id, "images")
    os.makedirs(img_dir, exist_ok=True)

    import shutil
    ext = os.path.splitext(image_path)[1] or ".png"
    img_filename = f"page_1{ext}"
    dest_path = os.path.join(img_dir, img_filename)
    shutil.copy2(image_path, dest_path)

    # OCR 识别
    ocr_result = ocr.ocr(image_path, cls=True)
    page_text_lines = []
    ocr_details = []
    if ocr_result and ocr_result[0]:
        for line in ocr_result[0]:
            bbox = line[0]
            text = line[1][0]
            confidence = float(line[1][1])
            if confidence > 0.3:
                page_text_lines.append(text)
                ocr_details.append({
                    "text": text,
                    "confidence": round(confidence, 4),
                    "bbox": bbox
                })

    page_text = "\n".join(page_text_lines)
    return [{
        "page": 1,
        "text": page_text,
        "image": f"{task_id}/images/{img_filename}",
        "ocr_details": ocr_details
    }]


def process_file(file_path: str, task_id: str, max_ocr_pages: int = 0) -> list:
    """
    统一处理入口：根据文件类型（PDF/图片）自动选择提取方式。
    max_ocr_pages: 图片型PDF最多OCR的页数，0表示全部。
    返回每页的 OCR 结果（含坐标框）。
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        pdf_type = detect_pdf_type(file_path)
        if pdf_type == "text":
            return extract_text_pdf(file_path)
        else:
            return extract_image_pdf(file_path, task_id, max_ocr_pages)
    elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"):
        return extract_image_file(file_path, task_id)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")
