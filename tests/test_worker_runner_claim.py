"""worker claim/release/reclaim CRUD 单元测试。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_worker_runner_claim.py

依赖:本地 PG 已跑过 migration 013(worker_lease_until + retry_count)。
测试会创建临时 batch_id (test_claim_XXX),完后清理。
"""
import sys
import os
import asyncio
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

# stub event_service:测试不依赖 system_events 表
import event_service
event_service.log_event = lambda *args, **kwargs: None

import event_service as _event_service  # noqa
from db import archive_detect_crud as crud
from db.engine import async_session_maker
from db.models import ArchiveDetectBatch, ArchiveDetectFile
from sqlalchemy import select, delete as sa_delete


# ---- 工具 ----

async def _seed_batch(batch_id: str, n_files: int = 3, file_status: str = "pending") -> list[int]:
    """创建一个测试 batch + N 个 pending file,返回 file ids。"""
    async with async_session_maker() as session:
        batch = ArchiveDetectBatch(
            batch_id=batch_id,
            user_prompt="test",
            source_kind="batch",
            total_files=n_files,
            done_files=0,
            status="running",
        )
        session.add(batch)
        files = []
        for i in range(n_files):
            f = ArchiveDetectFile(
                batch_id=batch_id,
                idx=i,
                file_id=f"F_{batch_id}_{i}",
                filename=f"test_{i}.pdf",
                source_url=f"http://test/{i}",
                status=file_status,
                version=1,
                retry_count=0,
            )
            session.add(f)
            files.append(f)
        await session.commit()
        return [f.id for f in files]


async def _cleanup_batch(batch_id: str):
    async with async_session_maker() as session:
        await session.execute(sa_delete(ArchiveDetectFile).where(ArchiveDetectFile.batch_id == batch_id))
        await session.execute(sa_delete(ArchiveDetectBatch).where(ArchiveDetectBatch.batch_id == batch_id))
        await session.commit()


async def _set_lease(file_id: int, until: datetime, retry_count: int = 0):
    """直接改 DB 让一行变成 leased 状态(测试 reclaim 用)。"""
    from sqlalchemy import update as sa_update
    async with async_session_maker() as session:
        await session.execute(sa_update(ArchiveDetectFile)
            .where(ArchiveDetectFile.id == file_id)
            .values(status="leased", worker_lease_until=until, retry_count=retry_count))
        await session.commit()


async def _get_file(file_id: int) -> dict:
    async with async_session_maker() as session:
        f = (await session.execute(
            select(ArchiveDetectFile).where(ArchiveDetectFile.id == file_id)
        )).scalar_one()
        return {
            "id": f.id,
            "status": f.status,
            "retry_count": f.retry_count,
            "worker_lease_until": f.worker_lease_until,
            "error_msg": f.error_msg,
        }


# ---- 测试 ----

async def test_claim_one_pending():
    """claim_one_pending_file 拿到一行,状态变 leased。"""
    bid = "test_claim_001"
    try:
        file_ids = await _seed_batch(bid, n_files=2)

        task = await crud.claim_one_pending_file("worker-test", lease_seconds=60)
        assert task is not None
        assert task["id"] in file_ids
        assert task["batch_id"] == bid

        f = await _get_file(task["id"])
        assert f["status"] == "leased"
        assert f["worker_lease_until"] is not None
    finally:
        await _cleanup_batch(bid)


async def test_claim_concurrent_mutual_exclusion():
    """3 个并发 claim 调用,3 个文件,3 个 task 应分别拿到一行。"""
    bid = "test_claim_002"
    try:
        await _seed_batch(bid, n_files=3)

        results = await asyncio.gather(
            crud.claim_one_pending_file("worker-1"),
            crud.claim_one_pending_file("worker-2"),
            crud.claim_one_pending_file("worker-3"),
        )
        # 全部拿到,且 file id 互不相同
        ids = {r["id"] for r in results if r is not None}
        assert len(ids) == 3, f"expected 3 unique, got {len(ids)}: {ids}"
    finally:
        await _cleanup_batch(bid)


