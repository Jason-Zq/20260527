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

import llm_service
import redactor
from db import archive_detect_crud as crud
from db import prompt_library_crud as prompt_lib
import event_service


def _build_new_criteria_for_batch(ctx: dict) -> str:
    """根据 batch + progress + client 上下文生成新的简化版 criteria。

    与 frontend/src/components/ArchiveDetectEntryPage.vue 的 buildBizCriteria()
    保持语义一致，但不再拼接 project/progress 名称，也不强调金额核对。
    如果该批次没有 client/progress 业务上下文（upload/url 匿名来源），
    返回一条通用新规则 criteria。
    """
    client_code = (ctx.get("client_code") or "").strip()
    client_name = (ctx.get("client_name") or "").strip()
    handler = (ctx.get("handler") or "").strip()
    stage_raw = ctx.get("stage") or "post_submit"
    stage = "递交前" if stage_raw == "pre_submit" else "递交后"

    parts = []
    if client_code:
        parts.append(f"客户代号{client_code}")
    elif client_name:
        parts.append(f"客户「{client_name}」")
    if handler:
        parts.append(f"办理人「{handler}」")

    subject = " / ".join(parts) if parts else "本客户"

    related_names = [n for n in [client_name, handler] if n]
    related_hint = ""
    if related_names:
        related_hint = (
            "\n关联人关键词："
            + "、".join(related_names)
            + "。系统将同时识别上述人名的中文、拼音及英文转写。"
        )

    return (
        f"请按公司文件留底标准，审核此文件是否为 {subject} 在「{stage}」阶段的相关留底文件。"
        "重点判断文件类型、内容完整性和格式规范，而不是严格匹配文件上的姓名（该客户的文件可能属于其配偶/子女/父母）。"
        "不核对具体项目名称、投资金额或转账金额，只看文件是否与公司留底分类体系相关。"
        f"{related_hint}"
    )


def _fallback_new_criteria() -> str:
    """无业务上下文批次（upload/url 匿名来源）的通用新规则 criteria。"""
    return (
        "请按公司文件留底标准，审核此文件是否为公司留底相关文件。"
        "重点判断文件类型、内容完整性和格式规范，不核对具体项目名称、投资金额或转账金额，"
        "只看文件是否与公司留底分类体系相关。"
    )


# ==================== 常量与状态 ====================

MAX_FILES_PER_BATCH = int(os.getenv("ARCHIVE_DETECT_MAX_FILES_PER_BATCH", "50"))
LLM_CONCURRENCY = 3
RESULT_TTL_HOURS = 6                         # 内存结果保留 6 小时

# 全局 pending 队列深度(仅供 /admin/queue-stats、/healthz 只读监控展示)
QUEUE_MAX_SIZE = int(os.getenv("ARCHIVE_DETECT_QUEUE_MAX", "200"))

# 内存态:供前端轮询 fast-path(数据全部在 DB,这里只是热缓存)
_batch_status: dict[str, dict] = {}

# LLM 限流:主进程的 finalize 阶段调 summarize_batch 时用;worker 进程不依赖这个
_LLM_SEMAPHORE = asyncio.Semaphore(LLM_CONCURRENCY)

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

def gen_batch_id() -> str:
    """YYMMDDHHMMSS_<6 hex>。"""
    return datetime.now().strftime("%y%m%d%H%M%S") + "_" + secrets.token_hex(3)


# ==================== 提交入口 ====================

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
#   - 增量复用:同 (progress_id, file_id) 命中历史 done 记录 → 跳 OCR/LLM
#   - 业务字段持久化:client/progress 实体表,file 记录带 progress_id/file_id/version
#   - 异步处理只跑 new 项,reused 项在 submit 阶段就 done
#   - 总报告生成由 _generate_batch_overall 负责(规则推 verdict/score + LLM 写 reason)


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


