"""
OCR 服务模块
从 PDF/图片中提取文字，并保留每行文字的坐标框信息。
"""

import os
import threading
import uuid
import pdfplumber
import pypdfium2
from rapidocr_onnxruntime import RapidOCR

# PDF类型检测的文字长度阈值
TEXT_LENGTH_THRESHOLD = 100

# 渲染相关参数(关键:控制单页内存峰值)
#   - 200dpi 对扫描件足够,识别率相比 300dpi 下降通常 <2%
#   - 但单页裸像素从 ~26MB 降到 ~11MB(44%),Paddle 算子峰值同步下降
OCR_RENDER_SCALE = 200 / 72
# 单页最大像素数(W*H),超过则等比缩放兜底,防止超大扫描件直接打爆 numpy/OpenCV
MAX_PIXELS = 16_000_000  # ≈ 4000 × 4000

# 全局 OCR 引擎（懒加载）
_ocr_engine = None

# RapidOCR(onnxruntime)单实例多线程推理不保证安全。所有调用都通过 run_ocr,
# 用此锁串行化,避免业务审核 worker 与拆分流程 worker 在不同线程池里同时打它。
_OCR_ENGINE_LOCK = threading.Lock()

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")


def _downscale_if_too_large(pil_image):
    """单页超过 MAX_PIXELS 时等比缩放,防止 OOM。返回 (image, 是否已缩放)。"""
    w, h = pil_image.size
    pixels = w * h
    if pixels <= MAX_PIXELS:
        return pil_image, False
    # 等比缩放到 MAX_PIXELS 以内
    ratio = (MAX_PIXELS / pixels) ** 0.5
    new_w = max(1, int(w * ratio))
    new_h = max(1, int(h * ratio))
    pil_image.thumbnail((new_w, new_h))
    return pil_image, True


def _get_ocr_engine():
    """懒加载 RapidOCR 引擎(onnxruntime,CPU)。模型权重随包内置,无需联网下载。"""
    global _ocr_engine
    if _ocr_engine is None:
        print("正在初始化 RapidOCR 引擎...")
        _ocr_engine = RapidOCR()
    return _ocr_engine


def run_ocr(img_path: str, cls: bool = True):
    """对单张图片跑 OCR(线程安全)。所有模块都应通过此入口调用,避免直接拿引擎。

    返回结构兼容旧 PaddleOCR 格式:[[ [bbox, (text, confidence)], ... ]],
    无文字时返回 [None]。这样下游 ocr_result[0] / line[0] / line[1][0] / line[1][1]
    的解析逻辑无需任何改动。
    cls 参数保留以兼容旧签名,RapidOCR 内部自带方向处理。
    """
    engine = _get_ocr_engine()
    with _OCR_ENGINE_LOCK:
        result, _elapse = engine(img_path)
    if not result:
        return [None]
    converted = [[box, (text, float(score))] for box, text, score in result]
    return [converted]


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
    max_ocr_pages: 最多OCR的页数，0表示全部OCR。超出的页面不渲染、不保存、不OCR(降低写盘峰值)。
    返回格式: [{"page": 1, "text": "...", "image": "xxx/page_1.png", "ocr_details": [...]}]
    """
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
    for i in range(ocr_page_limit):
        page = pdf[i]
        bitmap = page.render(scale=OCR_RENDER_SCALE)
        pil_image = bitmap.to_pil()
        pil_image, _shrunk = _downscale_if_too_large(pil_image)

        img_filename = f"page_{i + 1}.png"
        img_path = os.path.join(img_dir, img_filename)
        pil_image.save(img_path, "PNG")

        ocr_result = run_ocr(img_path, cls=True)
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
    # 复制图片到 output/{task_id}/images/
    img_dir = os.path.join(OUTPUT_DIR, task_id, "images")
    os.makedirs(img_dir, exist_ok=True)

    import shutil
    from PIL import Image
    ext = os.path.splitext(image_path)[1] or ".png"
    img_filename = f"page_1{ext}"
    dest_path = os.path.join(img_dir, img_filename)

    # 单图也走 MAX_PIXELS 兜底,大图直接缩放后再 OCR,避免 numpy/OpenCV OOM
    try:
        with Image.open(image_path) as im:
            im.load()
            shrunk_im, was_shrunk = _downscale_if_too_large(im.copy())
            if was_shrunk:
                shrunk_im.save(dest_path)
                ocr_input_path = dest_path
            else:
                shutil.copy2(image_path, dest_path)
                ocr_input_path = image_path
    except Exception:
        # PIL 打不开时退回到原始 copy(再交给 OCR 引擎自行报错)
        shutil.copy2(image_path, dest_path)
        ocr_input_path = image_path

    # OCR 识别
    ocr_result = run_ocr(ocr_input_path, cls=True)
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
