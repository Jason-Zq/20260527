"""extract_mixed_pdf 对「pdfplumber 报 0 页」非标 PDF 的全扫描兜底回归测试
（纯函数,mock run_ocr,不起 OCR 引擎）。

背景（2026-08-07,tests/test.pdf 实测）：某些扫描仪软件产出的 PDF,xref 段起始号
写成 1 且首条仍是惯用的 `0000000000 65535 f` free 条目 → 所有对象偏移整体错位 1,
pdfminer 按表查到错位的对象号(getobj 校验收 PDFObjectNotFound)→ Root Catalog 解析
失败 → pdfplumber 报 0 页,而 pypdfium2(pdfium)自愈正常。extract_mixed_pdf 旧逻辑
`min(page_limit, len(plumber.pages))` = 0 → 返回空列表 → 材料解析无图无文字、
留底检测误判 no_text。修复：pdfplumber 0 页时按纯扫描件兜底走 extract_image_pdf。

PDF 用手工构造的最小错位文件,不依赖 fixture;run_ocr mock 掉,不加载引擎。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_mixed_pdf_plumber_zero_pages.py
"""
import sys
import os
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import pdfplumber
import pypdfium2

import ocr_service


def _shifted_xref_pdf() -> bytes:
    """复刻 test.pdf 的非标 xref:起始号 1 + 首条 free 条目 → 偏移整体错位 1。

    一页空白扫描页(无文本层);pypdfium2 报 1 页,pdfplumber 报 0 页。
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /ProcSet [/PDF] >> /Contents 4 0 R >>",
        b"<< /Length 0 >>\rstream\r\rendstream",
    ]
    pdf = b"%PDF-1.7\r"
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\r" % i + body + b"\rendobj\r"
    xref_pos = len(pdf)
    pdf += b"xref\r1 %d\r" % (len(objects) + 1)
    pdf += b"0000000000 65535 f \r"
    for off in offsets:
        pdf += b"%010d 00000 n \r" % off
    pdf += (b"trailer\r<<\r/Size %d\r/Root 1 0 R\r>>\rstartxref\r%d\r%%%%EOF\r"
            % (len(objects) + 1, xref_pos))
    return pdf


_OCR_CANNED = [[([[0, 0], [10, 0], [10, 10], [0, 10]], ("SCAN_FALLBACK_TEXT", 0.99))]]


def test_fixture_reproduces_plumber_zero_pages():
    """钉住 fixture 的前提:pypdfium2 有页、pdfplumber 0 页(否则测试无效)。"""
    tmp = tempfile.mkdtemp(prefix="plumber_zero_test_")
    pdf_path = os.path.join(tmp, "in.pdf")
    try:
        with open(pdf_path, "wb") as f:
            f.write(_shifted_xref_pdf())
        pdf = pypdfium2.PdfDocument(pdf_path)
        try:
            assert len(pdf) == 1, "pypdfium2 应能自愈读出 1 页"
        finally:
            pdf.close()
        with pdfplumber.open(pdf_path) as plumber:
            assert len(plumber.pages) == 0, "pdfplumber 应因错位 xref 报 0 页"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_plumber_zero_pages_falls_back_to_full_scan():
    """pdfplumber 0 页 → 兜底 extract_image_pdf:出页、出图、文本来自 OCR。"""
    tmp = tempfile.mkdtemp(prefix="plumber_zero_test_")
    orig_output, orig_run_ocr = ocr_service.OUTPUT_DIR, ocr_service.run_ocr
    pdf_path = os.path.join(tmp, "in.pdf")
    calls = []
    try:
        with open(pdf_path, "wb") as f:
            f.write(_shifted_xref_pdf())
        ocr_service.OUTPUT_DIR = tmp
        ocr_service.run_ocr = lambda *a, **k: (calls.append(a), _OCR_CANNED)[1]
        pages = ocr_service.extract_mixed_pdf(pdf_path, "t_plumber_zero",
                                              render_text_pages=True)
        assert len(pages) == 1, f"应兜底出 1 页,实际 {len(pages)}"
        assert calls, "兜底路径必须调 run_ocr"
        assert pages[0]["text"] == "SCAN_FALLBACK_TEXT", "文本应来自 OCR 结果"
        assert pages[0]["image"] == "t_plumber_zero/images/page_1.png", pages[0]["image"]
    finally:
        ocr_service.OUTPUT_DIR, ocr_service.run_ocr = orig_output, orig_run_ocr
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_fixture_reproduces_plumber_zero_pages()
    test_plumber_zero_pages_falls_back_to_full_scan()
    print("All tests passed.")
