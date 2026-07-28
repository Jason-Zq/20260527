"""customer_file_crud.delete_import_task / delete_household_profile 测试(依赖真实 DB,测后清理)。

删除画像新语义:只删画像数据(household/persons/person_fields/assets/cases),
任务/文件/OCR/提取结果/磁盘原件全保留(customer_files.person_id 清 NULL),
重新导入可按 file_code re-link 复用 OCR。

场景 A: 两任务共享 household -> 删其一 = 删除画像: 画像域全没,任务/文件/OCR/提取结果保留,
        person_id 清 NULL,新任务 re-link 复用成功。
场景 B: 无 household 的任务 -> 维持原行为(只删任务级,级联 customer_files/doc_extract_results)。
场景 C: delete_household_profile 直接调用 + 删不存在 id 返回 False。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_task_delete.py
"""
import sys
import os
import asyncio
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete as sa_delete, select

from db import customer_file_crud, doc_extract_crud, profile_crud
from db.engine import async_session_maker
from db.models import (CustomerFile, ProfileAsset, ProfileCase, ProfileHousehold,
                       ProfileImportTask, ProfilePersonField)


async def _count_rows(model, *where) -> int:
    async with async_session_maker() as session:
        return len((await session.execute(select(model).where(*where))).scalars().all())


async def _cleanup_tasks(task_ids):
    """夹具清理:按 id 删任务(CASCADE 文件/提取结果)。"""
    async with async_session_maker() as s:
        await s.execute(sa_delete(ProfileImportTask).where(ProfileImportTask.id.in_(task_ids)))
        await s.commit()


async def scenario_a_delete_profile_keeps_files():
    """场景 A: 删共享家庭的一个任务 -> 只删画像数据,文件/OCR 全保留。"""
    suffix = uuid.uuid4().hex[:12]
    hname = f"测试家庭-{suffix}"
    code_a = f"testdel-{suffix}-a"
    code_b = f"testdel-{suffix}-b"
    local_path = f"customer_files/testdel-{suffix}.pdf"
    household_id = None
    task_ids = []
    try:
        h = await profile_crud.get_or_create_household(hname)
        household_id = h["id"]
        task1 = await customer_file_crud.create_import_task(
            filename=f"testdel-{suffix}-1.xlsx", client_name=hname,
            client_id=None, total_files=1, household_id=household_id)
        task2 = await customer_file_crud.create_import_task(
            filename=f"testdel-{suffix}-2.xlsx", client_name=hname,
            client_id=None, total_files=1, household_id=household_id)
        t1, t2 = task1["id"], task2["id"]
        task_ids = [t1, t2]
        await customer_file_crud.upsert_task_files(t1, None, [
            {"file_code": code_a, "filename": "a.pdf", "client_name": hname}])
        await customer_file_crud.upsert_task_files(t2, None, [
            {"file_code": code_b, "filename": "b.pdf", "client_name": hname}])
        files, _ = await customer_file_crud.list_task_files(t1)
        fa = files[0]
        await customer_file_crud.update_file_local(
            fa["id"], local_path=local_path,
            file_keep_until=datetime.now() + timedelta(days=30))
        # 标 done + 写 OCR 文本(re-link 复用的前提)
        async with async_session_maker() as session:
            row = await session.get(CustomerFile, fa["id"])
            row.status = "done"
            row.ocr_text = "测试OCR文本-保留验证"
            await session.commit()
        await doc_extract_crud.insert_result(
            customer_file_id=fa["id"], import_task_id=t1,
            file_id=code_a, client_id=None, doc_type="id_card",
            rule_id=None, rule_version=1, status="done")

        # 画像域数据: 主申请人字段 + 资产 + 案件 + 文件手动归属
        persons = await profile_crud.list_persons(household_id)
        assert len(persons) == 1 and persons[0]["is_main"] is True
        main_id = persons[0]["id"]
        await customer_file_crud.assign_file_person(fa["id"], main_id)
        async with async_session_maker() as session:
            session.add(ProfilePersonField(
                person_id=main_id, field="id_number", value="110101199001011234",
                layer="verified", status="ai",
                created_at=datetime.now(), updated_at=datetime.now()))
            session.add(ProfileAsset(
                household_id=household_id, asset_type="房产", name="测试房产",
                status="ai", created_at=datetime.now(), updated_at=datetime.now()))
            session.add(ProfileCase(
                household_id=household_id, case_type="测试案件",
                milestones=[{"name": "递交", "date": "2026-01-01"}],
                created_at=datetime.now(), updated_at=datetime.now()))
            await session.commit()

        # ---- 删 task1 -> 删除画像(只删画像数据) ----
        deleted, paths, stats = await customer_file_crud.delete_import_task(t1)
        assert deleted is True
        assert paths == [], paths  # 磁盘原件不删
        assert stats.get("household_deleted") is True and stats.get("tasks") == 2, stats
        assert stats.get("files_kept") == 2, stats

        # 画像域全删
        assert await profile_crud.get_household(household_id) is None
        assert await profile_crud.list_persons(household_id) == []
        assert await profile_crud.list_assets(household_id) == []
        assert await profile_crud.list_cases(household_id) == []
        assert await _count_rows(ProfilePersonField, ProfilePersonField.person_id == main_id) == 0

        # 任务/文件/OCR/提取结果全保留;任务 household_id 被 FK 置 NULL;person_id 清 NULL
        for t in (t1, t2):
            assert await customer_file_crud.get_import_task(t) is not None
        async with async_session_maker() as session:
            for t in (t1, t2):
                row = await session.get(ProfileImportTask, t)
                assert row.household_id is None, (t, row.household_id)
            kept = (await session.execute(
                select(CustomerFile).where(CustomerFile.import_task_id.in_([t1, t2]))
                .order_by(CustomerFile.id))).scalars().all()
            assert len(kept) == 2, kept
            fa_row = next(r for r in kept if r.file_code == code_a)
            assert fa_row.ocr_text == "测试OCR文本-保留验证", fa_row.ocr_text
            assert fa_row.local_path == local_path, fa_row.local_path
            assert fa_row.person_id is None, fa_row.person_id
        _, total = await doc_extract_crud.list_results(import_task_id=t1)
        assert total == 1  # 提取结果保留

        # ---- 复用链路:新家庭新任务 re-link 保留行,OCR 原样还在 ----
        h2 = await profile_crud.get_or_create_household(hname)
        task3 = await customer_file_crud.create_import_task(
            filename=f"testdel-{suffix}-3.xlsx", client_name=hname,
            client_id=None, total_files=1, household_id=h2["id"])
        task_ids.append(task3["id"])
        st = await customer_file_crud.upsert_task_files(task3["id"], None, [
            {"file_code": code_a, "filename": "a.pdf", "client_name": hname}])
        assert st["relinked"] == 1 and st["new"] == 0, st
        async with async_session_maker() as session:
            row = (await session.execute(
                select(CustomerFile).where(CustomerFile.file_code == code_a))).scalar_one()
            assert row.import_task_id == task3["id"]
            assert row.status == "done" and row.ocr_text == "测试OCR文本-保留验证"
        household_id = h2["id"]  # 交给 finally 清理
    finally:
        if task_ids:
            await _cleanup_tasks(task_ids)
        if household_id is not None:
            async with async_session_maker() as s:
                hh = await s.get(ProfileHousehold, household_id)
                if hh:
                    await s.delete(hh)
                await s.commit()


