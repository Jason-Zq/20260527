"""画像导入断点续跑(resume-stale)测试(依赖真实 DB,测后清理)。

进程重启会杀掉 run_import 协程,任务永远卡 running(stale)。恢复机制:
活跃内存登记(mark_task_active)区分真 running 与 stale;resume 模式只跑
未完成文件(pending/error,done 不重跑),计数器从 DB 基线继续。

场景 A: list_unfinished_files 只返回 pending/error;list_running_import_tasks 含 running 任务。
场景 B: 活跃登记防重——已 mark 的任务 run_import 直接跳过(任务状态不变)。
场景 C: resume 端到端(文件全 done,无 LLM/OCR):空未完成清单 -> 收尾合并/推导 -> 标 done,登记释放。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_resume.py
"""
import sys
import os
import asyncio
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete as sa_delete

import profile_import_service
from db import customer_file_crud, profile_crud
from db.engine import async_session_maker
from db.models import CustomerFile, ProfileHousehold, ProfileImportTask


async def _cleanup(task_ids, household_id):
    if task_ids:
        async with async_session_maker() as s:
            await s.execute(sa_delete(ProfileImportTask).where(ProfileImportTask.id.in_(task_ids)))
            await s.commit()
    if household_id is not None:
        async with async_session_maker() as s:
            hh = await s.get(ProfileHousehold, household_id)
            if hh:
                await s.delete(hh)
            await s.commit()


async def _set_file_done(row_id: int):
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, row_id)
        row.status = "done"
        row.ocr_text = "测试OCR文本"
        await session.commit()


async def _make_fixture():
    """建 household + running 任务 + 3 文件(done/error/pending)。"""
    suffix = uuid.uuid4().hex[:12]
    hname = f"测试恢复-{suffix}"
    h = await profile_crud.get_or_create_household(hname)
    task = await customer_file_crud.create_import_task(
        filename=f"testresume-{suffix}.xlsx", client_name=hname,
        client_id=None, total_files=3, household_id=h["id"])
    tid = task["id"]
    await customer_file_crud.upsert_task_files(tid, None, [
        {"file_code": f"testresume-{suffix}-a", "filename": "a.pdf", "client_name": hname},
        {"file_code": f"testresume-{suffix}-b", "filename": "b.pdf", "client_name": hname},
        {"file_code": f"testresume-{suffix}-c", "filename": "c.pdf", "client_name": hname},
    ])
    files, total = await customer_file_crud.list_task_files(tid)
    assert total == 3, files
    fa, fb, fc = files[0], files[1], files[2]
    await _set_file_done(fa["id"])
    await customer_file_crud.mark_file_error(fb["id"], "模拟上次中断前的失败")
    # fc 保持 pending
    return h["id"], tid, (fa, fb, fc)


async def scenario_a_unfinished_and_running_queries():
    """场景 A: 未完成清单只含 pending/error;running 清单含本任务。"""
    household_id, tid, (fa, fb, fc) = None, None, (None, None, None)
    try:
        household_id, tid, (fa, fb, fc) = await _make_fixture()

        unfinished = await customer_file_crud.list_unfinished_files(tid)
        unfinished_ids = {f["id"] for f in unfinished}
        assert unfinished_ids == {fb["id"], fc["id"]}, unfinished_ids

        pending = await customer_file_crud.list_pending_files(tid)
        pending_ids = {f["id"] for f in pending}
        assert pending_ids == {fa["id"], fc["id"]}, pending_ids  # 原语义:pending+done

        running = await customer_file_crud.list_running_import_tasks()
        assert tid in {t["id"] for t in running}, tid

        task = await customer_file_crud.get_import_task(tid)
        assert task["status"] == "running"
        # 计数器基线初始化(resume 模式口径):11 个键齐全且从 DB 取值
        counters = {k: int(task.get(k) or 0) for k in profile_import_service._COUNTER_KEYS}
        assert set(counters) == set(profile_import_service._COUNTER_KEYS)
        assert all(isinstance(v, int) for v in counters.values())
    finally:
        await _cleanup([tid] if tid else [], household_id)


async def scenario_b_active_registry_blocks_duplicate():
    """场景 B: 已 mark 的任务 run_import 跳过(状态仍 running,不重复跑)。"""
    household_id, tid = None, None
    try:
        household_id, tid, _ = await _make_fixture()

        assert profile_import_service.mark_task_active(tid) is True
        assert profile_import_service.mark_task_active(tid) is False  # 二次登记被拒

        await profile_import_service.run_import(tid)  # 应直接返回
        task = await customer_file_crud.get_import_task(tid)
        assert task["status"] == "running", task["status"]  # 没被碰
        assert task["processed_files"] == 0

        profile_import_service.unmark_task_active(tid)
        assert profile_import_service.mark_task_active(tid) is True  # 释放后可再登记
        profile_import_service.unmark_task_active(tid)
    finally:
        profile_import_service.unmark_task_active(tid) if tid else None
        await _cleanup([tid] if tid else [], household_id)


async def scenario_c_resume_finish_empty_unfinished():
    """场景 C: 全 done 的 stale 任务 resume -> 无文件可跑 -> 正常收尾标 done,登记释放。"""
    household_id, tid = None, None
    try:
        household_id, tid, (fa, fb, fc) = await _make_fixture()
        await _set_file_done(fb["id"])
        await _set_file_done(fc["id"])
        # 模拟中断时已有的进度基线
        await customer_file_crud.update_task_progress(tid, processed_files=3, failed_count=1)

        # 端点侧语义:先同步 mark(stale 判定+竞态关闭),再 assume_active 续跑
        assert profile_import_service.mark_task_active(tid) is True
        await profile_import_service.run_import(tid, resume=True, assume_active=True)

        task = await customer_file_crud.get_import_task(tid)
        assert task["status"] == "done", task["status"]
        assert task["processed_files"] == 3, task["processed_files"]  # 基线不归零

        # 登记已在 finally 释放
        assert profile_import_service.mark_task_active(tid) is True
        profile_import_service.unmark_task_active(tid)
    finally:
        profile_import_service.unmark_task_active(tid) if tid else None
        await _cleanup([tid] if tid else [], household_id)


if __name__ == "__main__":
    async def main():
        # 单 event loop 串行跑:asyncpg 连接池绑 loop,不能每个场景各起 asyncio.run
        await scenario_a_unfinished_and_running_queries()
        await scenario_b_active_registry_blocks_duplicate()
        await scenario_c_resume_finish_empty_unfinished()
    asyncio.run(main())
    print("All tests passed.")
