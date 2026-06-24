"""文件留底检测：编排服务（无数据库版本）。

提交 → in-memory 状态字典 → 异步 fan-out N 个文件并行处理
- OCR：受 ocr_service 全局单引擎天然串行；额外加 _OCR_LOCK 显式串行
- LLM：asyncio.Semaphore(3) 限流
- 单文件失败不影响 batch 其他文件（return_exceptions=True）
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

    # 3) 增量预判:每个 item 查历史 done
    items_plan = []
    for it in items:
        existing = await crud.find_latest_done_file(progress_id, it["file_id"])
        items_plan.append({
            "file_id": it["file_id"],
            "filename": it.get("filename"),
            "source_url": it.get("url") or it.get("source_url"),
            "local_path": it.get("local_path"),
            "reuse_from": existing,
            "version": (existing["version"] if existing else 1),
        })

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
            _build_business_mem_file(i, plan)
            for i, plan in enumerate(items_plan)
        ],
    }

    # 6) 启动异步 orchestrator(只处理 new 项)
    new_items_with_idx = [
        (i, plan) for i, plan in enumerate(items_plan)
        if not plan.get("reuse_from")
    ]
    asyncio.create_task(_orchestrate_business(
        batch_id, criteria.strip(), progress_id, stage, new_items_with_idx,
    ))

    return {
        "batch_id": batch_id,
        "progress_id": progress_id,
        "total_files": len(items_plan),
        "reused_count": counts["reused_count"],
        "new_count": counts["new_count"],
    }


def _build_business_mem_file(idx: int, plan: dict) -> dict:
    """构造内存态的单 file dict。reuse 项直接含 verdict 等;new 项 pending。"""
    base = {
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


async def _orchestrate_business(
    batch_id: str,
    criteria: str,
    progress_id: int,
    stage: str,
    new_items_with_idx: list,
):
    """业务模式异步编排:只跑 new 项 OCR/LLM,reuse 项已在 submit 阶段 done。

    stage: pre_submit | post_submit,透传给 LLM 分类阶段感知。
    new_items_with_idx: [(idx, items_plan_entry), ...] 只含 reuse_from=None 的项。
    """
    try:
        # 若全是 reuse,new_items_with_idx 为空,直接跳到 finally 生成总报告
        if new_items_with_idx:
            tasks = [
                asyncio.create_task(_process_one_business(
                    batch_id, idx, plan, criteria, stage,
                ))
                for idx, plan in new_items_with_idx
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        # 生成总报告(复用现有 _orchestrate finally 的逻辑,但取的是 batch 内所有 done 文件)
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
                local_path, fname, mtype = await file_fetcher.fetch_url_to_temp(source_url)
                filename = fname or filename
                mime_type = mtype
            except file_fetcher.FileTooLargeError:
                raise ValueError("文件超过 50MB 上限,无法处理")
            except ValueError as e:
                raise ValueError(f"文件地址无效:{e}")
            except Exception as e:
                msg = _humanize_fetch_error(e)
                raise ValueError(f"无法下载文件:{msg}")
            fetched_temp_path = local_path
            if not file_fetcher.is_supported_extension(filename):
                raise ValueError(f"不支持的文件类型:{filename}")
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

        # 2) OCR
        _set_file_state(batch_id, idx, status="ocr", filename=filename, mime_type=mime_type)
        async with _OCR_LOCK:
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
