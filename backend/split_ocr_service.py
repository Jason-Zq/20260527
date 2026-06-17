"""
拆分流水线专用 OCR 服务
与 ocr_service.py 解耦,只服务于"处理超长PDF文件"流水线:
  - 强制全页 OCR(不沿用 config.json 的 max_ocr_pages,那是给单证件解析流水线设的)
  - 降 DPI 到 200(证件类字号 16-24pt,200dpi 足够 PaddleOCR 识别,渲染快 30% + OCR 快 ~50%)
  - 双线程并发 OCR,每线程独立 PaddleOCR 实例(2.x 多线程共享同一实例不稳)

返回结构与 ocr_service.process_file 一致,可直接喂给 llm_service.detect_page_ranges。
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pypdfium2
from paddleocr import PaddleOCR

# 与 ocr_service 共用 output/ 目录约定
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")

# OCR 置信度过滤阈值(与 ocr_service 一致)
_OCR_CONF_THRESHOLD = 0.3

# 每个 worker 线程独立持有一个 OCR 引擎,通过 threading.local 隔离
_thread_local = threading.local()


def _get_thread_ocr() -> PaddleOCR:
    """每线程独立 OCR 实例,避免多线程共享 PaddleOCR 2.x 实例时的推理冲突。"""
    if not hasattr(_thread_local, "ocr"):
        os.environ["FLAGS_use_mkldnn"] = "0"
        _thread_local.ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    return _thread_local.ocr


def _ocr_one(img_path: str) -> tuple[str, list]:
    """对单张 PNG 跑 OCR,返回 (整页文本, ocr_details 列表)。
    逻辑与 ocr_service.extract_image_pdf 保持一致(同一套阈值/字段)。
    """
    ocr = _get_thread_ocr()
    ocr_result = ocr.ocr(img_path, cls=True)

    page_text_lines: list[str] = []
    ocr_details: list[dict] = []
    if ocr_result and ocr_result[0]:
        for line in ocr_result[0]:
            bbox = line[0]
            text = line[1][0]
            confidence = float(line[1][1])
            if confidence > _OCR_CONF_THRESHOLD:
                page_text_lines.append(text)
                ocr_details.append({
                    "text": text,
                    "confidence": round(confidence, 4),
                    "bbox": bbox,
                })
    return ("\n".join(page_text_lines), ocr_details)


def split_extract_all_pages(
    pdf_path: str,
    task_id: str,
    dpi: int = 150,
    max_workers: int = 2,
) -> list[dict]:
    """拆分专用:全页 OCR,降 DPI 渲染 + 双线程并发推理。

    Args:
        pdf_path: 源 PDF 路径
        task_id: 任务 ID,渲染后的 PNG 存到 output/{task_id}/images/
        dpi: PDF 渲染 DPI,默认 150(证件类字号 16-24pt,150dpi 足够分类识别,渲染更快)
        max_workers: 并发 OCR 线程数,默认 2(平衡内存与吞吐)

    Returns:
        每页一项 dict,字段与 ocr_service.process_file 一致:
          {"page", "text", "image" (相对 OUTPUT_DIR 的 URL 子路径), "ocr_details"}
    """
    pdf = pypdfium2.PdfDocument(pdf_path)
    total_pages = len(pdf)

    img_dir = os.path.join(OUTPUT_DIR, task_id, "images")
    os.makedirs(img_dir, exist_ok=True)

    # Step 1: 串行渲染所有页(渲染是 IO 重,Paddle/Python 多进程化没收益)
    img_paths: list[tuple[int, str, str]] = []   # (page_no_1based, abs_path, filename)
    scale = dpi / 72
    for i in range(total_pages):
        page = pdf[i]
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()
        img_filename = f"page_{i + 1}.png"
        img_path = os.path.join(img_dir, img_filename)
        pil_image.save(img_path, "PNG")
        img_paths.append((i + 1, img_path, img_filename))
    pdf.close()
    print(f"  [split_ocr] 渲染完成: {total_pages} 页 @ {dpi}dpi")

    # Step 2: 双线程并发 OCR,按页号 placeholder 保持顺序
    results: list[dict | None] = [None] * total_pages
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(_ocr_one, abs_path): (page_no, filename)
            for page_no, abs_path, filename in img_paths
        }
        for future in future_map:
            page_no, filename = future_map[future]
            try:
                text, details = future.result()
            except Exception as e:
                # 单页失败不阻塞其它页:写空文本,LLM 会归为"未知"
                print(f"  [split_ocr] 第 {page_no} 页 OCR 失败: {e}")
                text, details = ("", [])
            results[page_no - 1] = {
                "page": page_no,
                "text": text,
                "image": f"{task_id}/images/{filename}",
                "ocr_details": details,
            }
    print(f"  [split_ocr] OCR 完成: {total_pages} 页 / {max_workers} 线程")
    # 类型上 results 已经全部填充,直接返回
    return [r for r in results if r is not None]
