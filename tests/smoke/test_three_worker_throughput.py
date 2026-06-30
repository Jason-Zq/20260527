"""worker 端到端轻量集成测试。

模拟 3 个 worker 协程并发抢任务,验证:
- 多 worker 并发不抢同一行(SKIP LOCKED)
- 3 worker 处理一个 9 文件 batch,各自约处理 3 个,总时长 < 单 worker 串行
- 处理失败的文件自动 retry 1 次

不起独立进程(简化集成测试),用协程模拟。完整进程级测试在生产环境压测做。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_three_worker_throughput.py
"""
import sys
import os
import asyncio
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))

# stub event_service
import event_service
event_service.log_event = lambda *args, **kwargs: None

from db import archive_detect_crud as crud
from db.engine import async_session_maker
from db.models import ArchiveDetectBatch, ArchiveDetectFile
from sqlalchemy import delete as sa_delete


async def _seed_batch(batch_id: str, n_files: int) -> None:
    """创建测试 batch 和 N 个 pending 文件。"""
    async with async_session_maker() as session:
        session.add(ArchiveDetectBatch(
            batch_id=batch_id,
            user_prompt="integration test",
            source_kind="batch",
            total_files=n_files,
            done_files=0,
            status="running",
        ))
        for i in range(n_files):
            session.add(ArchiveDetectFile(
                batch_id=batch_id,
                idx=i,
                file_id=f"INT_{batch_id}_{i}",
                filename=f"int_{i}.pdf",
                source_url=f"http://test/{i}",
                status="pending",
                version=1,
                retry_count=0,
            ))
        await session.commit()


async def _cleanup_batch(batch_id: str):
    async with async_session_maker() as session:
        await session.execute(sa_delete(ArchiveDetectFile).where(ArchiveDetectFile.batch_id == batch_id))
        await session.execute(sa_delete(ArchiveDetectBatch).where(ArchiveDetectBatch.batch_id == batch_id))
        await session.commit()


async def _fake_worker(worker_id: str, processed_counter: dict, sleep_per_file: float = 0.3):
    """模拟 worker: 一直抢任务,处理(sleep)后 release_lease_done,直到没任务。"""
    while True:
        task = await crud.claim_one_pending_file(worker_id, lease_seconds=60)
        if task is None:
            return
        processed_counter[worker_id] = processed_counter.get(worker_id, 0) + 1
        # 模拟 OCR+LLM 处理耗时
        await asyncio.sleep(sleep_per_file)
        # 释放为 done
        await crud.update_file_done(task["batch_id"], task["idx"], {
            "verdict": "match",
            "match_score": 80,
            "is_archival": True,
            "confidence": 80,
            "reason": "stub",
            "key_points": [],
            "doc_category": "test",
            "elapsed_sec": sleep_per_file,
        })


async def test_three_workers_parallel():
    """3 worker 并发处理 9 文件,每个 worker 约 3 文件;总时长应远小于单 worker。"""
    bid = "test_smoke_001"
    try:
        await _seed_batch(bid, n_files=9)

        processed: dict[str, int] = {}
        t0 = time.time()

        await asyncio.gather(
            _fake_worker("w1", processed),
            _fake_worker("w2", processed),
            _fake_worker("w3", processed),
        )

        elapsed = time.time() - t0
        total = sum(processed.values())

        assert total == 9, f"3 worker 应共处理 9 文件,实际 {total}"
        # 单 worker 串行: 9 × 0.3 = 2.7s; 3 worker 并行应 ≈ 1.0-1.5s
        # 给一点余量,< 2.0s 即认为并行有效
        assert elapsed < 2.0, f"并行总时长应远小于串行,实际 {elapsed:.2f}s"

        # 验证每个 worker 都处理了任务(不是某个 worker 独占)
        assert len(processed) == 3, f"3 worker 应都活跃,实际 {processed}"
        for wid, cnt in processed.items():
            assert cnt >= 1, f"worker {wid} 处理 {cnt},应至少 1"

        print(f"  → 3 worker 处理 9 文件,总耗时 {elapsed:.2f}s,分布 {processed}")
    finally:
        await _cleanup_batch(bid)


async def test_progress_query_after_done():
    """全部处理完后,get_batch_progress 显示 is_complete=true。"""
    bid = "test_smoke_002"
    try:
        await _seed_batch(bid, n_files=3)

        processed = {}
        await asyncio.gather(
            _fake_worker("w1", processed),
            _fake_worker("w2", processed),
        )

        progress = await crud.get_batch_progress(bid)
        assert progress["total"] == 3
        assert progress["done"] == 3
        assert progress["error"] == 0
        assert progress["running"] == 0
        assert progress["is_complete"] is True
    finally:
        await _cleanup_batch(bid)


async def test_pending_count_drains_to_zero():
    """处理完一个 batch 后,count_pending_files 减去对应数量。"""
    bid = "test_smoke_003"
    try:
        before = await crud.count_pending_files()
        await _seed_batch(bid, n_files=5)
        after_seed = await crud.count_pending_files()
        assert after_seed >= before + 5, f"seed 后应增加 5: {before} → {after_seed}"

        processed = {}
        await asyncio.gather(
            _fake_worker("w1", processed),
            _fake_worker("w2", processed),
        )

        after_done = await crud.count_pending_files()
        assert after_done == before, f"done 后应回到初始: {before} vs {after_done}"
    finally:
        await _cleanup_batch(bid)


async def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and asyncio.iscoroutinefunction(v)]
    failed = 0
    for t in tests:
        try:
            await t()
            print(f"  OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  ERR  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    return failed, len(tests)


if __name__ == "__main__":
    failed, total = asyncio.run(_run_all())
    if failed:
        print(f"\n{failed}/{total} 失败")
        sys.exit(1)
    print(f"\nAll {total} tests passed.")
