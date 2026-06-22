"""文件留底检测：编排服务（无数据库版本）。

提交 → in-memory 状态字典 → 异步 fan-out N 个文件并行处理
- OCR：受 ocr_service 全局单引擎天然串行；额外加 _OCR_LOCK 显式串行
- LLM：asyncio.Semaphore(3) 限流
- 单文件失败不影响 batch 其他文件（return_exceptions=True）
- 内存中保留 6 小时；超过后台 GC 自动清理（防止内存泄露）

NOTE: 重启后所有任务状态丢失（用户需重新提交）。这是子项目刻意去 DB 的代价。
"""
import os
import asyncio
import time
import secrets
from datetime import datetime, timedelta
from typing import Optional

import file_fetcher
import text_extractor
import llm_service
import redactor


# ==================== 常量与状态 ====================

MAX_FILES_PER_BATCH = 20
LLM_CONCURRENCY = 3
RESULT_TTL_HOURS = 6                         # 内存结果保留 6 小时

# 内存态：{batch_id: {batch_id, status, total_files, done_files, user_prompt,
#                    source_kind, files: [...], created_ts}}
_batch_status: dict[str, dict] = {}

_OCR_LOCK = asyncio.Lock()
_LLM_SEMAPHORE = asyncio.Semaphore(LLM_CONCURRENCY)


# ==================== 工具 ====================

def _upload_temp_dir() -> str:
    """temp/archive_detect/ — 上传文件的暂存目录，处理完后删除。"""
    root = os.path.dirname(os.path.abspath(__file__))
    d = os.path.normpath(os.path.join(root, "..", "temp", "archive_detect"))
    os.makedirs(d, exist_ok=True)
    return d


def gen_batch_id() -> str:
    """YYMMDDHHMMSS_<6 hex>。"""
    return datetime.now().strftime("%y%m%d%H%M%S") + "_" + secrets.token_hex(3)


def _humanize_fetch_error(exc: Exception) -> str:
    """把 httpx 的网络层异常翻译成业务方能看懂的中文。"""
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return "文件不存在（404），地址可能已失效"
        if code == 403:
            return "服务器拒绝访问（403），可能需要授权"
        if code == 401:
            return "需要登录鉴权（401）"
        if code >= 500:
            return f"远端服务器错误（{code}）"
        return f"HTTP {code}"
    if isinstance(exc, httpx.ConnectTimeout):
        return "连接超时，地址可能无法访问"
    if isinstance(exc, httpx.ReadTimeout):
        return "下载超时（>5 分钟），文件过大或网络太慢"
    if isinstance(exc, httpx.ConnectError):
        return "无法连接到该地址（DNS 或网络问题）"
    if isinstance(exc, httpx.RequestError):
        return "网络请求失败"
    return str(exc) or exc.__class__.__name__


def _set_file_state(batch_id: str, idx: int, **patch) -> None:
    state = _batch_status.get(batch_id)
    if not state:
        return
    files = state.get("files") or []
    for f in files:
        if f.get("idx") == idx:
            f.update(patch)
            return


# ==================== 提交入口 ====================

async def submit_batch(
    *,
    user_prompt: str,
    source_kind: str,
    items: list[dict],
) -> str:
    """创建 batch，启动后台 orchestrator，立即返回 batch_id。

    items: list[dict]
      - upload 模式：{"local_path": str, "filename": str, "mime_type": str}
      - url 模式   ：{"source_url": str}
    """
    if not user_prompt or not user_prompt.strip():
        raise ValueError("判定标准 user_prompt 不能为空")
    if not items:
        raise ValueError("文件列表为空")
    if len(items) > MAX_FILES_PER_BATCH:
        raise ValueError(f"单次最多 {MAX_FILES_PER_BATCH} 个文件，收到 {len(items)} 个")
    if source_kind not in ("upload", "url"):
        raise ValueError(f"非法 source_kind={source_kind!r}")

    batch_id = gen_batch_id()

    _batch_status[batch_id] = {
        "batch_id": batch_id,
        "user_prompt": user_prompt.strip(),
        "source_kind": source_kind,
        "total_files": len(items),
        "done_files": 0,
        "status": "running",
        "error": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_ts": time.time(),
        "files": [
            {
                "idx": i,
                "source_url": it.get("source_url"),
                "filename": it.get("filename"),
                "mime_type": it.get("mime_type"),
                "status": "pending",
                "is_archival": None,
                "confidence": None,
                "reason": None,
                "key_points": [],
                "doc_category": None,
                "page_count": None,
                "char_count": None,
                "elapsed_sec": None,
                "error_msg": None,
            }
            for i, it in enumerate(items)
        ],
    }

    asyncio.create_task(_orchestrate(batch_id, user_prompt.strip(), source_kind, items))
    return batch_id


# ==================== 后台编排 ====================