async def _get_applicable_prompt_row(ctx: dict) -> Optional[dict]:
    """总体1 用:按五元组查提示词库行,仅当开启「应用到总体1」且 prompt2 非空时返回该行。

    只读查询,不建行/不生成 prompt2(那是总体2 路径 `_generate_batch_overall_v2` 的职责);
    任何异常都返回 None,总体1 回退默认判定路径。
    """
    try:
        key = prompt_lib.normalize_prompt_key(
            ctx.get("project_name"), ctx.get("project_code"), ctx.get("project_detail_name"),
            ctx.get("project_detail_code"), ctx.get("progress_name"),
        )
        if not ctx.get("progress_id") or not any(key):
            return None
        row = await prompt_lib.get_prompt_by_key(key)
        if row and row.get("apply_to_overall1") and (row.get("prompt2") or "").strip():
            return row
    except Exception as e:
        print(f"[finalize] 提示词库查询失败(总体1 回退默认判定): {e}")
    return None


async def _generate_batch_overall(batch_id: str) -> None:
    """所有 file 终态后,根据 file 结果合成 overall_verdict/score/reason,update batch。"""
    ctx = await crud.get_batch_context(batch_id)
    if not ctx:
        print(f"[finalize:{batch_id}] batch 元信息不存在,跳过")
        return

    criteria = ctx.get("user_prompt") or ""
    stage = ctx.get("stage")
    client_name = ctx.get("client_name") or None
    handler = ctx.get("handler") or None
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
    # 提示词库「应用到总体1」:该五元组行开启开关且有 prompt2 时,
    # 总体1 改用 prompt1 模板+prompt2 项目留底标准驱动(不再用业务方 criteria)
    prompt_row = await _get_applicable_prompt_row(ctx)
    if done_items:
        # 优先 LLM 综合判定(理解关键件 vs 附件);失败回退规则平均分 + summarize_batch 文本
        try:
            async with _LLM_SEMAPHORE:
                if prompt_row:
                    judged = await asyncio.to_thread(
                        llm_service.judge_batch_overall_v2,
                        files_brief,
                        prompt_row["prompt2"].strip(),
                        judge_template=prompt_row.get("prompt1"),
                        stage=stage,
                        client_name=client_name,
                        handler=handler,
                        operation="judge_batch_overall_std",
                        batch_id=batch_id,
                    )
                else:
                    judged = await asyncio.to_thread(
                        llm_service.judge_batch_overall,
                        files_brief,
                        criteria,
                        stage,
                        client_name=client_name,
                        handler=handler,
                        batch_id=batch_id,
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
                        files_brief,
                        criteria,
                        overall_verdict,
                        overall_score,
                        client_name=client_name,
                        handler=handler,
                        batch_id=batch_id,
                    )
                overall_reason = redactor.redact(overall_reason or "")
            except Exception as e:
                print(f"[finalize:{batch_id}] LLM summarize_batch 也失败,用规则文本: {e}")
                cnt_m = sum(1 for f in done_items if f.get("verdict") == "match")
                cnt_p = sum(1 for f in done_items if f.get("verdict") == "partial")
                cnt_x = sum(1 for f in done_items if f.get("verdict") == "mismatch")
                overall_reason = f"共 {len(done_items)} 个文件,{cnt_m} 个符合,{cnt_p} 个部分符合,{cnt_x} 个不符合。"
        elif no_text_count > 0 or error_count > 0:
            # 没有可参与判定的文件:全是无文字文件 / 检测失败文件(如加密、损坏)
            # 判定为无法判定而非内容不符,措辞上明确区分"检测失败"与"内容不符"
            causes = []
            if no_text_count > 0:
                causes.append(f"{no_text_count} 个文件无有效文字(图片型/扫描件/空白)")
            if error_count > 0:
                causes.append(f"{error_count} 个文件检测失败(如加密、损坏、无法读取)")
            overall_reason = (
                "本批次无可参与判定的文件:" + "、".join(causes)
                + "。此为检测未成功,非文件内容与留底标准不符。"
            )

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
                "overall1_source": "prompt_library" if prompt_row else "default",
                "total_files": total_files,
                "done_count": done_count,
                "error_count": error_count,
            },
        )
    except Exception:
        pass

    # 总体判定2(提示词库项目标准,best-effort):批次终态已提交,任何失败只留 NULL+日志,不影响主流程
    try:
        await _generate_batch_overall_v2(batch_id, ctx, files_brief)
    except Exception as e:
        print(f"[finalize:{batch_id}] 总体判定2失败(忽略,不影响主流程): {e}")
        try:
            event_service.log_event(
                event_service.WARN,
                event_service.CATEGORY_BATCH_OVERALL2,
                f"批次 {batch_id} 总体判定2失败: {e}",
                context={"batch_id": batch_id, "error": str(e)[:500]},
            )
        except Exception:
            pass


