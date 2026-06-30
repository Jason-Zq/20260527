"""archive_detect worker 进程入口。

设计：每个 worker 是一个独立进程，独立加载 RapidOCR，独立调 LLM。
通过 DB SELECT FOR UPDATE SKIP LOCKED 抢任务，处理完写 DB。

启动方式:
    python -m worker_runner --worker-id worker-1

systemd 模板:
    doc-review-worker@1.service → ExecStart=... python -m worker_runner --worker-id worker-%i

行为:
- SIGTERM 优雅退出: 不再抢新任务,等当前文件处理完(最长 5 分钟)再退
- DB 暂时挂掉: 等待 5 秒后重试,不退出(让 systemd 不要触发 restart)
- 单文件处理异常: 内部 catch,update_file_error,继续下一个文件

不做的事:
- 不维护内存状态 _batch_status (那是主进程的事)
- 不调 bump_done_count (主进程的 finalize 轮询查 batch_progress)
- 不写 _set_file_state (那是内存态;改用 update_file_intermediate_status 写 DB)
"""

import os
import sys
import time
import signal
import asyncio
import argparse
from typing import Optional


_should_stop = False


def _setup_signal_handlers():
    """SIGTERM/SIGINT 触发优雅退出。"""
    def _handler(signum, frame):
        global _should_stop
        print(f"[worker_runner] 收到信号 {signum},准备优雅退出...")
        _should_stop = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


