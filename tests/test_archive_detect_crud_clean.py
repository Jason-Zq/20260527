"""archive_detect_crud 文本清洗 helper 单元测试（无外部依赖）。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_archive_detect_crud_clean.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from db.archive_detect_crud import _clean_text, _clean_key_points


def test_clean_nul_bytes():
    assert _clean_text("abc\x00def") == "abcdef"
    assert _clean_text("\x00") == ""


def test_clean_control_chars_keep_whitespace():
    # 保留 \t \n \r,去除其它 C0 控制符和 DEL
    assert _clean_text("\x01\x02\t\n\rtest\x7f") == "\t\n\rtest"
    assert _clean_text("hello\x0bworld") == "helloworld"


def test_clean_none():
    assert _clean_text(None) is None


def test_clean_no_change_for_normal_text():
    s = "正常中文English123!@#"
    assert _clean_text(s) == s


def test_clean_truncate():
    s = "a" * 100
    out = _clean_text(s, limit=10)
    assert out == "a" * 10 + "\n...[已截断,原长 100 字]"


def test_clean_key_points():
    assert _clean_key_points(["a\x00b", None, "c"]) == ["ab", "c"]
    assert _clean_key_points([]) == []
    assert _clean_key_points(None) is None


def test_clean_key_points_truncate_items():
    long_item = "x" * 20_000
    out = _clean_key_points([long_item])
    # 每项按 _KEY_POINT_ITEM_LIMIT 截断
    assert len(out) == 1
    assert out[0].endswith("\n...[已截断,原长 20000 字]")


if __name__ == "__main__":
    test_clean_nul_bytes()
    test_clean_control_chars_keep_whitespace()
    test_clean_none()
    test_clean_no_change_for_normal_text()
    test_clean_truncate()
    test_clean_key_points()
    test_clean_key_points_truncate_items()
    print("All tests passed.")
