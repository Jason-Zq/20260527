"""文件留底业务审核并发压测脚本(冒烟级别)。

需要先启动后端:
  cd e:/qoderproject/20260527/backend
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000

然后跑:
  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_archive_detect_concurrent.py

验证目标:
- N 个 batch 并发提交,所有 submit 都在 2s 内返回 batch_id
- 同时调 /api/clients,P95 < 1s(不被 OCR 队列拖住)
- /admin/queue-stats 显示 queue_depth 增长后逐步消化
- 没有 5xx;过载时收到 429 + retry_after
"""
import sys
import os
import asyncio
import time
import json
import httpx

BACKEND = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
CONCURRENCY = 5         # 并发批次数
FILES_PER_BATCH = 3     # 每批文件数
DRY_RUN_SECONDS = 60    # 整个压测时长(秒);超过自动停

# 用 OSS-like 假 URL,实际会失败,但走的是 fetch → error → done 流程,业务侧能验证完整路径
# 注意:本地无可用 URL,需把 url 改成真实可访问的 PDF/PNG 才能完整跑通 OCR/LLM
FAKE_URL = os.getenv("ARCHIVE_DETECT_TEST_URL", "https://example.invalid/test.pdf")


async def _submit(client: httpx.AsyncClient, batch_idx: int) -> dict:
    body = {
        "criteria": "公司留底,关键页齐全",
        "stage": "post_submit",
        "client": {"client_code": f"STRESS_C{batch_idx}", "name": f"压测客户{batch_idx}"},
        "progress": {
            "progress_oid": f"STRESS_P{batch_idx}_{int(time.time())}",
            "handler": "压测",
            "project_name": "stress",
            "progress_name": "并发压测",
        },
        "items": [
            {"file_id": f"S{batch_idx}_F{i}", "filename": f"f{i}.pdf", "url": FAKE_URL}
            for i in range(FILES_PER_BATCH)
        ],
    }
    t0 = time.time()
    try:
        r = await client.post(f"{BACKEND}/api/archive-detect/business/batch", json=body, timeout=20)
        elapsed = time.time() - t0
        return {
            "batch_idx": batch_idx,
            "status_code": r.status_code,
            "elapsed": elapsed,
            "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text,
        }
    except Exception as e:
        return {"batch_idx": batch_idx, "error": type(e).__name__, "msg": str(e), "elapsed": time.time() - t0}


async def _poll_other_endpoints(client: httpx.AsyncClient, results: list, stop_event: asyncio.Event):
    """并发调一个不沾 OCR 的接口,看 P95 是否被拖累。"""
    while not stop_event.is_set():
        t0 = time.time()
        try:
            r = await client.get(f"{BACKEND}/api/clients", timeout=10)
            results.append({"endpoint": "/api/clients", "code": r.status_code, "elapsed": time.time() - t0})
        except Exception as e:
            results.append({"endpoint": "/api/clients", "error": str(e), "elapsed": time.time() - t0})
        await asyncio.sleep(0.5)


async def _watch_queue(client: httpx.AsyncClient, samples: list, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            r = await client.get(f"{BACKEND}/api/archive-detect/admin/queue-stats", timeout=5)
            if r.status_code == 200:
                samples.append({"t": time.time(), **r.json()})
        except Exception:
            pass
        await asyncio.sleep(1.0)


async def main():
    print(f"压测目标: {BACKEND}")
    print(f"并发: {CONCURRENCY} 批,{FILES_PER_BATCH} 文件/批")

    stop_event = asyncio.Event()
    other_results: list = []
    queue_samples: list = []

    async with httpx.AsyncClient() as client:
        # 启动并发提交 + 旁路监控
        submit_task = asyncio.create_task(asyncio.gather(*[
            _submit(client, i) for i in range(CONCURRENCY)
        ]))
        other_task = asyncio.create_task(_poll_other_endpoints(client, other_results, stop_event))
        watch_task = asyncio.create_task(_watch_queue(client, queue_samples, stop_event))

        # 等所有 submit 返回
        submit_results = await submit_task
        # 再观测 DRY_RUN_SECONDS - submit_耗时 秒
        elapsed_after_submit = 0
        while elapsed_after_submit < DRY_RUN_SECONDS:
            await asyncio.sleep(1)
            elapsed_after_submit += 1
            # 如果队列已经空了,提前结束
            if queue_samples and queue_samples[-1]["queue_depth"] == 0 and elapsed_after_submit > 5:
                break

        stop_event.set()
        await other_task
        await watch_task

    # --- 报告 ---
    print("\n=== submit 结果 ===")
    submit_times = [r["elapsed"] for r in submit_results]
    print(f"  submit P50: {sorted(submit_times)[len(submit_times)//2]:.3f}s")
    print(f"  submit max: {max(submit_times):.3f}s")
    print(f"  状态码分布: ", end="")
    by_code: dict = {}
    for r in submit_results:
        c = r.get("status_code") or r.get("error")
        by_code[c] = by_code.get(c, 0) + 1
    print(by_code)
    for r in submit_results[:3]:
        print(f"  sample: idx={r['batch_idx']} code={r.get('status_code')} elapsed={r['elapsed']:.2f}s body={str(r.get('body'))[:200]}")

    print("\n=== /api/clients 旁路调用 ===")
    if other_results:
        ts = [r["elapsed"] for r in other_results if "elapsed" in r]
        if ts:
            ts.sort()
            print(f"  N={len(ts)}, P50={ts[len(ts)//2]:.3f}s, P95={ts[int(len(ts)*0.95)]:.3f}s, max={ts[-1]:.3f}s")
        codes = [r.get("code") for r in other_results if "code" in r]
        from collections import Counter
        print(f"  状态码: {Counter(codes)}")

    print("\n=== queue-stats 轨迹 ===")
    if queue_samples:
        depths = [s["queue_depth"] for s in queue_samples]
        print(f"  深度变化: 起 {depths[0]} → 峰 {max(depths)} → 末 {depths[-1]}")
        print(f"  workers={queue_samples[-1]['workers']}, queue_max={queue_samples[-1]['queue_max']}")
        free_mem = [s.get("free_memory_mb") for s in queue_samples if s.get("free_memory_mb") is not None]
        if free_mem:
            print(f"  可用内存(MB): 最低 {min(free_mem)} 最高 {max(free_mem)}")


if __name__ == "__main__":
    asyncio.run(main())
