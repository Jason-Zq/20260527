"""file_fetcher.sweep_preview_cache 单元测试:按 mtime 过期清扫(纯函数,不依赖 DB/服务)。

运行: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_archive_preview_cache.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import file_fetcher

DAY = 24 * 3600
MAX_AGE = 20 * DAY


def _make_file(dir_path, name, age_sec):
    p = os.path.join(dir_path, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("x")
    ts = time.time() - age_sec
    os.utime(p, (ts, ts))
    return p


def test_sweep_deletes_only_expired():
    with tempfile.TemporaryDirectory() as d:
        fresh = _make_file(d, "191.pdf", 1 * DAY)
        borderline = _make_file(d, "192.docx", 19 * DAY)
        expired = _make_file(d, "193.preview.pdf", 21 * DAY)

        deleted = file_fetcher.sweep_preview_cache(d, MAX_AGE)

        assert deleted == 1, f"应删 1 个,实际 {deleted}"
        assert not os.path.exists(expired), "过期文件应被删除"
        assert os.path.exists(fresh), "新文件应保留"
        assert os.path.exists(borderline), "19 天文件应保留"


def test_sweep_missing_dir_returns_zero():
    deleted = file_fetcher.sweep_preview_cache(
        os.path.join(tempfile.gettempdir(), "no_such_preview_cache_dir_zzz"), MAX_AGE)
    assert deleted == 0


def test_sweep_skips_subdir():
    with tempfile.TemporaryDirectory() as d:
        sub = os.path.join(d, "subdir")
        os.makedirs(sub)
        old_ts = time.time() - 30 * DAY
        os.utime(sub, (old_ts, old_ts))
        deleted = file_fetcher.sweep_preview_cache(d, MAX_AGE)
        assert deleted == 0, "子目录不应被删除"
        assert os.path.isdir(sub)


if __name__ == "__main__":
    test_sweep_deletes_only_expired()
    print("PASS test_sweep_deletes_only_expired")
    test_sweep_missing_dir_returns_zero()
    print("PASS test_sweep_missing_dir_returns_zero")
    test_sweep_skips_subdir()
    print("PASS test_sweep_skips_subdir")
    print("All tests passed.")
