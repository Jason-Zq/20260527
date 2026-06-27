"""text_extractor._extract_pdf 采样逻辑测试。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_text_extractor_sampling.py

测试 4 个核心 case:
- 小文件(≤ 10 页): 全 OCR,不进 early-exit
- 大文件 + LLM 判 is_large_table=true: 3 页采样
- 大文件 + LLM 判 is_large_table=false: 全 OCR
- 大文件 + LLM 抛异常: 也走采样(激进降级)
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import text_extractor
import ocr_service
import llm_service
import event_service


# === stub event_service 避免真实 DB ===
event_service.log_event = lambda *args, **kwargs: None


# === stub ocr_service & helpers ===
_calls = {"extract_image_pdf": [], "single_page": [], "detect_pdf_type": [], "cleanup": []}


def _mock_pdf_pages(total: int):
    """生成假的 PDF pages list,每页一段假文字。"""
    return [{"page": i + 1, "text": f"page{i+1}-content", "image": "", "ocr_details": []}
            for i in range(total)]


def _setup_mocks(*, total_pages: int, pdf_type: str = "image"):
    """统一 mock 入口。"""
    _calls["extract_image_pdf"].clear()
    _calls["single_page"].clear()
    _calls["detect_pdf_type"].clear()
    _calls["cleanup"].clear()

    def fake_detect_pdf_type(p):
        _calls["detect_pdf_type"].append(p)
        return pdf_type

    def fake_extract_image_pdf(p, task_id, max_ocr_pages=0):
        _calls["extract_image_pdf"].append({"max_ocr_pages": max_ocr_pages})
        if max_ocr_pages > 0:
            return _mock_pdf_pages(min(max_ocr_pages, total_pages))
        return _mock_pdf_pages(total_pages)

    def fake_extract_text_pdf(p):
        return _mock_pdf_pages(total_pages)

    def fake_single_page(p, task_id, idx):
        _calls["single_page"].append({"idx": idx})
        return f"page{idx+1}-tail-content"

    def fake_cleanup(task_id):
        _calls["cleanup"].append(task_id)

    ocr_service.detect_pdf_type = fake_detect_pdf_type
    ocr_service.extract_image_pdf = fake_extract_image_pdf
    ocr_service.extract_text_pdf = fake_extract_text_pdf
    text_extractor._ocr_single_page = fake_single_page
    text_extractor._cleanup_ocr_dir = fake_cleanup

    # mock pypdfium2 拿总页数
    import pypdfium2
    class _FakePdf:
        def __init__(self, *args, **kwargs): pass
        def __len__(self): return total_pages
        def close(self): pass
    pypdfium2.PdfDocument = _FakePdf


# === 测试 ===

def test_small_file_full_ocr():
    """≤ 10 页扫描版,不走 early-exit,全 OCR。"""
    _setup_mocks(total_pages=5, pdf_type="image")
    # llm_service.detect_large_table_doc 不应该被调
    llm_called = {"yes": False}
    llm_service.detect_large_table_doc = lambda t: (llm_called.update(yes=True) or {})

    result = text_extractor._extract_pdf("/tmp/fake_small.pdf")

    assert result["source"] == "pdf_ocr", result
    assert result["page_count"] == 5, result
    assert not llm_called["yes"], "5 页不应该调 LLM 初判"
    # 只调一次 extract_image_pdf,max_ocr_pages=0(全部)
    assert len(_calls["extract_image_pdf"]) == 1
    assert _calls["extract_image_pdf"][0]["max_ocr_pages"] == 0


def test_large_file_text_layer():
    """大文件但有文字层,走 pdfplumber,不走 OCR。"""
    _setup_mocks(total_pages=200, pdf_type="text")

    result = text_extractor._extract_pdf("/tmp/fake_text.pdf")

    assert result["source"] == "pdf_text", result
    assert result["page_count"] == 200
    # 不应调 OCR
    assert len(_calls["extract_image_pdf"]) == 0


def test_large_file_is_table_sampled():
    """> 10 页扫描版 + LLM 判 is_large_table=true → 3 页采样。"""
    _setup_mocks(total_pages=50, pdf_type="image")
    llm_service.detect_large_table_doc = lambda t: {
        "is_large_table": True,
        "doc_type": "bank_statement",
        "confidence": 95,
        "_fallback": False,
    }

    result = text_extractor._extract_pdf("/tmp/fake_statement.pdf")

    assert result["source"] == "pdf_ocr_sampled", result
    assert result["page_count"] == 50
    # 应只 OCR 前 2 页 + 末页
    assert len(_calls["extract_image_pdf"]) == 1
    assert _calls["extract_image_pdf"][0]["max_ocr_pages"] == 2
    assert len(_calls["single_page"]) == 1
    assert _calls["single_page"][0]["idx"] == 49   # 0-based 末页
    # 文本里应该有"已采样"标记
    assert "已采样" in result["text"]
    assert "bank_statement" in result["text"]


def test_large_file_not_table_full_ocr():
    """> 10 页扫描版 + LLM 判 is_large_table=false → 走全文 OCR。"""
    _setup_mocks(total_pages=30, pdf_type="image")
    llm_service.detect_large_table_doc = lambda t: {
        "is_large_table": False,
        "doc_type": "other",
        "confidence": 80,
        "_fallback": False,
    }

    result = text_extractor._extract_pdf("/tmp/fake_passport_book.pdf")

    assert result["source"] == "pdf_ocr", result
    assert result["page_count"] == 30
    # 应该调用 2 次 extract_image_pdf:第 1 次前 2 页判初类,第 2 次全文
    assert len(_calls["extract_image_pdf"]) == 2
    assert _calls["extract_image_pdf"][0]["max_ocr_pages"] == 2
    assert _calls["extract_image_pdf"][1]["max_ocr_pages"] == 0
    assert len(_calls["single_page"]) == 0


def test_large_file_llm_failure_falls_back_to_sampling():
    """> 10 页扫描版 + LLM 抛异常 → 激进降级走采样(不走全文)。"""
    _setup_mocks(total_pages=100, pdf_type="image")
    # detect_large_table_doc 内部已经 catch 所有异常并返回 fallback dict
    # 这里模拟 LLM 真返了 _fallback 的情况
    llm_service.detect_large_table_doc = lambda t: {
        "is_large_table": True,
        "doc_type": "unknown",
        "confidence": 0,
        "_fallback": True,
    }

    result = text_extractor._extract_pdf("/tmp/fake_unknown_large.pdf")

    assert result["source"] == "pdf_ocr_sampled", result
    assert "LLM 降级" in result["text"]
    assert len(_calls["single_page"]) == 1


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed}/{len(tests)} 失败")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