async def scenario_b_task_without_household():
    """场景 B: 无 household 的任务维持原删除行为。"""
    suffix = uuid.uuid4().hex[:12]
    code_a = f"testdel-{suffix}-a"
    code_b = f"testdel-{suffix}-b"
    local_path = f"customer_files/testdel-{suffix}.pdf"
    task_id = None
    try:
        task = await customer_file_crud.create_import_task(
            filename=f"testdel-{suffix}.xlsx", client_name="测试删除",
            client_id=None, total_files=2)
        task_id = task["id"]
        await customer_file_crud.upsert_task_files(task_id, None, [
            {"file_code": code_a, "filename": "a.pdf", "client_name": "测试删除"},
            {"file_code": code_b, "filename": "b.pdf", "client_name": "测试删除"},
        ])
        files, total = await customer_file_crud.list_task_files(task_id)
        assert total == 2, files
        fa = next(f for f in files if f["file_code"] == code_a)
        await customer_file_crud.update_file_local(
            fa["id"], local_path=local_path,
            file_keep_until=datetime.now() + timedelta(days=30))
        await doc_extract_crud.insert_result(
            customer_file_id=fa["id"], import_task_id=task_id,
            file_id=code_a, client_id=None, doc_type="id_card",
            rule_id=None, rule_version=1, status="done")

        # ---- 删除 ----
        deleted, paths, stats = await customer_file_crud.delete_import_task(task_id)
        assert deleted is True
        assert paths == [local_path], paths
        assert stats.get("household_deleted") is False, stats

        # 任务/文件/提取结果全部级联删除
        assert await customer_file_crud.get_import_task(task_id) is None
        _, total = await customer_file_crud.list_task_files(task_id)
        assert total == 0
        _, total = await doc_extract_crud.list_results(import_task_id=task_id)
        assert total == 0

        # 重复删除幂等
        deleted2, paths2, _ = await customer_file_crud.delete_import_task(task_id)
        assert deleted2 is False and paths2 == []
        task_id = None  # 已删除,无需清理
    finally:
        if task_id is not None:
            await customer_file_crud.delete_import_task(task_id)


async def scenario_c_delete_household_profile_direct():
    """场景 C: delete_household_profile 直接调用 + 不存在 id 返回 False。"""
    suffix = uuid.uuid4().hex[:12]
    hname = f"测试家庭-{suffix}"
    household_id = None
    try:
        h = await profile_crud.get_or_create_household(hname)
        household_id = h["id"]
        deleted, paths, stats = await customer_file_crud.delete_household_profile(household_id)
        assert deleted is True and paths == [], (deleted, paths)
        assert stats.get("tasks") == 0 and stats.get("files_kept") == 0, stats
        assert await profile_crud.get_household(household_id) is None
        household_id = None

        deleted2, paths2, stats2 = await customer_file_crud.delete_household_profile(999999999)
        assert deleted2 is False and paths2 == [] and stats2.get("tasks") == 0
    finally:
        if household_id is not None:
            await customer_file_crud.delete_household_profile(household_id)


if __name__ == "__main__":
    async def main():
        # 单 event loop 串行跑:asyncpg 连接池绑 loop,不能每个场景各起 asyncio.run
        await scenario_a_delete_profile_keeps_files()
        await scenario_b_task_without_household()
        await scenario_c_delete_household_profile_direct()
    asyncio.run(main())
    print("All tests passed.")
