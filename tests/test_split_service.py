"""
split_service.py 单测 —— normalize_ranges 边界用例 + split_pdf_by_ranges 端到端。

运行:
    cd e:/qoderproject/20260527
    PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_split_service.py

不依赖 pytest,直接 python 执行。每个 test_* 函数失败抛异常,runner 汇总通过率。
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

# 确保 backend/ 在 sys.path,导入 split_service
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(THIS_DIR, "..", "backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import split_service
from pypdf import PdfReader, PdfWriter

DOC_TYPES = ["身份证", "户口本", "护照", "结婚证"]


def _make_blank_pdf(path: str, n_pages: int) -> None:
    """生成 n 页空白 PDF,用于拆分测试。"""
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        writer.write(f)


# ============== normalize_ranges 单测 ==============

def test_normalize_basic() -> None:
    """两段连续 range,覆盖全部页,无 gap 无重叠。"""
    raw = [
        {"doc_type": "身份证", "page_start": 1, "page_end": 1, "fields": {"姓名": {"value": "张三"}}},
        {"doc_type": "户口本", "page_start": 2, "page_end": 3, "fields": {}},
    ]
    out = split_service.normalize_ranges(raw, total_pages=3, doc_types=DOC_TYPES)
    assert len(out) == 2, f"expected 2 ranges, got {len(out)}"
    assert out[0]["doc_type"] == "身份证" and out[0]["page_start"] == 1 and out[0]["page_end"] == 1
    assert out[1]["doc_type"] == "户口本" and out[1]["page_start"] == 2 and out[1]["page_end"] == 3
    assert out[0]["fields"]["姓名"]["value"] == "张三"


def test_normalize_fill_gap() -> None:
    """中间漏 1 页,应补成 '未知' range。"""
    raw = [
        {"doc_type": "身份证", "page_start": 1, "page_end": 2, "fields": {}},
        {"doc_type": "户口本", "page_start": 4, "page_end": 5, "fields": {}},
    ]
    out = split_service.normalize_ranges(raw, total_pages=5, doc_types=DOC_TYPES)
    assert [r["doc_type"] for r in out] == ["身份证", "未知", "户口本"]
    assert [r["page_start"] for r in out] == [1, 3, 4]
    assert [r["page_end"] for r in out] == [2, 3, 5]


def test_normalize_overlap_first_wins() -> None:
    """重叠:首个 range 占住的页,后续 range 整段被丢弃。"""
    raw = [
        {"doc_type": "身份证", "page_start": 1, "page_end": 2, "fields": {}},
        {"doc_type": "户口本", "page_start": 2, "page_end": 3, "fields": {}},  # 2 已被占
    ]
    out = split_service.normalize_ranges(raw, total_pages=3, doc_types=DOC_TYPES)
    # 期望:身份证 1-2,户口本被整段丢弃,第 3 页补 '未知'
    assert [r["doc_type"] for r in out] == ["身份证", "未知"]
    assert [r["page_start"] for r in out] == [1, 3]
    assert [r["page_end"] for r in out] == [2, 3]


def test_normalize_merge_consecutive_same_type() -> None:
    """LLM 把连续 2 页同类型切成两个 range,应合并为一个。"""
    raw = [
        {"doc_type": "户口本", "page_start": 1, "page_end": 1, "fields": {"姓名": {"value": "A"}}},
        {"doc_type": "户口本", "page_start": 2, "page_end": 2, "fields": {"地址": {"value": "B"}}},
    ]
    out = split_service.normalize_ranges(raw, total_pages=2, doc_types=DOC_TYPES)
    assert len(out) == 1
    assert out[0]["doc_type"] == "户口本"
    assert out[0]["page_start"] == 1 and out[0]["page_end"] == 2
    # fields 应合并
    assert out[0]["fields"]["姓名"]["value"] == "A"
    assert out[0]["fields"]["地址"]["value"] == "B"


def test_normalize_unknown_doc_type_off_whitelist() -> None:
    """白名单外的 doc_type 应被统一归为 '未知'。"""
    raw = [
        {"doc_type": "驾驶证", "page_start": 1, "page_end": 1, "fields": {}},  # 不在白名单
    ]
    out = split_service.normalize_ranges(raw, total_pages=1, doc_types=DOC_TYPES)
    assert len(out) == 1
    assert out[0]["doc_type"] == "未知"


def test_normalize_page_out_of_range() -> None:
    """page_end 超出总页数应被夹到 total_pages。"""
    raw = [
        {"doc_type": "身份证", "page_start": 1, "page_end": 10, "fields": {}},
    ]
    out = split_service.normalize_ranges(raw, total_pages=3, doc_types=DOC_TYPES)
    assert len(out) == 1
    assert out[0]["page_start"] == 1
    assert out[0]["page_end"] == 3


def test_normalize_empty_raw() -> None:
    """LLM 返回空 ranges:应把全部页归为一个 '未知' range。"""
    out = split_service.normalize_ranges([], total_pages=3, doc_types=DOC_TYPES)
    assert len(out) == 1
    assert out[0]["doc_type"] == "未知"
    assert out[0]["page_start"] == 1 and out[0]["page_end"] == 3


def test_normalize_zero_pages() -> None:
    """total_pages=0 时直接返回空列表。"""
    out = split_service.normalize_ranges(
        [{"doc_type": "身份证", "page_start": 1, "page_end": 1, "fields": {}}],
        total_pages=0,
        doc_types=DOC_TYPES,
    )
    assert out == []


def test_normalize_invalid_entries_skipped() -> None:
    """非 dict 或缺字段的项应被跳过,不抛异常。"""
    raw = [
        "not a dict",
        {"doc_type": "身份证"},  # 缺 page_start/page_end
        {"doc_type": "户口本", "page_start": "abc", "page_end": 2, "fields": {}},  # 类型错
        {"doc_type": "身份证", "page_start": 1, "page_end": 2, "fields": {}},  # 合法
    ]
    out = split_service.normalize_ranges(raw, total_pages=2, doc_types=DOC_TYPES)
    assert len(out) == 1
    assert out[0]["doc_type"] == "身份证"


def test_normalize_swap_start_end() -> None:
    """page_start > page_end 时应自动交换。"""
    raw = [
        {"doc_type": "身份证", "page_start": 3, "page_end": 1, "fields": {}},
    ]
    out = split_service.normalize_ranges(raw, total_pages=3, doc_types=DOC_TYPES)
    assert len(out) == 1
    assert out[0]["page_start"] == 1 and out[0]["page_end"] == 3


# ============== split_pdf_by_ranges 端到端单测 ==============

def test_split_pdf_basic() -> None:
    """3 页 PDF,2 段 range -> 2 个子 PDF,文件存在且页数正确。"""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.pdf")
        _make_blank_pdf(src, n_pages=3)
        ranges = [
            {"doc_type": "身份证", "page_start": 1, "page_end": 1, "fields": {}},
            {"doc_type": "户口本", "page_start": 2, "page_end": 3, "fields": {}},
        ]
        out = split_service.split_pdf_by_ranges(src, ranges, tmp, "t1")
        assert len(out) == 2
        assert out[0]["filename"] == "t1_01_身份证.pdf"
        assert out[1]["filename"] == "t1_02_户口本.pdf"
        # 文件存在
        for r in out:
            assert os.path.exists(r["file_path"])
        # 页数验证
        r0 = PdfReader(out[0]["file_path"])
        r1 = PdfReader(out[1]["file_path"])
        assert len(r0.pages) == 1
        assert len(r1.pages) == 2


def test_split_pdf_filename_sanitize() -> None:
    """doc_type 中带 Windows 禁用字符应被替换为下划线。"""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.pdf")
        _make_blank_pdf(src, n_pages=1)
        ranges = [
            {"doc_type": 'a/b:c?', "page_start": 1, "page_end": 1, "fields": {}},
        ]
        out = split_service.split_pdf_by_ranges(src, ranges, tmp, "t2")
        assert len(out) == 1
        # /:?  三个字符都被替换
        assert "/" not in out[0]["filename"]
        assert ":" not in out[0]["filename"]
        assert "?" not in out[0]["filename"]
        assert out[0]["filename"] == "t2_01_a_b_c_.pdf"


def test_split_pdf_empty_ranges() -> None:
    """空 ranges 返回空列表,不创建任何文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.pdf")
        _make_blank_pdf(src, n_pages=1)
        out = split_service.split_pdf_by_ranges(src, [], tmp, "t3")
        assert out == []


def test_split_pdf_single_page_doc() -> None:
    """单页证件:1-1 范围应输出 1 页 PDF。"""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.pdf")
        _make_blank_pdf(src, n_pages=1)
        ranges = [
            {"doc_type": "身份证", "page_start": 1, "page_end": 1, "fields": {}},
        ]
        out = split_service.split_pdf_by_ranges(src, ranges, tmp, "t4")
        assert len(out) == 1
        reader = PdfReader(out[0]["file_path"])
        assert len(reader.pages) == 1


# ============== runner ==============

def _run_all() -> int:
    tests = [
        # normalize_ranges
        test_normalize_basic,
        test_normalize_fill_gap,
        test_normalize_overlap_first_wins,
        test_normalize_merge_consecutive_same_type,
        test_normalize_unknown_doc_type_off_whitelist,
        test_normalize_page_out_of_range,
        test_normalize_empty_raw,
        test_normalize_zero_pages,
        test_normalize_invalid_entries_skipped,
        test_normalize_swap_start_end,
        # split_pdf_by_ranges
        test_split_pdf_basic,
        test_split_pdf_filename_sanitize,
        test_split_pdf_empty_ranges,
        test_split_pdf_single_page_doc,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Total: {len(tests)} | Passed: {passed} | Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
