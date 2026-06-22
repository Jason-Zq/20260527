"""文件留底检测：编排服务。

提交 → 落 DB → 异步 fan-out N 个文件并行处理 → 每个文件 OCR + LLM 判定 + 脱敏 → 写库。
- OCR：受 ocr_service 的全局单引擎天然串行；额外加 _OCR_LOCK 显式串行（保证可读）
- LLM：asyncio.Semaphore(3) 限流
- 单文件失败不影响 batch 其他文件（return_exceptions=True）
- 内存态 _batch_status 用于轮询 fast path；进程重启后从 DB hydrate
"""
import os
import asyncio
import time
import secrets
from datetime import datetime
from typing import Optional

import file_fetcher
import text_extractor
import llm_service
import redactor
from db import archive_detect_crud as crud


# ==================== 常量与状态 ====================

MAX_FILES_PER_BATCH = 20
LLM_CONCURRENCY = 3

# 内存态：{batch_id: {batch_id, status, total_files, done_files, user_prompt, source_kind,
#                    files: [ {idx, status, filename, ...}, ... ]}}
_batch_status: dict[str, dict] = {}

# OCR 锁（PaddleOCR 单引擎线程不安全；显式串行）
_OCR_LOCK = asyncio.Lock()
# LLM 限流（用户决策：并发 3）
_LLM_SEMAPHORE = asyncio.Semaphore(LLM_CONCURRENCY)


# ==================== 上传文件落盘目录 ====================

def _upload_temp_dir() -> str:
    """temp/archive_detect/ — 上传文件的暂存目录，处理完后删除。"""
    root = os.path.dirname(os.path.abspath(__file__))
    d = os.path.normpath(os.path.join(root, "..", "temp", "archive_detect"))
    os.makedirs(d, exist_ok=True)
    return d


def gen_batch_id() -> str:
    """YYMMDDHHMMSS_<6 hex>。"""
    return datetime.now().strftime("%y%m%d%H%M%S") + "_" + secrets.token_hex(3)


# ==================== 内存态辅助 ====================

def _set_file_state(batch_id: str, idx: int, **patch) -> None:
    state = _batch_status.get(batch_id)
    if not state:
        return
    files = state.get("files") or []
    for f in files:
        if f.get("idx") == idx:
            f.update(patch)
            return


def _get_status_or_none(batch_id: str) -> Optional[dict]:
    """优先返回内存态。供 main.py 的 GET 接口在内存命中时直接用。"""
    return _batch_status.get(batch_id)


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

    # 构造 DB 写入用的 file_specs
    file_specs = []
    for it in items:
        file_specs.append({
            "source_url": it.get("source_url"),
            "filename": it.get("filename"),
            "mime_type": it.get("mime_type"),
        })
    await crud.create_batch_with_files(
        batch_id=batch_id,
        user_prompt=user_prompt.strip(),
        source_kind=source_kind,
        file_specs=file_specs,
    )

    # 内存态
    _batch_status[batch_id] = {
        "batch_id": batch_id,
        "user_prompt": user_prompt.strip(),
        "source_kind": source_kind,
        "total_files": len(items),
        "done_files": 0,
        "status": "running",
        "error": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        try:
            await crud.update_batch_status(batch_id, "done")
        except Exception as e:
            print(f"[archive_detect] update batch status failed batch={batch_id}: {e}")


async def _process_one(
    batch_id: str,
    idx: int,
    item: dict,
    user_prompt: str,
    source_kind: str,
):
    """单文件流水线：fetch → ocr → llm → redact → 写库。"""
    t0 = time.time()
    fetched_temp_path: Optional[str] = None  # url 模式下需要清理的临时下载文件
    upload_path = item.get("local_path")     # upload 模式下需要清理的暂存文件

    filename = item.get("filename") or ""
    mime_type = item.get("mime_type")
    page_count = char_count = None

    try:
        # 1) 拿到本地文件路径
        if source_kind == "url":
            url = item.get("source_url") or ""
            _set_file_state(batch_id, idx, status="fetching")
            await crud.update_file_status(batch_id, idx, "fetching")

            if not url.strip():
                raise ValueError("URL 为空")
            try:
                local_path, filename, mime_type = await file_fetcher.fetch_url_to_temp(url)
            except file_fetcher.FileTooLargeError as e:
                raise ValueError(f"文件超过 50MB 上限：{e}")
            fetched_temp_path = local_path

            if not file_fetcher.is_supported_extension(filename):
                raise ValueError(f"不支持的文件类型：{filename}")
        else:
            local_path = upload_path
            if not local_path or not os.path.exists(local_path):
                raise ValueError("上传文件丢失")

        # 2) 文本抽取（OCR 锁串行，避免单引擎并发风险）
        _set_file_state(batch_id, idx, status="ocr", filename=filename, mime_type=mime_type)
        await crud.update_file_status(batch_id, idx, "ocr")

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
        await crud.update_file_status(batch_id, idx, "llm")

        async with _LLM_SEMAPHORE:
            verdict = await asyncio.to_thread(llm_service.detect_archival, text, user_prompt)

        # 4) 脱敏（防御层：即使 LLM 没听话也兜底）
        verdict = redactor.redact_dict(verdict)

        # 5) 落库 + 内存态
        elapsed = round(time.time() - t0, 2)
        await crud.update_file_done(batch_id, idx, {
            "filename": filename,
            "mime_type": mime_type,
            "page_count": page_count,
            "char_count": char_count,
            "elapsed_sec": elapsed,
            **verdict,
        })
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
        try:
            await crud.update_file_error(batch_id, idx, msg, elapsed, filename or None)
        except Exception as e2:
            print(f"[archive_detect] update_file_error failed: {e2}")
        _set_file_state(batch_id, idx, status="error", error_msg=msg, elapsed_sec=elapsed,
                        filename=filename or None)
    finally:
        # 增加 done_files（无论成功失败）
        try:
            new_done = await crud.bump_done_count(batch_id)
            state = _batch_status.get(batch_id)
            if state:
                state["done_files"] = new_done
        except Exception:
            pass

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

async def get_batch(batch_id: str) -> Optional[dict]:
    """优先内存命中；否则从 DB hydrate。"""
    mem = _batch_status.get(batch_id)
    if mem:
        return mem
    return await crud.get_batch(batch_id)


async def list_history(limit: int = 200) -> list[dict]:
    return await crud.list_batches(limit=limit)


async def delete_batch(batch_id: str) -> bool:
    """删除 DB 记录 + 内存态。"""
    _batch_status.pop(batch_id, None)
    return await crud.delete_batch(batch_id)