# 提示词库五元组 per-key 锁:finalize 只在主进程跑,防同键并发双建行/重复生成 prompt2;
# DB 唯一索引兜底(并发胜者重查),锁只是优化。字典规模受项目×进展组合数约束,不清理也可接受。
_PROMPT_KEY_LOCKS: dict[tuple, asyncio.Lock] = {}


async def _generate_batch_overall_v2(batch_id: str, ctx: dict, files_brief: list) -> None:
    """批次总体判定2(提示词库驱动,best-effort)。

    流程:按项目五元组查提示词库 → 无行则建行(prompt1=代码默认模板) → prompt2 为空则调 LLM
    生成项目专属留底标准并入库 → prompt1(可编辑模板)+prompt2(标准)+文件明细 渲染判定
    → overall_verdict2/score2/reason2 落库。失败不回退规则分,overall_*2 留 NULL。
    """
    key = prompt_lib.normalize_prompt_key(
        ctx.get("project_name"), ctx.get("project_code"), ctx.get("project_detail_name"),
        ctx.get("project_detail_code"), ctx.get("progress_name"),
    )
    if not ctx.get("progress_id") or not any(key):
        print(f"[finalize:{batch_id}] 判定2跳过:无进展包/项目五元组全空(历史 quick 批次)")
        return

    lock = _PROMPT_KEY_LOCKS.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            row, _created = await prompt_lib.get_or_create_prompt(
                key, default_prompt1=llm_service.DEFAULT_JUDGE_OVERALL_TEMPLATE)
            standard = (row.get("prompt2") or "").strip()
            if not standard:
                async with _LLM_SEMAPHORE:
                    standard = await asyncio.to_thread(
                        llm_service.generate_archive_prompt_standard,
                        *key, batch_id=batch_id, client_code=ctx.get("client_code"))
                await prompt_lib.set_prompt2(row["id"], standard)
            async with _LLM_SEMAPHORE:
                result = await asyncio.to_thread(
                    llm_service.judge_batch_overall_v2,
                    files_brief, standard,
                    judge_template=row.get("prompt1"),
                    stage=ctx.get("stage"),
                    client_name=ctx.get("client_name"),
                    handler=ctx.get("handler"),
                    batch_id=batch_id, client_code=ctx.get("client_code"))
        reason2 = redactor.redact(result.get("reason") or "")
        await crud.update_batch_overall2(batch_id, result["verdict"], result["score"], reason2)
        event_service.log_event(
            event_service.INFO,
            event_service.CATEGORY_BATCH_OVERALL2,
            f"批次 {batch_id} 总体判定2完成({result['verdict']} {result['score']}/100)",
            context={"batch_id": batch_id, "overall_verdict2": result["verdict"],
                     "overall_score2": result["score"]},
        )
    finally:
        # 锁不再被持有时摘除,防长生命周期进程缓慢堆积(DB 唯一索引兜底极小窗口的并发)
        if not lock.locked():
            _PROMPT_KEY_LOCKS.pop(key, None)
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


