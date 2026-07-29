"""ai_api_calls 写库清洗规则测试(_build_row 短字段硬截断 + 大字段软截断)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_ai_api_call_clean.py

背景:detect_large_table_doc 曾把临时文件全路径当 task_id 传入,撑爆
ai_api_calls.task_id varchar(64)(生产 107 次 StringDataRightTruncation)。
_build_row 现对所有短 varchar 字段按列宽硬截断(_clean_short,不加截断标记)。
纯函数,不依赖 DB。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from db.ai_api_call_crud import _build_row, _clean_short


def test_short_fields_truncated_to_column_width():
    """超长 operation/model/file_id/task_id 截到 64,batch_id/client_code 截到 40,status 截到 10。"""
    row = _build_row(
        operation="op" * 40,            # 80 字 -> 64
        model="m" * 80,                 # -> 64
        prompt=None, response_raw=None,
        status="ok" + "x" * 20,         # -> 10
        error_msg=None, elapsed_ms=1,
        batch_id="b" * 50,              # -> 40
        file_id="f" * 100,              # -> 64
        client_code="c" * 50,           # -> 40
        task_id="/opt/fastapi/temp/fetched/" + "p" * 100,  # 模拟临时路径 -> 64
    )
    assert len(row.operation) == 64, f"operation {len(row.operation)}"
    assert len(row.model) == 64, f"model {len(row.model)}"
    assert len(row.status) == 10, f"status {len(row.status)}"
    assert len(row.batch_id) == 40, f"batch_id {len(row.batch_id)}"
    assert len(row.file_id) == 64, f"file_id {len(row.file_id)}"
    assert len(row.client_code) == 40, f"client_code {len(row.client_code)}"
    assert len(row.task_id) == 64, f"task_id {len(row.task_id)}"
    assert row.task_id.startswith("/opt/fastapi/temp/fetched/"), "截断应保留头部信息"


def test_short_values_pass_through():
    """短值原样保留,None 透传。"""
    row = _build_row(
        operation="detect_archival", model="deepseek-v4-flash",
        prompt=None, response_raw=None, status="ok",
        error_msg=None, elapsed_ms=None,
        batch_id="250728123456_abc123", file_id="FILE-001",
        client_code=None, task_id=None,
    )
    assert row.operation == "detect_archival"
    assert row.status == "ok"
    assert row.batch_id == "250728123456_abc123"
    assert row.file_id == "FILE-001"
    assert row.client_code is None
    assert row.task_id is None


def test_control_chars_stripped_from_short_fields():
    """短字段里的 NUL/控制字符被去除(PG varchar 不接受 NUL)。"""
    row = _build_row(
        operation="op\x00eration", model=None, prompt=None, response_raw=None,
        status="ok", error_msg=None, elapsed_ms=None,
        batch_id=None, file_id=None, client_code=None, task_id="task\x01_1",
    )
    assert row.operation == "operation"
    assert row.task_id == "task_1"


def test_text_fields_keep_soft_limit_with_marker():
    """大字段仍走 50KB/2KB 软截断 + 截断标记(不回归)。"""
    big = "x" * 60_000
    row = _build_row(
        operation=None, model=None, prompt=big, response_raw=big,
        status="error", error_msg="e" * 3000, elapsed_ms=None,
        batch_id=None, file_id=None, client_code=None, task_id=None,
    )
    assert "已截断" in row.prompt
    assert len(row.prompt) > 50_000, "标记追加在 50KB 截断之后"
    assert "已截断" in row.error_msg


def test_clean_short_hard_limit_no_marker():
    """_clean_short 硬截断:结果绝不超过 limit(与 _clean_text 标记式截断的区别)。"""
    s = _clean_short("y" * 100, 64)
    assert len(s) == 64
    assert "已截断" not in s
    assert _clean_short(None, 64) is None
    assert _clean_short("abc", 64) == "abc"


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
