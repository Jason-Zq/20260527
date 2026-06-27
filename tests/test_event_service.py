"""event_service.log_event 单元测试。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_event_service.py

stub 掉 event_crud,不依赖真实 DB。
"""
import sys
import os
import asyncio
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import event_service
from db import event_crud


# ---- monkey-patch:用内存 list 替代真实 DB 写 ----
_written: list[tuple] = []

async def _stub_insert(severity, category, message, context=None):
    _written.append((severity, category, message, context))

event_crud.insert_event = _stub_insert


# ---- 测试 ----

def test_log_event_valid_writes_record():
    """正常路径:log_event 调用后,event_crud.insert_event 收到对应参数。"""
    _written.clear()

    async def run():
        event_service.log_event(
            event_service.INFO,
            event_service.CATEGORY_BATCH_SUBMIT,
            "测试消息",
            context={"batch_id": "test_123"},
        )
        # log_event 是 fire-and-forget,让 task 跑完
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert len(_written) == 1, f"expected 1 event written, got {len(_written)}"
    sev, cat, msg, ctx = _written[0]
    assert sev == "info"
    assert cat == "batch.submit"
    assert msg == "测试消息"
    assert ctx == {"batch_id": "test_123"}


def test_log_event_invalid_severity_silently_dropped():
    """非法 severity:不写,不抛错。"""
    _written.clear()
    event_service.log_event("garbage", "x.y", "msg")
    assert _written == []


def test_log_event_empty_category_silently_dropped():
    """category 为空:不写,不抛错。"""
    _written.clear()
    event_service.log_event(event_service.INFO, "", "msg")
    assert _written == []


def test_log_event_db_failure_does_not_raise():
    """DB 写抛错时,log_event 不能把异常传出去(业务流不能被打断)。"""
    _written.clear()

    async def _bomb(*args, **kwargs):
        raise RuntimeError("DB 模拟挂了")

    event_crud.insert_event = _bomb
    try:
        async def run():
            # 同步调,不抛错
            event_service.log_event(event_service.ERROR, event_service.CATEGORY_DB_ERROR, "测试")
            await asyncio.sleep(0.1)   # 让 task 跑(它会吞错)
        # 不抛即通过
        asyncio.run(run())
    finally:
        event_crud.insert_event = _stub_insert


def test_log_event_context_too_large_truncated():
    """context 超过 2KB:被替换为 _truncated=true 摘要。"""
    _written.clear()
    huge = {"big_field": "x" * 5000}

    async def run():
        event_service.log_event(event_service.WARN, event_service.CATEGORY_FILE_FAILED, "msg", context=huge)
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert len(_written) == 1
    _, _, _, ctx = _written[0]
    assert ctx.get("_truncated") is True
    assert "_orig_size" in ctx


def test_log_event_message_truncated_to_500():
    """message > 500 字时自动截断。"""
    _written.clear()
    long_msg = "x" * 1000

    async def run():
        event_service.log_event(event_service.INFO, event_service.CATEGORY_SERVICE_START, long_msg)
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert len(_written) == 1
    _, _, msg, _ = _written[0]
    assert len(msg) == 500


def test_constants_are_strings():
    """所有 CATEGORY_* 常量都是非空字符串。"""
    consts = [k for k in dir(event_service) if k.startswith("CATEGORY_")]
    assert len(consts) >= 10
    for c in consts:
        v = getattr(event_service, c)
        assert isinstance(v, str) and v


def test_severity_constants():
    assert event_service.INFO == "info"
    assert event_service.WARN == "warn"
    assert event_service.ERROR == "error"
    assert event_service.CRITICAL == "critical"


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
