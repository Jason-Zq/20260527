"""业务事件流的应用层入口。

所有业务代码统一通过 log_event 调用,不直接动 event_crud。

设计原则:
  - 永不抛错:DB 写入失败时 print 到 stderr(journald 仍能看到),业务流不受影响
  - fire-and-forget:asyncio.create_task 投后台写,调用方不 await,不阻塞热路径
  - context 限长:超过 2KB 自动截断,避免巨型对象塞表
  - category 常量化:CATEGORY_* 给 IDE 补全和静态检查
"""
import asyncio
import json
import sys
import traceback
from typing import Optional

from db import event_crud


# ==================== 常量 ====================

# severity
INFO = "info"
WARN = "warn"
ERROR = "error"
CRITICAL = "critical"
_VALID_SEVERITIES = {INFO, WARN, ERROR, CRITICAL}

# category(代码内引用常量,避免拼错;前端展示直接用原值)
CATEGORY_SERVICE_START = "service.start"
CATEGORY_SERVICE_STOP = "service.stop"
CATEGORY_BATCH_SUBMIT = "batch.submit"
CATEGORY_BATCH_QUEUE_FULL = "batch.queue_full"
CATEGORY_BATCH_DONE = "batch.done"
CATEGORY_FILE_FAILED = "file.failed"
CATEGORY_FILE_OCR_SLOW = "file.ocr_slow"
CATEGORY_FILE_OCR_SAMPLED = "file.ocr_sampled"
CATEGORY_LLM_TIMEOUT = "llm.timeout"
CATEGORY_DB_ERROR = "db.error"
CATEGORY_WORKER_CRASH = "worker.crash"
CATEGORY_MEMORY_LOW = "memory.low"

# context 序列化后的大小上限(超过截断)
_CONTEXT_MAX_BYTES = 2048
# message 长度上限(对齐表列定义)
_MESSAGE_MAX_LEN = 500


def _truncate_context(context: Optional[dict]) -> Optional[dict]:
    """context > 2KB 时截断为 {_truncated: true, _orig_size: N, _preview: '...'}。"""
    if not context:
        return None
    try:
        serialized = json.dumps(context, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"_truncated": True, "_reason": "json_serialize_failed"}
    if len(serialized.encode("utf-8")) <= _CONTEXT_MAX_BYTES:
        return context
    return {
        "_truncated": True,
        "_orig_size": len(serialized),
        "_preview": serialized[:1500],
    }


def log_event(
    severity: str,
    category: str,
    message: str,
    context: Optional[dict] = None,
) -> None:
    """记录一条业务事件(同步签名,内部 fire-and-forget 异步写 DB)。

    参数校验失败仅 print warning 到 stderr,不抛错。
    DB 写失败也仅 print,不抛错(journald 兜底)。
    """
    # 1) 参数校验(失败 swallow,不影响业务)
    if severity not in _VALID_SEVERITIES:
        print(f"[event_service] 非法 severity={severity!r},事件丢弃", file=sys.stderr)
        return
    if not category:
        print(f"[event_service] category 为空,事件丢弃", file=sys.stderr)
        return
    if not message:
        message = "(no message)"
    message = message[:_MESSAGE_MAX_LEN]
    context_truncated = _truncate_context(context)

    # 2) 投后台写,不 await(调用方零等待)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 不在 event loop 中(测试或脚本),直接同步跑
        try:
            asyncio.run(event_crud.insert_event(severity, category, message, context_truncated))
        except Exception as e:
            print(f"[event_service] 同步写事件失败(忽略): {e}", file=sys.stderr)
        return

    loop.create_task(_safe_insert(severity, category, message, context_truncated))


async def _safe_insert(severity: str, category: str, message: str, context: Optional[dict]) -> None:
    """异步写 DB,任何异常 print 到 stderr。"""
    try:
        await event_crud.insert_event(severity, category, message, context)
    except Exception as e:
        # journald 仍能看到这里的错误;不再 raise
        print(
            f"[event_service] DB 写事件失败 severity={severity} category={category} "
            f"message={message!r}: {e.__class__.__name__}: {e}",
            file=sys.stderr,
        )