async def _orchestrate(batch_id: str, user_prompt: str, source_kind: str, items: list[dict]):
    """fan-out 所有文件，等全部结束后置 batch.status=done。"""
    try:
        tasks = [
            asyncio.create_task(_process_one(batch_id, idx, item, user_prompt, source_kind))
            for idx, item in enumerate(items)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        state = _batch_status.get(batch_id)
        if state:
            state["status"] = "done"


async def _process_one(
    batch_id: str,
    idx: int,
    item: dict,
    user_prompt: str,
    source_kind: str,
):
    """单文件流水线：fetch → ocr → llm → redact。"""
    t0 = time.time()
    fetched_temp_path: Optional[str] = None
    upload_path = item.get("local_path")

    filename = item.get("filename") or ""
    mime_type = item.get("mime_type")
    page_count = char_count = None

    try:
        # 1) 拿到本地文件路径
        if source_kind == "url":
            url = item.get("source_url") or ""
            _set_file_state(batch_id, idx, status="fetching")

            if not url.strip():
                raise ValueError("文件地址为空")
            try:
                local_path, filename, mime_type = await file_fetcher.fetch_url_to_temp(url)
            except file_fetcher.FileTooLargeError:
                raise ValueError("文件超过 50MB 上限，无法处理")
            except ValueError as e:
                # scheme 非法 / URL 空
                raise ValueError(f"文件地址无效：{e}")
            except Exception as e:
                # httpx 网络层错误（404 / DNS 失败 / 连接超时 / SSL 等）
                msg = _humanize_fetch_error(e)
                raise ValueError(f"无法下载文件：{msg}")
            fetched_temp_path = local_path

            if not file_fetcher.is_supported_extension(filename):
                raise ValueError(f"不支持的文件类型：{filename}")
        else:
            local_path = upload_path
            if not local_path or not os.path.exists(local_path):
                raise ValueError("上传文件丢失")
            # 上传文件大小检查（与 file_fetcher 50MB 上限对齐）
            try:
                size = os.path.getsize(local_path)
            except OSError:
                size = 0
            if size > file_fetcher.MAX_DOWNLOAD_BYTES:
                mb = size / 1024 / 1024
                raise ValueError(f"文件体积 {mb:.1f}MB 超过 50MB 上限，无法处理")

        # 2) 文本抽取（OCR 锁串行）
        _set_file_state(batch_id, idx, status="ocr", filename=filename, mime_type=mime_type)
        async with _OCR_LOCK:
            extracted = await text_extractor.extract_text(local_path, mime_type)

        text = extracted.get("text") or ""
        page_count = extracted.get("page_count")
        char_count = extracted.get("char_count")
        if not text.strip():
            raise ValueError("OCR/抽取后无文字")

        # 3) LLM 判定（限流 3 并发）
        _set_file_state(batch_id, idx, status="llm",
                        page_count=page_count, char_count=char_count)
        async with _LLM_SEMAPHORE:
            verdict = await asyncio.to_thread(llm_service.detect_archival, text, user_prompt)

        # 4) 脱敏（防御层）
        verdict = redactor.redact_dict(verdict)

        # 5) 写回内存态
        elapsed = round(time.time() - t0, 2)
        _set_file_state(
            batch_id, idx,
            status="done",
            filename=filename,
            mime_type=mime_type,
            page_count=page_count,
            char_count=char_count,
            elapsed_sec=elapsed,
            **verdict,
        )

    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        msg = str(e) or e.__class__.__name__
        _set_file_state(batch_id, idx, status="error", error_msg=msg, elapsed_sec=elapsed,
                        filename=filename or None)
    finally:
        # done 计数（成功/失败都算）
        state = _batch_status.get(batch_id)
        if state:
            state["done_files"] = (state.get("done_files") or 0) + 1

        # 清理临时文件
        if fetched_temp_path:
            file_fetcher.cleanup_temp_file(fetched_temp_path)
        if upload_path:
            try:
                if os.path.exists(upload_path):
                    os.remove(upload_path)
            except OSError:
                pass


# ==================== 查询入口 ====================

def get_batch(batch_id: str) -> Optional[dict]:
    """同步：从内存读批次状态。重启后将返回 None（用户需重新提交）。"""
    return _batch_status.get(batch_id)


# ==================== 后台 GC ====================

async def gc_loop(interval_seconds: int = 1800):
    """定期清理 RESULT_TTL_HOURS 之前的内存条目，避免长期运行内存膨胀。

    与 main.py 的 startup 一起 asyncio.create_task 启动。
    """
    cutoff_ttl = RESULT_TTL_HOURS * 3600
    while True:
        try:
            now = time.time()
            stale = [
                bid for bid, s in _batch_status.items()
                if (now - s.get("created_ts", now)) > cutoff_ttl
            ]
            for bid in stale:
                _batch_status.pop(bid, None)
            if stale:
                print(f"[archive_detect] GC 清理了 {len(stale)} 个过期批次")
        except Exception as e:
            print(f"[archive_detect] GC 异常（忽略）: {e}")
        await asyncio.sleep(interval_seconds)
