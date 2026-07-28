"""customer_files.person_id 手动归属测试(依赖真实 DB,测后清理)。

场景 A: assign_file_person 设置/清除 + list_person_files 三路并集
        (person_id 列 / write_stats 顶层 person_id / write_stats.persons[] 多人明细)。
场景 B: list_files_for_assignment 筛选(客户名/类型/归属状态)与归属人 enrichment(manual/extract)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_file_assign.py
"""
import sys
import os
import asyncio
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from db import customer_file_crud, doc_extract_crud, profile_crud
from db.engine import async_session_maker
from db.models import CustomerFile, ProfileHousehold, ProfileImportTask
from sqlalchemy import delete as sa_delete


async def _cleanup_full(household_id: int) -> None:
    """测试夹具清理:删名下任务(CASCADE 文件/提取结果)+ 家庭(CASCADE 画像域)。"""
    async with async_session_maker() as s:
        await s.execute(sa_delete(ProfileImportTask).where(
            ProfileImportTask.household_id == household_id))
        h = await s.get(ProfileHousehold, household_id)
        if h:
            await s.delete(h)
        await s.commit()


async def _set_doc_type(file_row_id: int, doc_type: str) -> None:
    async with async_session_maker() as session:
        row = await session.get(CustomerFile, file_row_id)
        row.doc_type = doc_type
        row.updated_at = datetime.now()
        await session.commit()


async def scenario_a_assign_and_union():
    """场景 A: 手动归属设置/清除 + list_person_files 三路并集。"""
    suffix = uuid.uuid4().hex[:12]
    household_id = None
    try:
        h = await profile_crud.get_or_create_household(f"归属测试-{suffix}")
        household_id = h["id"]
        main_id = (await profile_crud.list_persons(household_id))[0]["id"]
        p2 = (await profile_crud.create_person(household_id, f"成员-{suffix}"))["id"]

        task = await customer_file_crud.create_import_task(
            filename=f"assign-{suffix}.xlsx", client_name=f"归属测试-{suffix}",
            client_id=None, total_files=3, household_id=household_id)
        tid = task["id"]
        codes = [f"assign-{suffix}-{c}" for c in "abc"]
        await customer_file_crud.upsert_task_files(tid, None, [
            {"file_code": c, "filename": f"{c}.pdf", "client_name": f"归属测试-{suffix}"}
            for c in codes])
        files, _ = await customer_file_crud.list_task_files(tid)
        fa, fb, fc = (next(f for f in files if f["file_code"] == c)["id"] for c in codes)

        # 三路归属: fa→列, fb→write_stats 顶层, fc→write_stats.persons[] 明细(非首个人)
        await customer_file_crud.assign_file_person(fa, p2)
        await doc_extract_crud.insert_result(
            customer_file_id=fb, import_task_id=tid, file_id=codes[1], client_id=None,
            doc_type="id_card", rule_id=None, rule_version=1, status="done",
            write_stats={"person_id": p2})
        await doc_extract_crud.insert_result(
            customer_file_id=fc, import_task_id=tid, file_id=codes[2], client_id=None,
            doc_type="hukou", rule_id=None, rule_version=2, status="done",
            write_stats={"person_id": main_id,
                         "persons": [{"person_id": main_id}, {"person_id": p2}]})

        got = {f["id"] for f in await customer_file_crud.list_person_files(p2)}
        assert got == {fa, fb, fc}, got
        # 多人模式首个人(main)也能通过 persons[0] 与顶层 person_id 查到 fc
        got_main = {f["id"] for f in await customer_file_crud.list_person_files(main_id)}
        assert fc in got_main, got_main

        # 清除手动归属后 fa 不再出现(fb/fc 仍有提取归因)
        await customer_file_crud.assign_file_person(fa, None)
        got = {f["id"] for f in await customer_file_crud.list_person_files(p2)}
        assert got == {fb, fc}, got
    finally:
        if household_id is not None:
            await _cleanup_full(household_id)


async def scenario_b_list_for_assignment():
    """场景 B: 全局文件列表筛选 + 归属人 enrichment。"""
    suffix = uuid.uuid4().hex[:12]
    household_id = None
    try:
        cname = f"归属列表-{suffix}"
        h = await profile_crud.get_or_create_household(cname)
        household_id = h["id"]
        p2obj = await profile_crud.create_person(household_id, f"成员-{suffix}")
        p2, p2name = p2obj["id"], p2obj["name"]

        task = await customer_file_crud.create_import_task(
            filename=f"assign-{suffix}.xlsx", client_name=cname,
            client_id=None, total_files=3, household_id=household_id)
        tid = task["id"]
        codes = [f"assignlst-{suffix}-{c}" for c in "abc"]
        await customer_file_crud.upsert_task_files(tid, None, [
            {"file_code": c, "filename": f"{c}.pdf", "client_name": cname} for c in codes])
        files, _ = await customer_file_crud.list_task_files(tid)
        fa, fb, fc = (next(f for f in files if f["file_code"] == c)["id"] for c in codes)
        await _set_doc_type(fa, "id_card")
        await _set_doc_type(fb, "hukou")
        await _set_doc_type(fc, "hukou")

        # fa 手动归属, fb 提取归因, fc 无归属
        await customer_file_crud.assign_file_person(fa, p2)
        await doc_extract_crud.insert_result(
            customer_file_id=fb, import_task_id=tid, file_id=codes[1], client_id=None,
            doc_type="hukou", rule_id=None, rule_version=1, status="done",
            write_stats={"person_id": p2})

        # 客户名模糊 + 全量
        rows, total = await customer_file_crud.list_files_for_assignment(client_name=cname)
        assert total == 3 and len(rows) == 3, (total, rows)
        by_id = {r["id"]: r for r in rows}
        assert by_id[fa]["person_id"] == p2 and by_id[fa]["attributed_by"] == "manual"
        assert by_id[fa]["person_name"] == p2name
        assert by_id[fb]["person_id"] == p2 and by_id[fb]["attributed_by"] == "extract"
        assert by_id[fc]["person_id"] is None and by_id[fc]["attributed_by"] is None
        assert all(r["household_id"] == household_id for r in rows)

        # 类型筛选
        rows, total = await customer_file_crud.list_files_for_assignment(
            client_name=cname, doc_type="hukou")
        assert total == 2 and {r["id"] for r in rows} == {fb, fc}

        # 归属状态筛选(列 ∪ write_stats 都算已归属)
        rows, total = await customer_file_crud.list_files_for_assignment(
            client_name=cname, assigned="none")
        assert total == 1 and rows[0]["id"] == fc, (total, rows)
        rows, total = await customer_file_crud.list_files_for_assignment(
            client_name=cname, assigned="any")
        assert total == 2 and {r["id"] for r in rows} == {fa, fb}, (total, rows)

        # 清除归属后"未归属"变 2 个
        await customer_file_crud.assign_file_person(fa, None)
        rows, total = await customer_file_crud.list_files_for_assignment(
            client_name=cname, assigned="none")
        assert total == 2 and {r["id"] for r in rows} == {fa, fc}
    finally:
        if household_id is not None:
            await _cleanup_full(household_id)


if __name__ == "__main__":
    async def main():
        # 单 event loop 串行跑:asyncpg 连接池绑 loop,不能每个场景各起 asyncio.run
        await scenario_a_assign_and_union()
        await scenario_b_list_for_assignment()
    asyncio.run(main())
    print("All tests passed.")
