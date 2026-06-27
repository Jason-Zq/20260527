"""文件留底检测：编排服务（无数据库版本）。

提交 → in-memory 状态字典 + 文件级队列 → worker 串行/小并发处理
- OCR：进程内单实例 PaddleOCR + threading.Lock 串行(ocr_service.run_ocr),业务流走文件级队列,worker 数即 OCR 并发上限
- LLM：asyncio.Semaphore(3) 限流
- 单文件失败不影响 batch 其他文件
- 内存中保留 6 小时；超过后台 GC 自动清理（防止内存泄露）

NOTE: 重启后所有任务状态丢失（用户需重新提交）。这是刻意去 DB 的代价；
      DB 改造已挂起到后续，archive_detect_crud / migration 008 / models.py 中
      的 ORM 类暂时保留作为占位，本服务当前**不调用**它们。
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
from redactor import redact as _redact_text
from db import archive_detect_crud as crud
import event_service


# ==================== 常量与状态 ====================

MAX_FILES_PER_BATCH = 50
LLM_CONCURRENCY = 3
RESULT_TTL_HOURS = 6                         # 内存结果保留 6 小时

# 业务审核文件级队列(单进程内,串行/小并发消化,防止瞬时 fan-out 打爆内存)
QUEUE_MAX_SIZE = int(os.getenv("ARCHIVE_DETECT_QUEUE_MAX", "200"))   # ≈ 4 个满批次
QUEUE_WORKERS = max(1, int(os.getenv("ARCHIVE_DETECT_WORKERS", "1")))

# 内存态：{batch_id: {batch_id, status, total_files, done_files, user_prompt,
#                    source_kind, files: [...], created_ts}}
_batch_status: dict[str, dict] = {}

_OCR_LOCK = asyncio.Lock()
_LLM_SEMAPHORE = asyncio.Semaphore(LLM_CONCURRENCY)

# 文件级队列。每项 = (batch_id, idx, plan, criteria, stage)。
# 队列实例必须延迟到 startup 时才创建,确保绑定到运行中的事件循环。
_FILE_QUEUE: asyncio.Queue | None = None
_workers: list[asyncio.Task] = []

# 每个 batch 待处理的 new 文件计数 + done 事件。orchestrator 阻塞在 event 上等所有 new 项被 worker 消化。
_batch_pending: dict[str, int] = {}
_batch_done_event: dict[str, asyncio.Event] = {}


class QueueFullError(Exception):
    """提交时队列水位超限,业务方应稍后重试。"""
    def __init__(self, queue_depth: int, queue_max: int, retry_after: int = 60):
        super().__init__(
            f"任务队列已满 (depth={queue_depth}, max={queue_max}),请 {retry_after} 秒后重试"
        )
        self.queue_depth = queue_depth
        self.queue_max = queue_max
        self.retry_after = retry_after


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
        return "连接超时,地址可能无法访问"
    if isinstance(exc, httpx.ReadTimeout):
        return "下载超时（>5 分钟）,文件过大或网络太慢"
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
        "overall_verdict": None,
        "overall_score": None,
        "overall_reason": None,
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
                "verdict": None,
                "match_score": None,
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

    # DB 双写：create（中间态不写 DB，只写终态，见 _process_one）
    # 失败只 print 不阻断——内存态已建立，任务仍可跑
    file_specs = [
        {
            "source_url": it.get("source_url"),
            "filename": it.get("filename"),
            "mime_type": it.get("mime_type"),
        }
        for it in items
    ]
    try:
        await crud.create_batch_with_files(
            batch_id=batch_id,
            user_prompt=user_prompt.strip(),
            source_kind=source_kind,
            file_specs=file_specs,
        )
    except Exception as e:
        print(f"[archive_detect:{batch_id}] DB create 失败（继续内存态）: {e}")

    asyncio.create_task(_orchestrate(batch_id, user_prompt.strip(), source_kind, items))
    return batch_id


# ==================== 后台编排 ====================

async def _orchestrate(batch_id: str, user_prompt: str, source_kind: str, items: list[dict]):
    """fan-out 所有文件，等全部结束后置 batch.status=done 并生成总报告。"""
    try:
        tasks = [
            asyncio.create_task(_process_one(batch_id, idx, item, user_prompt, source_kind))
            for idx, item in enumerate(items)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        # ---- 生成批次总报告（混合方案:规则推 verdict/score + LLM 写 reason）----
        state = _batch_status.get(batch_id)
        overall_verdict = "mismatch"
        overall_score = 0
        overall_reason = ""
        if state:
            done_items = [f for f in (state.get("files") or []) if f.get("status") == "done"]
            error_count = sum(1 for f in (state.get("files") or []) if f.get("status") == "error")

            # 1) 规则推 overall_verdict + overall_score
            if not done_items:
                overall_verdict, overall_score = "mismatch", 0
            else:
                scores = [int(f.get("match_score") or 0) for f in done_items]
                avg = round(sum(scores) / len(scores))
                if avg >= 80:
                    overall_verdict = "match"
                elif avg >= 50:
                    overall_verdict = "partial"
                else:
                    overall_verdict = "mismatch"
                overall_score = avg

            # 2) LLM 生成 overall_reason，失败兜底为规则文本
            try:
                files_brief = [
                    {
                        "filename": f.get("filename"),
                        "verdict": f.get("verdict"),
                        "match_score": f.get("match_score"),
                        "doc_category": f.get("doc_category"),
                        "reason": (f.get("reason") or "")[:80],
                        "key_points": (f.get("key_points") or [])[:3],
                    }
                    for f in done_items
                ]
                async with _LLM_SEMAPHORE:
                    overall_reason = await asyncio.to_thread(
                        llm_service.summarize_batch,
                        files_brief, user_prompt, overall_verdict, overall_score,
                    )
                overall_reason = redactor.redact(overall_reason or "")
            except Exception as e:
                print(f"[archive_detect:{batch_id}] LLM summarize_batch 失败,用规则文本兜底: {e}")
                cnt_m = sum(1 for f in done_items if f.get("verdict") == "match")
                cnt_p = sum(1 for f in done_items if f.get("verdict") == "partial")
                cnt_x = sum(1 for f in done_items if f.get("verdict") == "mismatch")
                overall_reason = f"共 {len(done_items)} 个文件,{cnt_m} 个符合,{cnt_p} 个部分符合,{cnt_x} 个不符合。"

            if error_count > 0:
                overall_reason = (overall_reason or "").rstrip() + f" 另有 {error_count} 个文件处理失败。"

            # 3) 写内存
            state["overall_verdict"] = overall_verdict
            state["overall_score"] = overall_score
            state["overall_reason"] = overall_reason
            state["status"] = "done"

        # 4) 写 DB:overall + 状态(分两步,失败互不影响)
        try:
            await crud.update_batch_overall(batch_id, overall_verdict, overall_score, overall_reason)
        except Exception as e:
            print(f"[archive_detect:{batch_id}] DB update_batch_overall 失败（忽略）: {e}")
        try:
            await crud.update_batch_status(batch_id, "done")
        except Exception as e:
            print(f"[archive_detect:{batch_id}] DB update_batch_status 失败（忽略）: {e}")


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
                raise ValueError(file_fetcher.get_unsupported_hint(filename))
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
        # OCR 原文也脱敏后才入库（绝不存原文）
        ocr_text_redacted = _redact_text(text)

        # 5) 写回内存态（内存不存 ocr_text，省内存；DB 存）
        #    verdict dict 现含 7 个字段(verdict/match_score/is_archival/confidence/reason/key_points/doc_category),
        #    **verdict 解包即可全部写到内存 file 项。
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
        # DB 双写：终态 done（含脱敏 ocr_text）。失败只 print 不影响内存态
        try:
            await crud.update_file_done(batch_id, idx, {
                "filename": filename,
                "mime_type": mime_type,
                "page_count": page_count,
                "char_count": char_count,
                "is_archival": verdict.get("is_archival"),
                "confidence": verdict.get("confidence"),
                "verdict": verdict.get("verdict"),
                "match_score": verdict.get("match_score"),
                "reason": verdict.get("reason"),
                "key_points": verdict.get("key_points"),
                "doc_category": verdict.get("doc_category"),
                "ocr_text": ocr_text_redacted,
                "elapsed_sec": elapsed,
            })
        except Exception as e:
            print(f"[archive_detect:{batch_id}:{idx}] DB update_file_done 失败（忽略）: {e}")

    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        msg = str(e) or e.__class__.__name__
        _set_file_state(batch_id, idx, status="error", error_msg=msg, elapsed_sec=elapsed,
                        filename=filename or None)
        # DB 双写：终态 error。失败只 print
        try:
            await crud.update_file_error(batch_id, idx, msg, elapsed, filename or None)
        except Exception as e2:
            print(f"[archive_detect:{batch_id}:{idx}] DB update_file_error 失败（忽略）: {e2}")
    finally:
        # done 计数（成功/失败都算）—— DB 双写用返回值回填内存保证一致
        try:
            new_done = await crud.bump_done_count(batch_id)
            state = _batch_status.get(batch_id)
            if state:
                state["done_files"] = new_done
        except Exception as e:
            # DB 失败则回退内存自增
            state = _batch_status.get(batch_id)
            if state:
                state["done_files"] = (state.get("done_files") or 0) + 1
            print(f"[archive_detect:{batch_id}] DB bump_done_count 失败（回退内存）: {e}")

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
    """优先内存命中（fast-path，含细粒度中间态）；否则从 DB 回落（重启后恢复）。

    DB 回落只含终态（done/error），中间态（fetching/ocr/llm）丢失——重启后任务本就要重提。
    ocr_text 在 DB 层已 defer，不会拉大文本。
    """
    mem = _batch_status.get(batch_id)
    if mem:
        return mem
    return await crud.get_batch(batch_id)


async def list_history(limit: int = 200) -> list[dict]:
    """历史 batch 列表（不含 files 详情）。"""
    return await crud.list_batches(limit=limit)


async def delete_batch(batch_id: str) -> bool:
    """删除一条历史：清内存 + 删 DB（CASCADE 连带删 files）。"""
    _batch_status.pop(batch_id, None)
    return await crud.delete_batch(batch_id)


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


# ==================== 业务接口编排(阶段三) ====================
# 与匿名 upload/urls 的区别:
#   - 增量复用:同 (progress_id, file_id) 命中历史 done 记录 → 跳 OCR/LLM
#   - 业务字段持久化:client/progress 实体表,file 记录带 progress_id/file_id/version
#   - 异步处理只跑 new 项,reused 项在 submit 阶段就 done
#   - 总报告生成同 _orchestrate(规则推 verdict/score + LLM 写 reason)


# ==================== 进程内文件级队列 / Worker ====================

async def start_workers() -> None:
    """启动后台 worker 协程。由 FastAPI startup 调用一次。"""
    global _FILE_QUEUE, _workers
    if _FILE_QUEUE is not None:
        return  # 幂等
    _FILE_QUEUE = asyncio.Queue(maxsize=QUEUE_MAX_SIZE)
    for i in range(QUEUE_WORKERS):
        t = asyncio.create_task(_queue_worker(i), name=f"archive-detect-worker-{i}")
        _workers.append(t)
    print(f"[archive_detect] 启动 {QUEUE_WORKERS} 个 worker, 队列上限={QUEUE_MAX_SIZE}")


async def stop_workers() -> None:
    """关停 worker。由 FastAPI shutdown 调用,优雅退出。"""
    for t in _workers:
        t.cancel()
    for t in _workers:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    _workers.clear()


async def _queue_worker(worker_id: int) -> None:
    """worker 主循环:阻塞拉取一项,串行执行 _process_one_business,完成后递减 batch 计数。"""
    assert _FILE_QUEUE is not None
    while True:
        try:
            item = await _FILE_QUEUE.get()
        except asyncio.CancelledError:
            return
        batch_id, idx, plan, criteria, stage = item
        try:
            await _process_one_business(batch_id, idx, plan, criteria, stage)
        except Exception as e:
            # _process_one_business 内部已经处理大部分异常并写 error 态,这里兜底 print
            print(f"[archive_detect_worker:{worker_id}] batch={batch_id} idx={idx} 处理异常: {e}")
            event_service.log_event(
                event_service.CRITICAL,
                event_service.CATEGORY_WORKER_CRASH,
                f"worker {worker_id} 处理 batch={batch_id} idx={idx} 抛出未捕获异常",
                context={
                    "worker_id": worker_id,
                    "batch_id": batch_id,
                    "idx": idx,
                    "error_class": e.__class__.__name__,
                    "error_msg": str(e)[:300],
                },
            )
        finally:
            _FILE_QUEUE.task_done()
            remaining = _batch_pending.get(batch_id)
            if remaining is None:
                continue
            remaining -= 1
            if remaining <= 0:
                _batch_pending.pop(batch_id, None)
                ev = _batch_done_event.pop(batch_id, None)
                if ev is not None:
                    ev.set()
            else:
                _batch_pending[batch_id] = remaining


def queue_stats() -> dict:
    """admin 监控接口使用。"""
    depth = _FILE_QUEUE.qsize() if _FILE_QUEUE is not None else 0
    return {
        "queue_depth": depth,
        "queue_max": QUEUE_MAX_SIZE,
        "workers": QUEUE_WORKERS,
        "in_flight_batches": len(_batch_pending),
        "llm_semaphore_avail": _LLM_SEMAPHORE._value,   # 私有属性,只读监控可接受
    }


async def submit_business_batch(
    *,
    criteria: str,
    stage: str = "post_submit",
    client_payload: dict,    # {client_code, name}
    progress_payload: dict,  # {progress_oid, handler, project_*, progress_name}
    items: list,             # [{file_id, filename, url?, local_path?}]
) -> dict:
    """业务接口入口:upsert client/progress + 增量预判 + 创建 batch + 启 orchestrator。

    stage: pre_submit(递交前) | post_submit(递交后),透传给 LLM 用作分类阶段感知。
    返回 {batch_id, progress_id, total_files, reused_count, new_count}。
    """
    # 1) 基本校验
    if not criteria or not criteria.strip():
        raise ValueError("criteria 不能为空")
    if stage not in ("pre_submit", "post_submit"):
        raise ValueError(f"非法 stage: {stage} (仅支持 pre_submit / post_submit)")
    if not client_payload or not client_payload.get("client_code") or not client_payload.get("name"):
        raise ValueError("client.client_code 和 client.name 必填")
    if not progress_payload or not progress_payload.get("progress_oid"):
        raise ValueError("progress.progress_oid 必填")
    if not items:
        raise ValueError("文件列表为空")
    if len(items) > MAX_FILES_PER_BATCH:
        raise ValueError(f"单次最多 {MAX_FILES_PER_BATCH} 个文件,收到 {len(items)} 个")

    # 校验 file_id 唯一性 + 必填
    seen_ids = set()
    for i, it in enumerate(items):
        fid = it.get("file_id")
        if not fid:
            raise ValueError(f"第 {i+1} 个文件缺少 file_id")
        if fid in seen_ids:
            raise ValueError(f"重复的 file_id: {fid}")
        seen_ids.add(fid)

    # 2) upsert client / progress
    client_code = client_payload["client_code"].strip()
    client_name = client_payload["name"].strip()
    client_id = await crud.upsert_client_by_code(client_code, client_name)

    progress = await crud.upsert_progress(
        client_id=client_id,
        progress_oid=progress_payload["progress_oid"].strip(),
        handler=(progress_payload.get("handler") or None),
        project_name=(progress_payload.get("project_name") or None),
        project_code=(progress_payload.get("project_code") or None),
        project_detail_name=(progress_payload.get("project_detail_name") or None),
        project_detail_code=(progress_payload.get("project_detail_code") or None),
        progress_name=(progress_payload.get("progress_name") or None),
    )
    progress_id = progress["id"]

    # 3) 增量预判:一次 SQL 批量查所有 file_id 的最新 done 记录
    file_ids = [it["file_id"] for it in items]
    reuse_map = await crud.find_latest_done_files_bulk(progress_id, file_ids)
    items_plan = []
    for it in items:
        existing = reuse_map.get(it["file_id"])
        items_plan.append({
            "file_id": it["file_id"],
            "filename": it.get("filename"),
            "source_url": it.get("url") or it.get("source_url"),
            "local_path": it.get("local_path"),
            "reuse_from": existing,
            "version": (existing["version"] if existing else 1),
        })

    # 3.5) 水位线检查:还要进队列的 new 项数 + 当前队列深度若超上限,直接 429,
    #     避免业务方瞬时打爆。reuse 项不消耗队列容量。
    new_count = sum(1 for p in items_plan if not p.get("reuse_from"))
    if new_count > 0 and _FILE_QUEUE is not None:
        depth = _FILE_QUEUE.qsize()
        if depth + new_count > QUEUE_MAX_SIZE:
            event_service.log_event(
                event_service.WARN,
                event_service.CATEGORY_BATCH_QUEUE_FULL,
                f"队列水位已满,拒绝新批次(深度={depth}+{new_count} > 上限={QUEUE_MAX_SIZE})",
                context={
                    "client_code": client_code,
                    "progress_oid": progress_payload.get("progress_oid"),
                    "depth": depth,
                    "new_count": new_count,
                    "queue_max": QUEUE_MAX_SIZE,
                },
            )
            raise QueueFullError(queue_depth=depth, queue_max=QUEUE_MAX_SIZE)

    # 4) 生成 batch_id + 创建 DB 记录(含 reuse 项直接 done)
    batch_id = gen_batch_id()
    counts = await crud.create_business_batch_with_files(
        batch_id=batch_id,
        user_prompt=criteria.strip(),
        progress_id=progress_id,
        items_plan=items_plan,
    )

    # 5) 内存态(供前端 fast-path 轮询)
    _batch_status[batch_id] = {
        "batch_id": batch_id,
        "user_prompt": criteria.strip(),
        "criteria": criteria.strip(),
        "stage": stage,
        "source_kind": "batch",
        "total_files": len(items_plan),
        "done_files": counts["reused_count"],   # reuse 项已 done
        "status": "running",
        "error": None,
        "overall_verdict": None,
        "overall_score": None,
        "overall_reason": None,
        "client": {"id": client_id, "client_code": client_code, "name": client_name},
        "progress": progress,
        "reused_count": counts["reused_count"],
        "new_count": counts["new_count"],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_ts": time.time(),
        "files": [
            _build_business_mem_file(i, plan, counts.get("idx_to_id", {}).get(i))
            for i, plan in enumerate(items_plan)
        ],
    }

    # 6) 把 new 项放进文件级队列;水位检查在前面已经做过,这里 put 不会阻塞超过短瞬。
    new_items_with_idx = [
        (i, plan) for i, plan in enumerate(items_plan)
        if not plan.get("reuse_from")
    ]
    if new_items_with_idx:
        assert _FILE_QUEUE is not None, "worker 未启动,start_workers() 必须在 startup 钩子里调"
        _batch_pending[batch_id] = len(new_items_with_idx)
        _batch_done_event[batch_id] = asyncio.Event()
        for idx, plan in new_items_with_idx:
            await _FILE_QUEUE.put((batch_id, idx, plan, criteria.strip(), stage))
        # 单独起一个 task 等所有 new 项处理完,生成总报告
        asyncio.create_task(_finalize_business_batch(
            batch_id, criteria.strip(), progress_id,
        ))
    else:
        # 全是 reuse,无需进队列,直接生成总报告
        asyncio.create_task(_finalize_business_batch(
            batch_id, criteria.strip(), progress_id,
        ))

    event_service.log_event(
        event_service.INFO,
        event_service.CATEGORY_BATCH_SUBMIT,
        f"接收批次 {batch_id}(共 {len(items_plan)} 文件,复用 {counts['reused_count']},新检 {counts['new_count']})",
        context={
            "batch_id": batch_id,
            "client_code": client_code,
            "progress_id": progress_id,
            "total_files": len(items_plan),
            "reused": counts["reused_count"],
            "new": counts["new_count"],
        },
    )

    return {
        "batch_id": batch_id,
        "progress_id": progress_id,
        "total_files": len(items_plan),
        "reused_count": counts["reused_count"],
        "new_count": counts["new_count"],
        "queue_depth": _FILE_QUEUE.qsize() if _FILE_QUEUE is not None else 0,
    }


def _build_business_mem_file(idx: int, plan: dict, file_db_id: Optional[int] = None) -> dict:
    """构造内存态的单 file dict。reuse 项直接含 verdict 等;new 项 pending。

    file_db_id 是 DB 主键,用于前端"详情"按钮跳转;来自 create_business_batch_with_files 的 idx_to_id 映射。
    """
    base = {
        "id": file_db_id,
        "idx": idx,
        "file_id": plan["file_id"],
        "filename": plan.get("filename"),
        "source_url": plan.get("source_url"),
        "version": plan.get("version") or 1,
        "page_count": None,
        "char_count": None,
        "elapsed_sec": None,
        "error_msg": None,
        "mime_type": None,
    }
    reuse = plan.get("reuse_from")
    if reuse:
        base.update({
            "status": "done",
            "verdict": reuse.get("verdict"),
            "match_score": reuse.get("match_score"),
            "is_archival": reuse.get("is_archival"),
            "confidence": reuse.get("confidence"),
            "reason": reuse.get("reason"),
            "key_points": reuse.get("key_points") or [],
            "doc_category": reuse.get("doc_category"),
            "page_count": reuse.get("page_count"),
            "char_count": reuse.get("char_count"),
            "elapsed_sec": 0.0,
            "is_reused": True,
        })
    else:
        base.update({
            "status": "pending",
            "verdict": None,
            "match_score": None,
            "is_archival": None,
            "confidence": None,
            "reason": None,
            "key_points": [],
            "doc_category": None,
            "is_reused": False,
        })
    return base


async def _finalize_business_batch(
    batch_id: str,
    criteria: str,
    progress_id: int,
):
    """业务模式总报告生成:等所有 new 项被 worker 处理完后,汇总单文件结果写 batch overall。

    与旧 _orchestrate_business 的差异:不再自己 fan-out _process_one_business,
    那一步由 _queue_worker 完成。这里只 await 事件 + 算 overall + 写 DB。
    全 reuse 的批次没有 event(未注册),会立即生成总报告。
    """
    ev = _batch_done_event.get(batch_id)
    if ev is not None:
        try:
            await ev.wait()
        except asyncio.CancelledError:
            pass

    # 生成总报告(逻辑同旧版,取 batch 内所有 done 文件)
    state = _batch_status.get(batch_id)
    overall_verdict = "mismatch"
    overall_score = 0
    overall_reason = ""
    if state:
        done_items = [f for f in (state.get("files") or []) if f.get("status") == "done"]
        error_count = sum(1 for f in (state.get("files") or []) if f.get("status") == "error")

        if not done_items:
            overall_verdict, overall_score = "mismatch", 0
        else:
            scores = [int(f.get("match_score") or 0) for f in done_items]
            avg = round(sum(scores) / len(scores))
            if avg >= 80:
                overall_verdict = "match"
            elif avg >= 50:
                overall_verdict = "partial"
            else:
                overall_verdict = "mismatch"
            overall_score = avg

        try:
            files_brief = [
                {
                    "filename": f.get("filename"),
                    "verdict": f.get("verdict"),
                    "match_score": f.get("match_score"),
                    "doc_category": f.get("doc_category"),
                    "reason": (f.get("reason") or "")[:80],
                    "key_points": (f.get("key_points") or [])[:3],
                }
                for f in done_items
            ]
            async with _LLM_SEMAPHORE:
                overall_reason = await asyncio.to_thread(
                    llm_service.summarize_batch,
                    files_brief, criteria, overall_verdict, overall_score,
                )
            overall_reason = redactor.redact(overall_reason or "")
        except Exception as e:
            print(f"[archive_detect:{batch_id}] LLM summarize_batch 失败,用规则文本兜底: {e}")
            cnt_m = sum(1 for f in done_items if f.get("verdict") == "match")
            cnt_p = sum(1 for f in done_items if f.get("verdict") == "partial")
            cnt_x = sum(1 for f in done_items if f.get("verdict") == "mismatch")
            overall_reason = f"共 {len(done_items)} 个文件,{cnt_m} 个符合,{cnt_p} 个部分符合,{cnt_x} 个不符合。"

        if error_count > 0:
            overall_reason = (overall_reason or "").rstrip() + f" 另有 {error_count} 个文件处理失败。"

        state["overall_verdict"] = overall_verdict
        state["overall_score"] = overall_score
        state["overall_reason"] = overall_reason
        state["status"] = "done"

    try:
        await crud.update_batch_overall(batch_id, overall_verdict, overall_score, overall_reason)
    except Exception as e:
        print(f"[archive_detect:{batch_id}] DB update_batch_overall 失败(忽略): {e}")
    try:
        await crud.update_batch_status(batch_id, "done")
    except Exception as e:
        print(f"[archive_detect:{batch_id}] DB update_batch_status 失败(忽略): {e}")

    # batch.done 事件:汇总此次审核结果
    if state:
        total_files = len(state.get("files") or [])
        error_count = sum(1 for f in (state.get("files") or []) if f.get("status") == "error")
        done_count = sum(1 for f in (state.get("files") or []) if f.get("status") == "done")
        event_service.log_event(
            event_service.INFO,
            event_service.CATEGORY_BATCH_DONE,
            f"批次 {batch_id} 完成({overall_verdict} {overall_score}/100,共 {total_files} 文件,done={done_count},error={error_count})",
            context={
                "batch_id": batch_id,
                "overall_verdict": overall_verdict,
                "overall_score": overall_score,
                "total_files": total_files,
                "done_count": done_count,
                "error_count": error_count,
            },
        )


async def _process_one_business(
    batch_id: str,
    idx: int,
    plan: dict,        # {file_id, filename, source_url, local_path, version}
    criteria: str,
    stage: Optional[str] = None,
):
    """业务模式单文件处理:fetch → ocr → llm → redact → DB 写。

    stage: pre_submit | post_submit,透传给 LLM 用作分类阶段感知。
    与 _process_one 99% 一致;DB 记录的 progress_id/file_id/version 已在 create_business_batch_with_files
    阶段写入,本函数仅 update verdict/match_score/ocr_text 等结果字段。
    """
    t0 = time.time()
    fetched_temp_path = None
    upload_path = plan.get("local_path")
    filename = plan.get("filename") or ""
    mime_type = None
    page_count = char_count = None

    try:
        # 1) 拿到本地文件路径
        source_url = plan.get("source_url")
        if source_url:
            _set_file_state(batch_id, idx, status="fetching")
            if not source_url.strip():
                raise ValueError("文件地址为空")
            try:
                local_path, fname, mtype, refresh_info = await file_fetcher.fetch_url_to_temp_with_refresh(
                    source_url,
                    file_id=plan.get("file_id"),
                )
                if refresh_info:
                    print(f"[archive_detect:{batch_id}:{idx}] URL 已过期,已用 file_id={plan.get('file_id')} 刷新下载地址")
                filename = fname or filename
                mime_type = mtype
            except file_fetcher.FileTooLargeError:
                raise ValueError("文件超过 50MB 上限,无法处理")
            except ValueError as e:
                raise ValueError(f"文件地址无效或刷新失败:{e}")
            except Exception as e:
                msg = _humanize_fetch_error(e)
                raise ValueError(f"无法下载文件:{msg}")
            fetched_temp_path = local_path
            if not file_fetcher.is_supported_extension(filename):
                raise ValueError(file_fetcher.get_unsupported_hint(filename))
        else:
            local_path = upload_path
            if not local_path or not os.path.exists(local_path):
                raise ValueError("上传文件丢失")
            try:
                size = os.path.getsize(local_path)
            except OSError:
                size = 0
            if size > file_fetcher.MAX_DOWNLOAD_BYTES:
                mb = size / 1024 / 1024
                raise ValueError(f"文件体积 {mb:.1f}MB 超过 50MB 上限")

        # 2) OCR(business 走 queue worker,worker 数即 OCR 并发上限;
        #    引擎内部由 ocr_service._OCR_ENGINE_LOCK threading.Lock 保护,跨线程池安全)
        _set_file_state(batch_id, idx, status="ocr", filename=filename, mime_type=mime_type)
        extracted = await text_extractor.extract_text(local_path, mime_type)

        text = extracted.get("text") or ""
        page_count = extracted.get("page_count")
        char_count = extracted.get("char_count")
        if not text.strip():
            raise ValueError("OCR/抽取后无文字")

        # 3) LLM 判定
        _set_file_state(batch_id, idx, status="llm",
                        page_count=page_count, char_count=char_count)
        async with _LLM_SEMAPHORE:
            verdict = await asyncio.to_thread(llm_service.detect_archival, text, criteria, stage)

        verdict = redactor.redact_dict(verdict)
        ocr_text_redacted = _redact_text(text)

        elapsed = round(time.time() - t0, 2)
        _set_file_state(
            batch_id, idx,
            status="done",
            filename=filename,
            mime_type=mime_type,
            page_count=page_count,
            char_count=char_count,
            elapsed_sec=elapsed,
            is_reused=False,
            **verdict,
        )
        try:
            await crud.update_file_done(batch_id, idx, {
                "filename": filename,
                "mime_type": mime_type,
                "page_count": page_count,
                "char_count": char_count,
                "is_archival": verdict.get("is_archival"),
                "confidence": verdict.get("confidence"),
                "verdict": verdict.get("verdict"),
                "match_score": verdict.get("match_score"),
                "reason": verdict.get("reason"),
                "key_points": verdict.get("key_points"),
                "doc_category": verdict.get("doc_category"),
                "ocr_text": ocr_text_redacted,
                "elapsed_sec": elapsed,
            })
        except Exception as e:
            print(f"[archive_detect:{batch_id}:{idx}] DB update_file_done 失败(忽略): {e}")

    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        msg = str(e) or e.__class__.__name__
        _set_file_state(batch_id, idx, status="error", error_msg=msg, elapsed_sec=elapsed,
                        filename=filename or None)
        try:
            await crud.update_file_error(batch_id, idx, msg, elapsed, filename or None)
        except Exception as e2:
            print(f"[archive_detect:{batch_id}:{idx}] DB update_file_error 失败(忽略): {e2}")
        event_service.log_event(
            event_service.WARN,
            event_service.CATEGORY_FILE_FAILED,
            f"批次 {batch_id} 第 {idx} 个文件失败:{msg[:200]}",
            context={
                "batch_id": batch_id,
                "idx": idx,
                "file_id": plan.get("file_id"),
                "filename": filename or None,
                "error_class": e.__class__.__name__,
                "error_msg": msg[:300],
                "elapsed_sec": elapsed,
            },
        )
    finally:
        try:
            new_done = await crud.bump_done_count(batch_id)
            state = _batch_status.get(batch_id)
            if state:
                state["done_files"] = new_done
        except Exception as e:
            state = _batch_status.get(batch_id)
            if state:
                state["done_files"] = (state.get("done_files") or 0) + 1
            print(f"[archive_detect:{batch_id}] DB bump_done_count 失败(回退内存): {e}")

        if fetched_temp_path:
            file_fetcher.cleanup_temp_file(fetched_temp_path)
        if upload_path:
            try:
                if os.path.exists(upload_path):
                    os.remove(upload_path)
            except OSError:
                pass


async def get_business_batch(batch_id: str) -> Optional[dict]:
    """业务接口轮询:优先内存,DB 回落含 client/progress 业务字段透传。"""
    mem = _batch_status.get(batch_id)
    if mem:
        return mem
    return await crud.get_business_batch(batch_id)


# ==================== 重新审核:复用 OCR 文本重新跑 AI ====================

async def submit_recheck_batch(
    *,
    source_batch_id: str,
    criteria: str,
    stage: Optional[str] = None,
) -> dict:
    """基于当前 batch 创建 recheck batch,优先复用 ocr_text,重新跑 AI 和总报告。"""
    if not criteria or not criteria.strip():
        raise ValueError("重新审核的 criteria 不能为空")
    if stage not in (None, "pre_submit", "post_submit"):
        raise ValueError(f"非法 stage: {stage}")

    source = await crud.get_batch_files_for_recheck(source_batch_id)
    if not source:
        raise ValueError(f"原批次 {source_batch_id} 不存在")
    files = source.get("files") or []
    if not files:
        raise ValueError(f"原批次 {source_batch_id} 没有文件")

    new_batch_id = gen_batch_id()
    items_plan = []
    ai_only_count = 0
    ocr_count = 0
    for i, f in enumerate(files):
        needs_ocr = not bool(f.get("ocr_text"))
        if needs_ocr:
            ocr_count += 1
        else:
            ai_only_count += 1
        items_plan.append({
            "source_file_id": f.get("id"),
            "idx": i,
            "file_id": f.get("file_id"),
            "filename": f.get("filename"),
            "source_url": f.get("source_url"),
            "ocr_text": f.get("ocr_text"),
            "needs_ocr": needs_ocr,
            "progress_id": f.get("progress_id"),
            "version": f.get("version") or 1,
            "mime_type": f.get("mime_type"),
        })

    await crud.create_recheck_batch_with_files(
        source_batch=source,
        new_batch_id=new_batch_id,
        criteria=criteria.strip(),
        items_plan=items_plan,
    )

    # 内存态:文件都先 pending,由 _process_one_recheck 逐个置 done/error
    _batch_status[new_batch_id] = {
        "batch_id": new_batch_id,
        "source_batch_id": source_batch_id,
        "user_prompt": criteria.strip(),
        "criteria": criteria.strip(),
        "stage": stage,
        "source_kind": "recheck",
        "total_files": len(items_plan),
        "done_files": 0,
        "status": "running",
        "error": None,
        "overall_verdict": None,
        "overall_score": None,
        "overall_reason": None,
        "client": source.get("client"),
        "progress": source.get("progress"),
        "reused_count": 0,
        "new_count": len(items_plan),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_ts": time.time(),
        "files": [
            {
                "idx": p["idx"],
                "file_id": p.get("file_id"),
                "filename": p.get("filename"),
                "source_url": p.get("source_url"),
                "version": p.get("version") or 1,
                "page_count": None,
                "char_count": None,
                "elapsed_sec": None,
                "error_msg": None,
                "mime_type": p.get("mime_type"),
                "status": "pending",
                "verdict": None,
                "match_score": None,
                "is_archival": None,
                "confidence": None,
                "reason": None,
                "key_points": [],
                "doc_category": None,
                "is_reused": False,
            }
            for p in items_plan
        ],
    }

    asyncio.create_task(_orchestrate_recheck(new_batch_id, criteria.strip(), stage, items_plan))

    mode = "business" if source.get("progress") or source.get("client") else "quick"
    return {
        "batch_id": new_batch_id,
        "source_batch_id": source_batch_id,
        "total_files": len(items_plan),
        "ai_only_count": ai_only_count,
        "ocr_count": ocr_count,
        "mode": mode,
    }


async def _orchestrate_recheck(batch_id: str, criteria: str, stage: Optional[str], items_plan: list[dict]):
    try:
        tasks = [
            asyncio.create_task(_process_one_recheck(batch_id, p["idx"], p, criteria, stage))
            for p in items_plan
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await _finalize_overall_for_batch(batch_id, criteria)


async def _process_one_recheck(
    batch_id: str,
    idx: int,
    item: dict,
    criteria: str,
    stage: Optional[str],
):
    """重新审核单文件:有 ocr_text 则 AI-only;否则尝试用 source_url 重新 OCR。"""
    t0 = time.time()
    fetched_temp_path = None
    filename = item.get("filename") or ""
    mime_type = item.get("mime_type")
    page_count = char_count = None

    try:
        text = item.get("ocr_text") or ""
        if text:
            _set_file_state(batch_id, idx, status="llm")
        else:
            source_url = item.get("source_url")
            if not source_url:
                raise ValueError("缺少 OCR 文本且无可重新下载的 source_url")
            _set_file_state(batch_id, idx, status="fetching")
            try:
                local_path, fname, mtype, refresh_info = await file_fetcher.fetch_url_to_temp_with_refresh(
                    source_url,
                    file_id=plan.get("file_id"),
                )
                if refresh_info:
                    print(f"[archive_detect:{batch_id}:{idx}] URL 已过期,已用 file_id={plan.get('file_id')} 刷新下载地址")
                filename = fname or filename
                mime_type = mtype
            except file_fetcher.FileTooLargeError:
                raise ValueError("文件超过 50MB 上限,无法处理")
            except ValueError as e:
                raise ValueError(f"文件地址无效或刷新失败:{e}")
            except Exception as e:
                msg = _humanize_fetch_error(e)
                raise ValueError(f"无法下载文件:{msg}")
            fetched_temp_path = local_path
            if not file_fetcher.is_supported_extension(filename):
                raise ValueError(file_fetcher.get_unsupported_hint(filename))

            _set_file_state(batch_id, idx, status="ocr", filename=filename, mime_type=mime_type)
            async with _OCR_LOCK:
                extracted = await text_extractor.extract_text(local_path, mime_type)
            text = extracted.get("text") or ""
            page_count = extracted.get("page_count")
            char_count = extracted.get("char_count")
            if not text.strip():
                raise ValueError("OCR/抽取后无文字")
            text = _redact_text(text)
            _set_file_state(batch_id, idx, status="llm", page_count=page_count, char_count=char_count)

        async with _LLM_SEMAPHORE:
            verdict = await asyncio.to_thread(llm_service.detect_archival, text, criteria, stage)
        verdict = redactor.redact_dict(verdict)

        elapsed = round(time.time() - t0, 2)
        _set_file_state(
            batch_id, idx,
            status="done",
            filename=filename,
            mime_type=mime_type,
            page_count=page_count,
            char_count=char_count,
            elapsed_sec=elapsed,
            is_reused=False,
            **verdict,
        )
        try:
            await crud.update_file_done(batch_id, idx, {
                "filename": filename,
                "mime_type": mime_type,
                "page_count": page_count,
                "char_count": char_count,
                "is_archival": verdict.get("is_archival"),
                "confidence": verdict.get("confidence"),
                "verdict": verdict.get("verdict"),
                "match_score": verdict.get("match_score"),
                "reason": verdict.get("reason"),
                "key_points": verdict.get("key_points"),
                "doc_category": verdict.get("doc_category"),
                "ocr_text": text,
                "elapsed_sec": elapsed,
            })
        except Exception as e:
            print(f"[archive_detect:{batch_id}:{idx}] DB recheck update_file_done 失败(忽略): {e}")

    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        msg = str(e) or e.__class__.__name__
        _set_file_state(batch_id, idx, status="error", error_msg=msg, elapsed_sec=elapsed,
                        filename=filename or None)
        try:
            await crud.update_file_error(batch_id, idx, msg, elapsed, filename or None)
        except Exception as e2:
            print(f"[archive_detect:{batch_id}:{idx}] DB recheck update_file_error 失败(忽略): {e2}")
    finally:
        try:
            new_done = await crud.bump_done_count(batch_id)
            state = _batch_status.get(batch_id)
            if state:
                state["done_files"] = new_done
        except Exception as e:
            state = _batch_status.get(batch_id)
            if state:
                state["done_files"] = (state.get("done_files") or 0) + 1
            print(f"[archive_detect:{batch_id}] DB recheck bump_done_count 失败(回退内存): {e}")
        if fetched_temp_path:
            file_fetcher.cleanup_temp_file(fetched_temp_path)


async def _finalize_overall_for_batch(batch_id: str, criteria: str) -> None:
    """为内存态 batch 生成 overall_* 并写 DB。

    recheck 使用;后续可抽给匿名/business 共用。
    """
    state = _batch_status.get(batch_id)
    overall_verdict = "mismatch"
    overall_score = 0
    overall_reason = ""
    if state:
        done_items = [f for f in (state.get("files") or []) if f.get("status") == "done"]
        error_count = sum(1 for f in (state.get("files") or []) if f.get("status") == "error")
        if done_items:
            scores = [int(f.get("match_score") or 0) for f in done_items]
            avg = round(sum(scores) / len(scores))
            if avg >= 80:
                overall_verdict = "match"
            elif avg >= 50:
                overall_verdict = "partial"
            else:
                overall_verdict = "mismatch"
            overall_score = avg
        try:
            files_brief = [
                {
                    "filename": f.get("filename"),
                    "verdict": f.get("verdict"),
                    "match_score": f.get("match_score"),
                    "doc_category": f.get("doc_category"),
                    "reason": (f.get("reason") or "")[:80],
                    "key_points": (f.get("key_points") or [])[:3],
                }
                for f in done_items
            ]
            async with _LLM_SEMAPHORE:
                overall_reason = await asyncio.to_thread(
                    llm_service.summarize_batch,
                    files_brief, criteria, overall_verdict, overall_score,
                )
            overall_reason = redactor.redact(overall_reason or "")
        except Exception as e:
            print(f"[archive_detect:{batch_id}] LLM recheck summarize_batch 失败,用规则文本兜底: {e}")
            cnt_m = sum(1 for f in done_items if f.get("verdict") == "match")
            cnt_p = sum(1 for f in done_items if f.get("verdict") == "partial")
            cnt_x = sum(1 for f in done_items if f.get("verdict") == "mismatch")
            overall_reason = f"共 {len(done_items)} 个文件,{cnt_m} 个符合,{cnt_p} 个部分符合,{cnt_x} 个不符合。"
        if error_count > 0:
            overall_reason = (overall_reason or "").rstrip() + f" 另有 {error_count} 个文件处理失败。"
        state["overall_verdict"] = overall_verdict
        state["overall_score"] = overall_score
        state["overall_reason"] = overall_reason
        state["status"] = "done"

    try:
        await crud.update_batch_overall(batch_id, overall_verdict, overall_score, overall_reason)
    except Exception as e:
        print(f"[archive_detect:{batch_id}] DB recheck update_batch_overall 失败(忽略): {e}")
    try:
        await crud.update_batch_status(batch_id, "done")
    except Exception as e:
        print(f"[archive_detect:{batch_id}] DB recheck update_batch_status 失败(忽略): {e}")
