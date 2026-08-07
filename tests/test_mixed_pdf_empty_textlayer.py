"""extract_mixed_pdf / _extract_pdf 空文本层降级 OCR 回归测试（纯函数,mock run_ocr,不起 OCR 引擎）。

背景（2026-08-07）：邮件转存/文字转曲的 PDF 肉眼有字，但页面既无大图、文本层也为空。
extract_mixed_pdf 旧逻辑只在文本层解析「抛异常」时才降级 OCR，空串被 `is not None`
放行 → 材料解析出空文、留底检测误判 no_text。修复：空文本层也走 OCR 兜底。
本测试盯住两条分支:
  1. 无大图 + 空文本层 → 必须调 run_ocr,文本来自 OCR
  2. 无大图 + 有文本层 → 不调 run_ocr,文本来自文本层(防过度 OCR 回归)
另盯住 _extract_pdf 文字型快车道(detect_pdf_type=text → extract_text_pdf)的同款漏洞:
  3. 文档级判 text 但某页文本层为空 → 该页必须单页 OCR 兜底(source=pdf_text+ocr)
  4. 全页都有文本层 → 不调 run_ocr(source=pdf_text)

PDF 用手工构造的最小文件(算好 xref),不依赖任何 fixture 文件。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_mixed_pdf_empty_textlayer.py
"""
import sys
import os
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import ocr_service
import event_service
import text_extractor

# _extract_pdf 采样分支会写事件流,stub 掉避免真实 DB(本测试走不到,防御性)
event_service.log_event = lambda *a, **k: None


def _build_pdf(objects: list[bytes]) -> bytes:
    """把 1..N 号对象拼成带 xref 的合法 PDF。"""
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(pdf)
    pdf += b"xref\n0 %d\n" % (len(objects) + 1)
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += b"%010d 00000 n \n" % off
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref_pos)
    return pdf


def _blank_page_pdf() -> bytes:
    """空白页:无图片、无文字 —— 无大图 + 空文本层的最小形态。"""
    content = b""
    return _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /ProcSet [/PDF /Text] >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ])


def _text_page_pdf(text: str = "Hello Digital Layer") -> bytes:
    """含真文本层的数字页(标准 Helvetica,无需嵌字体)。"""
    content = f"BT /F1 24 Tf 100 700 Td ({text}) Tj ET".encode()
    return _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources "
        b"<< /Font << /F1 4 0 R >> /ProcSet [/PDF /Text] >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ])


# 超过 TEXT_LENGTH_THRESHOLD(100, 按去空白后计)的真文本层内容
_LONG_TEXT = "HelloDigitalLayer" * 10  # 170 非空白字符


def _mixed_two_page_pdf() -> bytes:
    """第 1 页真文本层(≥阈值,让 detect_pdf_type 判 text),第 2 页空 content stream。"""
    content1 = f"BT /F1 12 Tf 50 700 Td ({_LONG_TEXT}) Tj ET".encode()
    content2 = b""
    return _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources "
        b"<< /Font << /F1 5 0 R >> /ProcSet [/PDF /Text] >> /Contents 6 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /ProcSet [/PDF /Text] >> /Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content1) + content1 + b"\nendstream",
        b"<< /Length %d >>\nstream\n" % len(content2) + content2 + b"\nendstream",
    ])


_OCR_CANNED = [[([[0, 0], [10, 0], [10, 10], [0, 10]], ("OCR_FALLBACK_TEXT", 0.99))]]


def _run(pdf_bytes: bytes, task_id: str, mock_calls: list) -> list:
    """在临时目录里跑 extract_mixed_pdf,run_ocr 换 mock。返回 pages。"""
    tmp = tempfile.mkdtemp(prefix="mixed_pdf_test_")
    orig_output, orig_run_ocr = ocr_service.OUTPUT_DIR, ocr_service.run_ocr
    pdf_path = os.path.join(tmp, "in.pdf")
    try:
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        ocr_service.OUTPUT_DIR = tmp
        ocr_service.run_ocr = lambda *a, **k: (mock_calls.append(a), _OCR_CANNED)[1]
        return ocr_service.extract_mixed_pdf(pdf_path, task_id)
    finally:
        ocr_service.OUTPUT_DIR, ocr_service.run_ocr = orig_output, orig_run_ocr
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_text_layer_falls_back_to_ocr():
    calls = []
    pages = _run(_blank_page_pdf(), "t_empty_layer", calls)
    assert len(pages) == 1
    assert calls, "空文本层页必须降级调 run_ocr"
    assert pages[0]["text"] == "OCR_FALLBACK_TEXT", "文本应来自 OCR 结果"


def test_digital_page_keeps_text_layer():
    calls = []
    pages = _run(_text_page_pdf(), "t_digital", calls)
    assert len(pages) == 1
    assert not calls, "有文本层的数字页不应调 run_ocr"
    assert "Hello Digital Layer" in pages[0]["text"], "文本应来自文本层"


def _run_extract(pdf_bytes: bytes, mock_calls: list) -> dict:
    """在临时目录里跑 text_extractor._extract_pdf,run_ocr 换 mock。"""
    tmp = tempfile.mkdtemp(prefix="extract_pdf_test_")
    orig_output, orig_run_ocr = ocr_service.OUTPUT_DIR, ocr_service.run_ocr
    pdf_path = os.path.join(tmp, "in.pdf")
    try:
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        ocr_service.OUTPUT_DIR = tmp
        ocr_service.run_ocr = lambda *a, **k: (mock_calls.append(a), _OCR_CANNED)[1]
        return text_extractor._extract_pdf(pdf_path)
    finally:
        ocr_service.OUTPUT_DIR, ocr_service.run_ocr = orig_output, orig_run_ocr
        shutil.rmtree(tmp, ignore_errors=True)


def test_text_lane_empty_page_falls_back_to_ocr():
    """文字型快车道:文档级判 text 但第 2 页文本层为空 → 该页单页 OCR 兜底。"""
    calls = []
    result = _run_extract(_mixed_two_page_pdf(), calls)
    assert result["page_count"] == 2
    assert len(calls) == 1, f"只有空文本层页应调 run_ocr(恰好 1 次): {calls}"
    assert _LONG_TEXT in result["text"], "第 1 页文本应来自文本层"
    assert "OCR_FALLBACK_TEXT" in result["text"], "第 2 页文本应来自 OCR 兜底"
    assert result["source"] == "pdf_text+ocr", result["source"]


def test_text_lane_full_text_no_ocr():
    """文字型快车道:全页都有文本层 → 不调 run_ocr(防过度 OCR 回归)。"""
    calls = []
    result = _run_extract(_text_page_pdf(_LONG_TEXT), calls)
    assert not calls, "全页有文本层不应调 run_ocr"
    assert _LONG_TEXT in result["text"]
    assert result["source"] == "pdf_text", result["source"]


if __name__ == "__main__":
    test_empty_text_layer_falls_back_to_ocr()
    test_digital_page_keeps_text_layer()
    test_text_lane_empty_page_falls_back_to_ocr()
    test_text_lane_full_text_no_ocr()
    print("All tests passed.")