async def start_rerun_files_batch(
    verdicts: Optional[list[str]] = None,
    batch_ids: Optional[list[str]] = None,
    regenerate_criteria: bool = False,
) -> dict:
    """批量重审单文件:对目标批次逐个原地重跑(force_all,复用 ocr_text,不重新 OCR/下载)。

    每个批次沿用它自己原本的 criteria/stage。verdicts=要重审的 overall 集合;None/空=所有 done。
    batch_ids=指定要处理的批次(提供时只处理其中 done 批次,忽略 verdicts)。
    regenerate_criteria=True 时,每批会根据自身 client/progress/stage 重新生成新规则 criteria。
    单例;后台异步执行,前端轮询 get_rerun_batch_progress。
    """
    if _rerun_batch_progress["running"]:
        raise ValueError("已有批量重审任务在进行中,请等待其完成")
    if _rejudge_progress["running"]:
        raise ValueError("批量重判总体正在进行中,请等待其完成后再批量重审")

    target_ids = await crud.list_batch_ids_for_rejudge(
        verdicts=verdicts or None, batch_ids=batch_ids or None
    )
    _rerun_batch_progress.update({
        "running": True, "total": len(target_ids), "done": 0, "failed": 0,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": None, "verdicts": verdicts or None,
    })
    asyncio.create_task(_run_rerun_files_batch(target_ids, regenerate_criteria=regenerate_criteria))
    return {"total": len(target_ids), "batch_ids": target_ids}


async def _run_rerun_files_batch(batch_ids: list[str], regenerate_criteria: bool = False) -> None:
    """逐批触发原地重跑(force_all)。各批沿用自身 criteria/stage(或重新生成);单文件由 worker 队列串行消化。"""
    try:
        for bid in batch_ids:
            if _should_stop:
                break
            try:
                if regenerate_criteria:
                    await rerun_batch_inplace(
                        batch_id=bid, criteria="", stage=None, force_all=True, regenerate_criteria=True,
                    )
                else:
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

    # 2) upsert progress(客户编码/姓名冗余存进展包行,2026-08 起不再写 clients 表)
    client_code = client_payload["client_code"].strip()
    client_name = client_payload["name"].strip()

    progress = await crud.upsert_progress(
        client_code=client_code,
        client_name=client_name,
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
        "client": {"client_code": client_code, "name": client_name},
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
    regenerate_criteria: bool = False,
) -> dict:
    """基于当前 batch 创建 recheck batch,优先复用 ocr_text,重新跑 AI 和总报告。

    regenerate_criteria=True 时,根据原批次 client/progress/stage 重新生成新规则 criteria,
    忽略传入的 criteria 参数。
    """
    if stage not in (None, "pre_submit", "post_submit"):
        raise ValueError(f"非法 stage: {stage}")

    source = await crud.get_batch_files_for_recheck(source_batch_id)
    if not source:
        raise ValueError(f"原批次 {source_batch_id} 不存在")
    files = source.get("files") or []
    if not files:
        raise ValueError(f"原批次 {source_batch_id} 没有文件")

    if regenerate_criteria:
        ctx = await crud.get_batch_context(source_batch_id)
        if ctx and (ctx.get("client_name") or ctx.get("client_code") or ctx.get("handler")):
            criteria = _build_new_criteria_for_batch(ctx)
        else:
            criteria = _fallback_new_criteria()
        stage = ctx.get("stage") or stage or "post_submit"
    else:
        if not criteria or not criteria.strip():
            raise ValueError("重新审核的 criteria 不能为空")
        criteria = criteria.strip()

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
    regenerate_criteria: bool = False,
) -> dict:
    """原地重跑批次:复用已有结果,只补跑缺失的。

    Args:
        force_all: True = 无视已有 AI 结果,全部用新 criteria 重跑 AI。
                   False = 有 AI 结果的跳过,只补跑缺失的。
        regenerate_criteria: True = 根据该批次当前 client/progress/stage 重新生成新规则 criteria,
                             忽略传入的 criteria 参数。
    """
    if stage not in (None, "pre_submit", "post_submit"):
        raise ValueError(f"非法 stage: {stage}")

    if regenerate_criteria:
        ctx = await crud.get_batch_context(batch_id)
        if ctx and (ctx.get("client_name") or ctx.get("client_code") or ctx.get("handler")):
            criteria = _build_new_criteria_for_batch(ctx)
        else:
            criteria = _fallback_new_criteria()
        stage = ctx.get("stage") or stage or "post_submit"
    else:
        if not criteria or not criteria.strip():
            raise ValueError("判定提示词不能为空")
        criteria = criteria.strip()

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