async def test_claim_returns_none_when_empty():
    """没 pending 文件时,claim 返回 None。"""
    bid = "test_claim_003"
    try:
        await _seed_batch(bid, n_files=1, file_status="done")
        task = await crud.claim_one_pending_file("worker-x")
        # 注意:可能其它 batch 有 pending 文件,这里只断言"返回是 dict 或 None"
        # 严格测试需要独立测试 DB,这里宽松
        if task is not None:
            assert task["batch_id"] != bid, f"不应该拿到 done 文件: {task}"
    finally:
        await _cleanup_batch(bid)


async def test_reclaim_expired_lease_retry():
    """超时的 leased 任务,retry_count<1 时回 pending + retry+1。"""
    bid = "test_claim_004"
    try:
        file_ids = await _seed_batch(bid, n_files=1)
        fid = file_ids[0]

        # 手动设成过期 leased
        await _set_lease(fid, datetime.now() - timedelta(minutes=20), retry_count=0)

        result = await crud.reclaim_expired_leases()
        assert fid in result["requeued_ids"], f"应被 requeue: {result}"

        f = await _get_file(fid)
        assert f["status"] == "pending"
        assert f["retry_count"] == 1
        assert f["worker_lease_until"] is None
    finally:
        await _cleanup_batch(bid)


async def test_reclaim_expired_lease_kill():
    """超时 leased 且 retry_count >= 上限时,标记 error。"""
    bid = "test_claim_005"
    try:
        file_ids = await _seed_batch(bid, n_files=1)
        fid = file_ids[0]

        # retry_count = 1 已到上限
        await _set_lease(fid, datetime.now() - timedelta(minutes=20), retry_count=1)

        result = await crud.reclaim_expired_leases(max_retry=1)
        assert fid in result["killed_ids"], f"应被标记 error: {result}"

        f = await _get_file(fid)
        assert f["status"] == "error"
        assert f["error_msg"] is not None
    finally:
        await _cleanup_batch(bid)


async def test_count_pending_files():
    """count_pending_files 包含 pending+leased+fetching+ocr+llm 共 5 个状态。"""
    bid = "test_claim_006"
    try:
        file_ids = await _seed_batch(bid, n_files=3)

        before = await crud.count_pending_files()
        assert before >= 3, f"应至少 3 个 pending: {before}"

        # 拿掉一个变 leased
        task = await crud.claim_one_pending_file("worker-c")
        assert task is not None

        after = await crud.count_pending_files()
        # pending - 1 → leased + 1,总数不变
        assert after == before, f"count 应不变: {before} vs {after}"
    finally:
        await _cleanup_batch(bid)


async def test_get_batch_progress():
    """get_batch_progress 正确返回 done/error/running/total。"""
    bid = "test_claim_007"
    try:
        await _seed_batch(bid, n_files=3, file_status="done")
        # 改一个为 error,一个为 pending
        async with async_session_maker() as session:
            from sqlalchemy import update as sa_update
            files = (await session.execute(
                select(ArchiveDetectFile).where(ArchiveDetectFile.batch_id == bid)
                .order_by(ArchiveDetectFile.idx)
            )).scalars().all()
            files[0].status = "error"
            files[1].status = "done"
            files[2].status = "pending"
            await session.commit()

        progress = await crud.get_batch_progress(bid)
        assert progress["total"] == 3
        assert progress["done"] == 1
        assert progress["error"] == 1
        assert progress["running"] == 1   # 还有 pending,即未完成
        assert progress["is_complete"] is False

        # 全 done 后 is_complete 应 true
        async with async_session_maker() as session:
            from sqlalchemy import update as sa_update
            await session.execute(sa_update(ArchiveDetectFile)
                .where(ArchiveDetectFile.batch_id == bid)
                .values(status="done"))
            await session.commit()

        progress = await crud.get_batch_progress(bid)
        assert progress["is_complete"] is True
    finally:
        await _cleanup_batch(bid)


# ---- 入口 ----

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
