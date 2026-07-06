"""文件留底检测：编排服务(方案二 2b: DB 队列 + 多进程 worker)。

架构:
- 主进程: 接 HTTP + 写 DB + 启 finalize 轮询 + watchdog 回收死 worker 任务
- Worker 进程 × N: 独立进程,各自 PaddleOCR + LLM 客户端,SKIP LOCKED 抢 DB 任务
- 状态全部落 DB(archive_detect_files),进程重启不丢任务

业务流程:
  POST /business/batch → 写 DB status='pending' → 立即返回 batch_id
  ↓
  Worker 进程 SELECT FOR UPDATE SKIP LOCKED 抢 pending → 处理 → 写 done/error
  ↓
  主进程 _batch_finalize_poll 周期查 batch 全部终态 → 生成 overall + LLM summarize

主进程**不再**做 OCR/LLM 单文件处理;这些都搬到 worker_runner.py。

内存态 _batch_status 仍保留作为前端轮询 fast-path(避免高频查 DB),
但不再做调度依赖。
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

MAX_FILES_PER_BATCH = int(os.getenv("ARCHIVE_DETECT_MAX_FILES_PER_BATCH", "50"))
LLM_CONCURRENCY = 3
# 快速检测 fan-out 下载并发上限:一批最多 50 文件 asyncio.gather 全并发。
# httpx 连接池不设上限,并发保护交给这个信号量,避免瞬间轰爆业务方/OSS。
DOWNLOAD_CONCURRENCY = int(os.getenv("ARCHIVE_DETECT_DOWNLOAD_CONCURRENCY", "10"))
RESULT_TTL_HOURS = 6                         # 内存结果保留 6 小时

# 全局 pending 队列深度(仅供 /admin/queue-stats、/healthz 只读监控展示)
QUEUE_MAX_SIZE = int(os.getenv("ARCHIVE_DETECT_QUEUE_MAX", "200"))

# 内存态:供前端轮询 fast-path(数据全部在 DB,这里只是热缓存)
_batch_status: dict[str, dict] = {}

# LLM 限流:主进程的 finalize 阶段调 summarize_batch 时用;worker 进程不依赖这个
_LLM_SEMAPHORE = asyncio.Semaphore(LLM_CONCURRENCY)

# 下载限流:快速检测 fan-out 的下载环节用,主动排队而非靠连接池满超时
_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

# 主进程的后台协程引用(startup 创建,shutdown cancel)
_finalize_tasks: dict[str, asyncio.Task] = {}      # batch_id → finalize task
_watchdog_task: Optional[asyncio.Task] = None
_should_stop = False

# 批量重判总体的进度(单例,后台管理触发;前端轮询)
_rejudge_progress: dict = {
    "running": False, "total": 0, "done": 0, "failed": 0,
    "started_at": None, "finished_at": None, "verdicts": None,
}

# 批量重审单文件的进度(单例,后台管理触发;前端轮询)
_rerun_batch_progress: dict = {
    "running": False, "total": 0, "done": 0, "failed": 0,
    "started_at": None, "finished_at": None, "verdicts": None,
}


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
        # ---- 生成批次总报告(LLM 综合判定 verdict/score/reason;失败回退规则平均分)----
        state = _batch_status.get(batch_id)
        overall_verdict = "mismatch"
        overall_score = 0
        overall_reason = ""
        if state:
            done_all = [f for f in (state.get("files") or []) if f.get("status") == "done"]
            no_text_count = sum(1 for f in done_all if f.get("verdict") == "no_text")
            done_items = [f for f in done_all if f.get("verdict") != "no_text"]
            error_count = sum(1 for f in (state.get("files") or []) if f.get("status") == "error")

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

            def _rule_avg():
                if not done_items:
                    return "mismatch", 0
                scores = [int(f.get("match_score") or 0) for f in done_items]
                avg = round(sum(scores) / len(scores))
                if avg >= 80:
                    return "match", avg
                elif avg >= 50:
                    return "partial", avg
                return "mismatch", avg

            llm_ok = False
            if done_items:
                # 1) 优先 LLM 综合判定(理解关键件 vs 附件)
                try:
                    async with _LLM_SEMAPHORE:
                        judged = await asyncio.to_thread(
                            llm_service.judge_batch_overall, files_brief, user_prompt, None,
                        )
                    overall_verdict = judged["verdict"]
                    overall_score = judged["score"]
                    overall_reason = redactor.redact(judged.get("reason") or "")
                    llm_ok = True
                except Exception as e:
                    print(f"[archive_detect:{batch_id}] LLM judge_batch_overall 失败,回退规则平均分: {e}")

            # 2) 兜底:规则平均分 + summarize_batch 文本
            if not llm_ok:
                overall_verdict, overall_score = _rule_avg()
                if done_items:
                    try:
                        async with _LLM_SEMAPHORE:
                            overall_reason = await asyncio.to_thread(
                                llm_service.summarize_batch,
                                files_brief, user_prompt, overall_verdict, overall_score,
                            )
                        overall_reason = redactor.redact(overall_reason or "")
                    except Exception as e:
                        print(f"[archive_detect:{batch_id}] LLM summarize_batch 也失败,用规则文本: {e}")
                        cnt_m = sum(1 for f in done_items if f.get("verdict") == "match")
                        cnt_p = sum(1 for f in done_items if f.get("verdict") == "partial")
                        cnt_x = sum(1 for f in done_items if f.get("verdict") == "mismatch")
                        overall_reason = f"共 {len(done_items)} 个文件,{cnt_m} 个符合,{cnt_p} 个部分符合,{cnt_x} 个不符合。"
                elif no_text_count > 0:
                    overall_reason = f"共 {no_text_count} 个文件均无有效文字(图片型/扫描件/空白),无法判定留底符合度。"

            extra = []
            if no_text_count > 0:
                extra.append(f"{no_text_count} 个文件无有效文字(未参与判定)")
            if error_count > 0:
                extra.append(f"{error_count} 个文件处理失败")
            if extra:
                overall_reason = (overall_reason or "").rstrip() + " 另有 " + "、".join(extra) + "。"

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
                async with _DOWNLOAD_SEMAPHORE:
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

        # 2) 文本抽取(ocr_service 内部用 threading.Lock 串行 PaddleOCR,跨线程池安全)
        _set_file_state(batch_id, idx, status="ocr", filename=filename, mime_type=mime_type)
        extracted = await text_extractor.extract_text(local_path, mime_type)

        text = extracted.get("text") or ""
        page_count = extracted.get("page_count")
        char_count = extracted.get("char_count")
        if not text.strip():
            # 无有效文字:不算失败,标 done + verdict=no_text,由总体判定排除在外。
            elapsed = round(time.time() - t0, 2)
            no_text_result = {
                "verdict": "no_text", "match_score": 0, "is_archival": False,
                "confidence": 0, "doc_category": "无文字",
                "reason": "OCR/抽取后无有效文字(可能为图片型/扫描件/空白文档)",
                "key_points": [],
            }
            _set_file_state(
                batch_id, idx, status="done", filename=filename, mime_type=mime_type,
                page_count=page_count, char_count=char_count, elapsed_sec=elapsed,
                **no_text_result,
            )
            try:
                await crud.update_file_done(batch_id, idx, {
                    "filename": filename, "mime_type": mime_type,
                    "page_count": page_count, "char_count": char_count,
                    "ocr_text": "", "elapsed_sec": elapsed, **no_text_result,
                })
            except Exception as e:
                print(f"[archive_detect:{batch_id}:{idx}] DB update_file_done(no_text) 失败（忽略）: {e}")
            return

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


# ==================== 主进程 watchdog + finalize 协程 ====================

# Watchdog 周期(秒):扫超时 lease,把死 worker 的任务回到 pending
WATCHDOG_INTERVAL_SECONDS = 30

# Finalize 轮询周期(秒):查 batch 是否全部终态
FINALIZE_POLL_INTERVAL_SECONDS = 3


async def start_background_tasks() -> None:
    """主进程 startup 调用:
       1. 启动一次性 reclaim,把残留 leased 任务立即回 pending(整机重启场景必需)
       2. 启动 watchdog 协程,后续周期 reclaim
       3. 恢复 status='running' 的 batch 的 finalize 轮询
    """
    global _watchdog_task, _should_stop
    _should_stop = False

    # 启动时主动 reclaim 一次:把上次进程被强杀时残留的 leased 任务立即回 pending,
    # 不用等 10 分钟 lease 自然过期。watchdog 协程接管后续周期性回收。
    try:
        result = await crud.reclaim_expired_leases()
        if result["requeued"] or result["killed"]:
            print(f"[startup] 启动时回收: 回到 pending={result['requeued']}, "
                  f"标记 error={result['killed']}")
    except Exception as e:
        print(f"[startup] 启动 reclaim 异常(忽略,watchdog 会重试): {e}")

    if _watchdog_task is None:
        _watchdog_task = asyncio.create_task(_watchdog_loop(), name="archive-detect-watchdog")
    await _resume_running_batches()
    print(f"[archive_detect] 后台协程启动: watchdog OK,resume {len(_finalize_tasks)} 个 batch")


async def stop_background_tasks() -> None:
    """主进程 shutdown 调用:取消所有后台协程,优雅退出。"""
    global _should_stop, _watchdog_task
    _should_stop = True

    tasks_to_wait = list(_finalize_tasks.values())
    if _watchdog_task:
        tasks_to_wait.append(_watchdog_task)

    for t in tasks_to_wait:
        t.cancel()
    for t in tasks_to_wait:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    _finalize_tasks.clear()
    _watchdog_task = None


async def _watchdog_loop() -> None:
    """周期 reclaim 超时 lease。死 worker 的任务回到 pending,被其他 worker 抢走。"""
    while not _should_stop:
        try:
            result = await crud.reclaim_expired_leases()
            if result["requeued"] or result["killed"]:
                print(f"[watchdog] 回收 {result['requeued']} 任务到 pending,"
                      f"{result['killed']} 任务标记终态 error")
                if result["killed"]:
                    try:
                        event_service.log_event(
                            event_service.ERROR,
                            event_service.CATEGORY_WORKER_CRASH,
                            f"watchdog 检测到 {result['killed']} 个文件超 retry 上限,标记 error",
                            context={"killed_ids": result["killed_ids"]},
                        )
                    except Exception:
                        pass
        except Exception as e:
            print(f"[watchdog] 异常(忽略,下个周期重试): {e}")
        # 用 wait_for 而不是 sleep,让 cancel 立即生效
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return


async def _resume_running_batches() -> None:
    """主进程启动时,把 DB 里 status='running' 的 batch 重新启动 finalize 轮询。
    这样进程重启不会让 batch 永远卡在 running。
    """
    try:
        batch_ids = await crud.list_running_batch_ids()
    except Exception as e:
        print(f"[resume] 查询 running batch 失败: {e}")
        return

    for bid in batch_ids:
        if bid in _finalize_tasks:
            continue   # 已经在跑
        task = asyncio.create_task(_batch_finalize_poll(bid),
                                    name=f"finalize-{bid}")
        _finalize_tasks[bid] = task


async def _batch_finalize_poll(batch_id: str) -> None:
    """周期查 batch 进度,所有文件终态后生成总报告并 update_batch_status('done')。"""
    try:
        while not _should_stop:
            try:
                progress = await crud.get_batch_progress(batch_id)
            except Exception as e:
                print(f"[finalize:{batch_id}] 查询进度异常,等待重试: {e}")
                await asyncio.sleep(FINALIZE_POLL_INTERVAL_SECONDS)
                continue

            if progress["is_complete"]:
                break

            await asyncio.sleep(FINALIZE_POLL_INTERVAL_SECONDS)

        # 全部终态了,生成总报告
        await _generate_batch_overall(batch_id)
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"[finalize:{batch_id}] 异常: {e}")
    finally:
        _finalize_tasks.pop(batch_id, None)


async def _generate_batch_overall(batch_id: str) -> None:
    """所有 file 终态后,根据 file 结果合成 overall_verdict/score/reason,update batch。"""
    meta = await crud.get_batch_meta(batch_id)
    if not meta:
        print(f"[finalize:{batch_id}] batch 元信息不存在,跳过")
        return

    criteria = meta.get("user_prompt") or ""
    stage = meta.get("stage")
    files = await crud.get_batch_files_simple(batch_id)
    done_all = [f for f in files if f.get("status") == "done"]
    # no_text 文件算处理完成,但不参与总体判定
    no_text_count = sum(1 for f in done_all if f.get("verdict") == "no_text")
    done_items = [f for f in done_all if f.get("verdict") != "no_text"]
    error_count = sum(1 for f in files if f.get("status") == "error")

    # 规则平均分(作为 LLM 判定的兜底)
    def _rule_avg():
        if not done_items:
            return "mismatch", 0
        scores = [int(f.get("match_score") or 0) for f in done_items]
        avg = round(sum(scores) / len(scores))
        if avg >= 80:
            return "match", avg
        elif avg >= 50:
            return "partial", avg
        return "mismatch", avg

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

    overall_verdict = overall_score = None
    overall_reason = ""
    if done_items:
        # 优先 LLM 综合判定(理解关键件 vs 附件);失败回退规则平均分 + summarize_batch 文本
        try:
            async with _LLM_SEMAPHORE:
                judged = await asyncio.to_thread(
                    llm_service.judge_batch_overall, files_brief, criteria, stage,
                )
            overall_verdict = judged["verdict"]
            overall_score = judged["score"]
            overall_reason = redactor.redact(judged.get("reason") or "")
        except Exception as e:
            print(f"[finalize:{batch_id}] LLM judge_batch_overall 失败,回退规则平均分: {e}")

    if overall_verdict is None:
        # 兜底:规则平均分 + (可选)summarize_batch 文本
        overall_verdict, overall_score = _rule_avg()
        if done_items:
            try:
                async with _LLM_SEMAPHORE:
                    overall_reason = await asyncio.to_thread(
                        llm_service.summarize_batch,
                        files_brief, criteria, overall_verdict, overall_score,
                    )
                overall_reason = redactor.redact(overall_reason or "")
            except Exception as e:
                print(f"[finalize:{batch_id}] LLM summarize_batch 也失败,用规则文本: {e}")
                cnt_m = sum(1 for f in done_items if f.get("verdict") == "match")
                cnt_p = sum(1 for f in done_items if f.get("verdict") == "partial")
                cnt_x = sum(1 for f in done_items if f.get("verdict") == "mismatch")
                overall_reason = f"共 {len(done_items)} 个文件,{cnt_m} 个符合,{cnt_p} 个部分符合,{cnt_x} 个不符合。"
        elif no_text_count > 0:
            # 没有可参与判定的文件,但全是无文字文件
            overall_reason = f"共 {no_text_count} 个文件均无有效文字(图片型/扫描件/空白),无法判定留底符合度。"

    # no_text / error 提示追加
    extra = []
    if no_text_count > 0:
        extra.append(f"{no_text_count} 个文件无有效文字(未参与判定)")
    if error_count > 0:
        extra.append(f"{error_count} 个文件处理失败")
    if extra:
        overall_reason = (overall_reason or "").rstrip() + " 另有 " + "、".join(extra) + "。"

    # 同步到内存 _batch_status(如果还在)
    state = _batch_status.get(batch_id)
    if state:
        state["overall_verdict"] = overall_verdict
        state["overall_score"] = overall_score
        state["overall_reason"] = overall_reason
        state["done_files"] = len(done_all)
        state["status"] = "done"

    try:
        await crud.update_batch_overall(batch_id, overall_verdict, overall_score, overall_reason)
    except Exception as e:
        print(f"[finalize:{batch_id}] DB update_batch_overall 失败(忽略): {e}")
    # worker 路径不调 bump_done_count,done_files 会停在初始值(reused_count)。
    # 这里用真实 done 数回填,保证前端"进度"列 done_files/total_files 正确。
    try:
        await crud.reset_done_count(batch_id, len(done_all))
    except Exception as e:
        print(f"[finalize:{batch_id}] DB reset_done_count 失败(忽略): {e}")
    try:
        await crud.update_batch_status(batch_id, "done")
    except Exception as e:
        print(f"[finalize:{batch_id}] DB update_batch_status 失败(忽略): {e}")

    try:
        total_files = len(files)
        done_count = len(done_items)
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
    except Exception:
        pass


def get_rejudge_progress() -> dict:
    """返回批量重判进度快照(前端轮询)。"""
    return dict(_rejudge_progress)


async def start_rejudge_overall(verdicts: Optional[list[str]] = None) -> dict:
    """按新规则批量重判总体(只重跑 judge_batch_overall,不碰单文件/不 OCR/不下载)。

    verdicts: 要重判的 overall_verdict 集合,如 ['partial','mismatch'];None/空=所有 done 批次。
    单例:已有重判在跑则拒绝。后台异步执行,前端轮询 get_rejudge_progress。
    """
    if _rejudge_progress["running"]:
        raise ValueError("已有批量重判任务在进行中,请等待其完成")

    batch_ids = await crud.list_batch_ids_for_rejudge(verdicts or [])
    _rejudge_progress.update({
        "running": True, "total": len(batch_ids), "done": 0, "failed": 0,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": None, "verdicts": verdicts or None,
    })
    asyncio.create_task(_run_rejudge_overall(batch_ids))
    return {"total": len(batch_ids), "batch_ids": batch_ids}


async def _run_rejudge_overall(batch_ids: list[str]) -> None:
    """逐个重判(串行,复用 _generate_batch_overall);单次 LLM 调用的并发由内部 _LLM_SEMAPHORE 控。"""
    try:
        for bid in batch_ids:
            if _should_stop:
                break
            try:
                await _generate_batch_overall(bid)
                _rejudge_progress["done"] += 1
            except Exception as e:
                _rejudge_progress["failed"] += 1
                print(f"[rejudge:{bid}] 重判失败(忽略): {e}")
    finally:
        _rejudge_progress["running"] = False
        _rejudge_progress["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_rerun_batch_progress() -> dict:
    """返回批量重审进度快照(前端轮询)。"""
    return dict(_rerun_batch_progress)


async def start_rerun_files_batch(verdicts: Optional[list[str]] = None) -> dict:
    """批量重审单文件:对目标批次逐个原地重跑(force_all,复用 ocr_text,不重新 OCR/下载)。

    每个批次沿用它自己原本的 criteria/stage。verdicts=要重审的 overall 集合;None/空=所有 done。
    单例;后台异步执行,前端轮询 get_rerun_batch_progress。
    """
    if _rerun_batch_progress["running"]:
        raise ValueError("已有批量重审任务在进行中,请等待其完成")
    if _rejudge_progress["running"]:
        raise ValueError("批量重判总体正在进行中,请等待其完成后再批量重审")

    batch_ids = await crud.list_batch_ids_for_rejudge(verdicts or [])
    _rerun_batch_progress.update({
        "running": True, "total": len(batch_ids), "done": 0, "failed": 0,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": None, "verdicts": verdicts or None,
    })
    asyncio.create_task(_run_rerun_files_batch(batch_ids))
    return {"total": len(batch_ids), "batch_ids": batch_ids}


async def _run_rerun_files_batch(batch_ids: list[str]) -> None:
    """逐批触发原地重跑(force_all)。各批沿用自身 criteria/stage;单文件由 worker 队列串行消化。"""
    try:
        for bid in batch_ids:
            if _should_stop:
                break
            try:
                meta = await crud.get_batch_meta(bid)
                if not meta:
                    _rerun_batch_progress["failed"] += 1
                    continue
                criteria = meta.get("user_prompt") or ""
                stage = meta.get("stage")
                if not criteria.strip():
                    _rerun_batch_progress["failed"] += 1
                    print(f"[rerun_batch:{bid}] 原 criteria 为空,跳过")
                    continue
                await rerun_batch_inplace(
                    batch_id=bid, criteria=criteria, stage=stage, force_all=True,
                )
                _rerun_batch_progress["done"] += 1
            except Exception as e:
                _rerun_batch_progress["failed"] += 1
                print(f"[rerun_batch:{bid}] 批量重审触发失败(忽略): {e}")
    finally:
        _rerun_batch_progress["running"] = False
        _rerun_batch_progress["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def queue_stats() -> dict:
    """admin 监控接口使用。从 DB 查 pending 数(不再是内存队列)。

    注意: 这个函数同步签名,返回的不包含 DB pending count(避免阻塞)。
    完整 stats 走 queue_stats_async。
    """
    return {
        "queue_max": QUEUE_MAX_SIZE,
        "in_flight_batches": len(_finalize_tasks),
        "llm_semaphore_avail": _LLM_SEMAPHORE._value,
    }


async def queue_stats_async() -> dict:
    """完整版 stats,含 DB pending 数。/admin/queue-stats 路由用这个。"""
    pending = 0
    try:
        pending = await crud.count_pending_files()
    except Exception as e:
        print(f"[queue_stats] DB count 失败: {e}")
    return {
        "queue_depth": pending,
        "queue_max": QUEUE_MAX_SIZE,
        "workers": "see-systemd-status",   # worker 是独立进程,不在主进程统计内
        "in_flight_batches": len(_finalize_tasks),
        "llm_semaphore_avail": _LLM_SEMAPHORE._value,
    }


# ==================== 业务接口:提交批次 ====================
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

    # 4) 生成 batch_id + 创建 DB 记录(含 reuse 项直接 done,new 项 status='pending')
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
        "done_files": counts["reused_count"],
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

    # 6) 启动 finalize 轮询协程(等所有 worker 处理完,生成 overall)
    #    Worker 是独立进程,会自动 SELECT FOR UPDATE SKIP LOCKED 抢 pending 任务
    if counts["new_count"] > 0 or counts["reused_count"] > 0:
        task = asyncio.create_task(_batch_finalize_poll(batch_id),
                                    name=f"finalize-{batch_id}")
        _finalize_tasks[batch_id] = task

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
        "queue_depth": 0,   # worker 进程独立,这里无内存队列
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


async def get_business_batch(batch_id: str) -> Optional[dict]:
    """业务接口轮询。方案二 2b 架构下，worker 进程直接写 DB，
    文件级状态在 DB;主进程的 _batch_status 仅是 submit 时的 snapshot。
    所以必须查 DB 获得实时文件状态。
    """
    db_data = await crud.get_business_batch(batch_id)
    if db_data:
        return db_data
    # DB 里也没有，可能是刚 submit 还没 commit 完；用内存兜底
    return _batch_status.get(batch_id)


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
        stage=stage,
    )

    # 内存态:文件都先 pending,worker 进程逐个置 done/error(前端仍走 DB 轮询)
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

    # 交给 worker DB 队列串行消化(不再主进程 fan-out),启动 finalize 轮询等全部终态生成总报告
    task = asyncio.create_task(_batch_finalize_poll(new_batch_id),
                               name=f"finalize-{new_batch_id}")
    _finalize_tasks[new_batch_id] = task

    mode = "business" if source.get("progress") or source.get("client") else "quick"
    return {
        "batch_id": new_batch_id,
        "source_batch_id": source_batch_id,
        "total_files": len(items_plan),
        "ai_only_count": ai_only_count,
        "ocr_count": ocr_count,
        "mode": mode,
    }


async def rerun_batch_inplace(
    *,
    batch_id: str,
    criteria: str,
    stage: Optional[str] = None,
    force_all: bool = False,
) -> dict:
    """原地重跑批次:复用已有结果,只补跑缺失的。

    Args:
        force_all: True = 无视已有 AI 结果,全部用新 criteria 重跑 AI。
                   False = 有 AI 结果的跳过,只补跑缺失的。
    """
    if not criteria or not criteria.strip():
        raise ValueError("判定提示词不能为空")
    if stage not in (None, "pre_submit", "post_submit"):
        raise ValueError(f"非法 stage: {stage}")

    source = await crud.get_batch_files_for_recheck(batch_id)
    if not source:
        raise ValueError(f"批次 {batch_id} 不存在")
    files = source.get("files") or []
    if not files:
        raise ValueError(f"批次 {batch_id} 没有文件")

    # 筛选要重跑的文件
    items_plan = []
    ai_only_count = 0
    ocr_count = 0
    skipped_count = 0
    for f in files:
        has_ocr = bool(f.get("ocr_text"))
        has_ai = bool(f.get("verdict")) and f.get("status") == "done"

        if has_ai and not force_all:
            skipped_count += 1
            continue

        needs_ocr = not has_ocr
        if needs_ocr:
            ocr_count += 1
        else:
            ai_only_count += 1
        items_plan.append({
            "source_file_id": f.get("id"),
            "idx": f.get("idx"),
            "file_id": f.get("file_id"),
            "filename": f.get("filename"),
            "source_url": f.get("source_url"),
            "ocr_text": f.get("ocr_text"),
            "needs_ocr": needs_ocr,
            "progress_id": f.get("progress_id"),
            "version": f.get("version") or 1,
            "mime_type": f.get("mime_type"),
        })

    if not items_plan:
        return {
            "batch_id": batch_id,
            "total_files": len(files),
            "ai_only_count": 0,
            "ocr_count": 0,
            "skipped_count": skipped_count,
            "mode": "no-op",
        }

    done_kept = len(files) - len(items_plan)
    # 重置选中文件为 pending(有 ocr_text 的写进 reuse_ocr_text 供 worker 跳过 OCR)+
    # 批次回 running、更新 criteria/stage、done_files 重置为保留数。一次事务完成。
    await crud.requeue_files_for_rerun(
        batch_id=batch_id,
        criteria=criteria.strip(),
        stage=stage,
        rerun_items=[{"idx": p["idx"], "ocr_text": p.get("ocr_text")} for p in items_plan],
        done_kept=done_kept,
    )

    # 内存态失效(历史批次重跑,前端走 DB 轮询);清掉旧缓存避免 fast-path 返回陈旧结果
    _batch_status.pop(batch_id, None)

    # 交给 worker DB 队列串行消化,启动 finalize 轮询等全部终态生成总报告
    if batch_id not in _finalize_tasks:
        task = asyncio.create_task(_batch_finalize_poll(batch_id),
                                   name=f"finalize-{batch_id}")
        _finalize_tasks[batch_id] = task

    mode = "business" if source.get("progress") or source.get("client") else "quick"
    return {
        "batch_id": batch_id,
        "total_files": len(files),
        "ai_only_count": ai_only_count,
        "ocr_count": ocr_count,
        "skipped_count": skipped_count,
        "mode": mode,
    }