async def _process_one_file(task: dict, worker_id: str) -> None:
    """处理单文件:fetch → ocr → llm → 写 DB。
    所有异常 catch 并 release_lease_error,不抛。
    """
    import file_fetcher
    import text_extractor
    import llm_service
    import redactor
    from redactor import redact as _redact_text
    from db import archive_detect_crud as crud
    import event_service

    t0 = time.time()
    file_db_id = task["id"]
    batch_id = task["batch_id"]
    idx = task["idx"]
    source_url = task.get("source_url")
    upload_local_path = task.get("local_path")
    filename = task.get("filename") or ""
    mime_type = task.get("mime_type")
    fetched_temp_path: Optional[str] = None

    try:
        # 1) 拿本地文件路径
        if source_url:
            await crud.update_file_intermediate_status(file_db_id, "fetching")
            if not source_url.strip():
                raise ValueError("文件地址为空")
            try:
                local_path, fname, mtype, refresh_info = await file_fetcher.fetch_url_to_temp_with_refresh(
                    source_url, file_id=task.get("file_id"),
                )
                filename = fname or filename
                mime_type = mtype
                if refresh_info:
                    print(f"[{worker_id}:{batch_id}:{idx}] URL 已刷新")
            except file_fetcher.FileTooLargeError:
                raise ValueError("文件超过 50MB 上限,无法处理")
            except ValueError as e:
                raise ValueError(f"文件地址无效或刷新失败:{e}")
            except Exception as e:
                from archive_detect_service import _humanize_fetch_error
                msg = _humanize_fetch_error(e)
                raise ValueError(f"无法下载文件:{msg}")
            fetched_temp_path = local_path
            if not file_fetcher.is_supported_extension(filename):
                raise ValueError(file_fetcher.get_unsupported_hint(filename))
        elif upload_local_path:
            # upload 模式: 主进程已落盘,worker 进程直读
            local_path = upload_local_path
            if not os.path.exists(local_path):
                raise ValueError("上传文件丢失(可能已被清理或路径错误)")
            try:
                size = os.path.getsize(local_path)
            except OSError:
                size = 0
            if size > file_fetcher.MAX_DOWNLOAD_BYTES:
                mb = size / 1024 / 1024
                raise ValueError(f"文件体积 {mb:.1f}MB 超过 50MB 上限")
        else:
            raise ValueError("文件来源缺失(既无 source_url 也无 local_path)")

        # 2) OCR / 抽取
        await crud.update_file_intermediate_status(file_db_id, "ocr")
        extracted = await text_extractor.extract_text(local_path, mime_type)
        text = extracted.get("text") or ""
        page_count = extracted.get("page_count")
        char_count = extracted.get("char_count")
        if not text.strip():
            raise ValueError("OCR/抽取后无文字")

        # 3) LLM 判定
        await crud.update_file_intermediate_status(file_db_id, "llm")
        # 拿 batch 的 criteria + stage
        batch_meta = await crud.get_batch_meta(batch_id)
        if not batch_meta:
            raise ValueError(f"批次 {batch_id} 元信息已丢失")
        criteria = batch_meta.get("user_prompt") or ""
        # stage 当前 DB 没存,从内存约定走默认 post_submit;后续可以加 batch.stage 字段
        verdict = await asyncio.to_thread(llm_service.detect_archival, text, criteria, "post_submit")
        verdict = redactor.redact_dict(verdict)
        ocr_text_redacted = _redact_text(text)

        # 4) 写终态 done
        elapsed = round(time.time() - t0, 2)
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
        elapsed = round(time.time() - t0, 2)
        msg = str(e) or e.__class__.__name__
        try:
            await crud.update_file_error(batch_id, idx, msg, elapsed, filename or None)
        except Exception as e2:
            print(f"[{worker_id}:{batch_id}:{idx}] DB update_file_error 失败: {e2}")

        try:
            event_service.log_event(
                event_service.WARN,
                event_service.CATEGORY_FILE_FAILED,
                f"批次 {batch_id} 第 {idx} 个文件失败:{msg[:200]}",
                context={
                    "batch_id": batch_id,
                    "idx": idx,
                    "file_id": task.get("file_id"),
                    "filename": filename or None,
                    "error_class": e.__class__.__name__,
                    "error_msg": msg[:300],
                    "elapsed_sec": elapsed,
                    "worker_id": worker_id,
                    "retry_count": task.get("retry_count", 0),
                },
            )
        except Exception:
            pass

    finally:
        # 清理临时文件
        if fetched_temp_path:
            try:
                file_fetcher.cleanup_temp_file(fetched_temp_path)
            except Exception:
                pass
        # upload 模式的本地文件 worker 处理完也要清理(单文件归一个 worker,不会被另一个用)
        if upload_local_path and os.path.exists(upload_local_path):
            try:
                os.remove(upload_local_path)
            except OSError:
                pass


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", required=True, help="worker 标识,如 worker-1")
    args = parser.parse_args()
    worker_id = args.worker_id

    _setup_signal_handlers()

    import llm_service
    import ocr_service
    import event_service
    from db import archive_detect_crud as crud

    # 1. 加载配置
    llm_service.load_config()

    # 2. 预热 RapidOCR(首次启动加载 onnx 模型,顺便检测可用)
    print(f"[{worker_id}] 预热 RapidOCR 引擎...")
    try:
        ocr_service._get_ocr_engine()
        print(f"[{worker_id}] RapidOCR 就绪")
    except Exception as e:
        print(f"[{worker_id}] RapidOCR 初始化失败,继续启动(首次任务会再试): {e}")

    # 3. 启动事件
    try:
        event_service.log_event(
            event_service.INFO,
            "worker.start",
            f"Worker {worker_id} 启动完成",
            context={"worker_id": worker_id, "pid": os.getpid()},
        )
    except Exception:
        pass

    # 4. 主循环
    print(f"[{worker_id}] 主循环开始,PID={os.getpid()}")
    consecutive_empty = 0

    while not _should_stop:
        # 抢任务
        try:
            task = await crud.claim_one_pending_file(worker_id)
        except Exception as e:
            print(f"[{worker_id}] DB 抢任务失败,等 5s 重试: {e}")
            await asyncio.sleep(5)
            continue

        if task is None:
            # 没活,指数退避(0.5 → 1 → 2 → 4 → 5)
            consecutive_empty += 1
            sleep_sec = min(0.5 * (2 ** min(consecutive_empty, 4)), 5)
            await asyncio.sleep(sleep_sec)
            continue

        consecutive_empty = 0
        print(f"[{worker_id}] 抢到任务: file_id={task['id']} batch={task['batch_id']} idx={task['idx']}")
        try:
            await _process_one_file(task, worker_id)
        except Exception as e:
            # _process_one_file 内部已经 catch,这里是兜底
            print(f"[{worker_id}] 处理 {task['id']} 抛出未捕获异常: {e}")

    # 5. 优雅退出
    print(f"[{worker_id}] 退出循环")
    try:
        event_service.log_event(
            event_service.INFO,
            "worker.stop",
            f"Worker {worker_id} 优雅退出",
            context={"worker_id": worker_id},
        )
    except Exception:
        pass


if __name__ == "__main__":
    # 进程级 OpenMP/MKL 线程上限(早于 paddlepaddle 导入)
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")

    asyncio.run(main())
