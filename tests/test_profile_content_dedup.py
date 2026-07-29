"""同家庭内容级去重 + 任务内并发导入测试(依赖真实 DB,测后清理)。

场景 A: find_household_dup_ocr —— 同家庭同 sha256 命中/排除自身/跨家庭隔离/fresh 优先
场景 B: _rule_has_case_fields + _sha256_file 纯函数
场景 C: _record_dup_extract_skip —— 跳过 LLM 留痕 + 沿用兄弟行归因(人员文件并集可查)
场景 D: _run_import 并发冒烟 —— stub 掉 _process_one_file,验证并发度与计数

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_content_dedup.py
"""
import sys
import os
import asyncio
import hashlib
import time
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import extract_rules
import profile_import_service as svc
from db import customer_file_crud, doc_extract_crud, profile_crud
from db.engine import async_session_maker
from db.models import CustomerFile, ProfileHousehold, ProfileImportTask
from sqlalchemy import delete as sa_delete
from sqlalchemy import select


async def _cleanup_full(household_id: int) -> None:
    async with async_session_maker() as s:
        await s.execute(sa_delete(ProfileImportTask).where(
            ProfileImportTask.household_id == household_id))
        h = await s.get(ProfileHousehold, household_id)
        if h:
            await s.delete(h)
        await s.commit()


async def _mk_file(task_id: int, file_code: str, *, ocr_text="张三 身份证 110101199001011234",
                   ocr_source="fresh", sha256=None) -> int:
    """落一个带 OCR 文本(可选内容 hash)的文件行,返回 row id。"""
    await customer_file_crud.upsert_task_files(task_id, None, [
        {"file_code": file_code, "filename": f"{file_code}.pdf", "client_name": "去重测试"}])
    async with async_session_maker() as s:
        row_id = (await s.execute(
            select(CustomerFile.id).where(CustomerFile.file_code == file_code)
        )).scalar_one()
    await customer_file_crud.update_file_ocr(
        row_id, status="ocr", ocr_source=ocr_source, ocr_text=ocr_text,
        mime_type="application/pdf", page_count=1, content_sha256=sha256)
    return row_id


async def scenario_a_find_dup():
    """场景 A: find_household_dup_ocr 命中规则。"""
    suffix = uuid.uuid4().hex[:12]
    household_id = None
    try:
        h = await profile_crud.get_or_create_household(f"去重测试-{suffix}")
        household_id = h["id"]
        h2 = (await profile_crud.get_or_create_household(f"去重测试B-{suffix}"))["id"]
        t1 = (await customer_file_crud.create_import_task(
            filename=f"a-{suffix}.xlsx", client_name="去重测试",
            client_id=None, total_files=0, household_id=household_id))["id"]
        t2 = (await customer_file_crud.create_import_task(
            filename=f"b-{suffix}.xlsx", client_name="去重测试",
            client_id=None, total_files=0, household_id=h2))["id"]
        digest = hashlib.sha256(b"same-content").hexdigest()

        # fresh 行(同家庭 t1) + reused 行(同家庭 t1) + 同 hash 行(另一家庭 t2)
        fa = await _mk_file(t1, f"dup-{suffix}-a", ocr_source="fresh", sha256=digest)
        fb = await _mk_file(t1, f"dup-{suffix}-b", ocr_source="reused", sha256=digest)
        fc = await _mk_file(t2, f"dup-{suffix}-c", ocr_source="fresh", sha256=digest)

        # 同家庭命中:fresh 优先于 reused(即使 reused 的 id 更大)
        got = await customer_file_crud.find_household_dup_ocr(household_id, digest)
        assert got and got["id"] == fa and got["ocr_source"] == "fresh", got
        # 排除自身后命中 reused 行
        got = await customer_file_crud.find_household_dup_ocr(household_id, digest, exclude_id=fa)
        assert got and got["id"] == fb, got
        # 跨家庭隔离:h2 只命中自己的 fc
        got = await customer_file_crud.find_household_dup_ocr(h2, digest)
        assert got and got["id"] == fc, got
        # 未知 hash / 空 ocr_text 不命中
        assert await customer_file_crud.find_household_dup_ocr(
            household_id, hashlib.sha256(b"other").hexdigest()) is None
        fe = await _mk_file(t1, f"dup-{suffix}-e", ocr_text="", sha256="x" * 64)
        assert await customer_file_crud.find_household_dup_ocr(household_id, "x" * 64) is None
        # household_id 为空安全返回 None
        assert await customer_file_crud.find_household_dup_ocr(None, digest) is None
    finally:
        if household_id is not None:
            await _cleanup_full(household_id)


def scenario_b_pure():
    """场景 B: 纯函数(_rule_has_case_fields / _sha256_file)。"""
    for dt in ("submission", "receipt", "approval"):
        assert svc._rule_has_case_fields(extract_rules.get_rule(dt)) is True, dt
    for dt in ("id_card", "hukou", "passport", "property_cert", "birth_cert", "no_crime"):
        assert svc._rule_has_case_fields(extract_rules.get_rule(dt)) is False, dt
    assert svc._rule_has_case_fields({"fields": []}) is False

    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_sha_{uuid.uuid4().hex[:8]}.bin")
    try:
        payload = b"dup-content" * 1000
        with open(tmp, "wb") as f:
            f.write(payload)
        assert svc._sha256_file(tmp) == hashlib.sha256(payload).hexdigest()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


async def scenario_c_dup_extract_skip():
    """场景 C: 重复文件跳过 LLM 提取,留痕并沿用兄弟行归因,人员文件并集可查。"""
    suffix = uuid.uuid4().hex[:12]
    household_id = None
    try:
        h = await profile_crud.get_or_create_household(f"去重留痕-{suffix}")
        household_id = h["id"]
        person_id = (await profile_crud.list_persons(household_id))[0]["id"]
        tid = (await customer_file_crud.create_import_task(
            filename=f"c-{suffix}.xlsx", client_name="去重留痕",
            client_id=None, total_files=0, household_id=household_id))["id"]
        # 兄弟行:已 done 提取,write_stats 带归因(含多人 persons[])
        fa = await _mk_file(tid, f"skip-{suffix}-a", sha256=hashlib.sha256(b"x").hexdigest())
        await doc_extract_crud.insert_result(
            customer_file_id=fa, import_task_id=tid, file_id=f"skip-{suffix}-a",
            client_id=None, doc_type="hukou", rule_id=None, rule_version=2, status="done",
            write_stats={"person_id": person_id, "persons": [{"person_id": person_id}]})
        fb = await _mk_file(tid, f"skip-{suffix}-b", sha256=hashlib.sha256(b"x").hexdigest())

        task = await customer_file_crud.get_import_task(tid)
        row = await customer_file_crud.get_customer_file(fb)
        dup_of = await customer_file_crud.find_household_dup_ocr(
            household_id, hashlib.sha256(b"x").hexdigest(), exclude_id=fb)
        assert dup_of and dup_of["id"] == fa
        outcome = await svc._record_dup_extract_skip(
            task, row, "hukou", extract_rules.get_rule("hukou"), dup_of)
        assert outcome["status"] == "skipped" and outcome["skip_reason"] == "dup_content"

        latest = await doc_extract_crud.get_latest_result_for_file(fb)
        assert latest["status"] == "skipped" and latest["skip_reason"] == "dup_content"
        ws = latest["write_stats"]
        assert ws["person_id"] == person_id and ws["dup_of_file_id"] == fa, ws
        assert ws["persons"] == [{"person_id": person_id}], ws
        # 人员「查看文件」并集能关联到重复文件(write_stats 顶层/persons[] 通道)
        pfiles = {f["id"] for f in await customer_file_crud.list_person_files(person_id)}
        assert fb in pfiles, pfiles
    finally:
        if household_id is not None:
            await _cleanup_full(household_id)


async def scenario_d_concurrency_smoke():
    """场景 D: _run_import 文件级并发(stub _process_one_file,验证并发度/计数/任务收尾)。"""
    suffix = uuid.uuid4().hex[:12]
    tid = None
    try:
        task = await customer_file_crud.create_import_task(
            filename=f"d-{suffix}.xlsx", client_name="并发测试",
            client_id=None, total_files=6, household_id=None)
        tid = task["id"]
        await customer_file_crud.upsert_task_files(tid, None, [
            {"file_code": f"conc-{suffix}-{i}", "filename": f"{i}.pdf", "client_name": "并发测试"}
            for i in range(6)])

        in_flight = 0
        max_in_flight = 0
        orig = svc._process_one_file

        async def _stub(task, row, counters):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.3)
            in_flight -= 1

        svc._process_one_file = _stub
        try:
            t0 = time.time()
            await svc.run_import(tid)
            elapsed = time.time() - t0
        finally:
            svc._process_one_file = orig

        assert max_in_flight >= 2, f"未观察到并发(max={max_in_flight})"
        assert elapsed < 1.5, f"6x0.3s 串行应 1.8s+,实际 {elapsed:.2f}s 疑似未并发"
        final = await customer_file_crud.get_import_task(tid)
        assert final["status"] == "done", final["status"]
        assert final["processed_files"] == 6, final["processed_files"]
    finally:
        if tid is not None:
            async with async_session_maker() as s:
                await s.execute(sa_delete(ProfileImportTask).where(ProfileImportTask.id == tid))
                await s.commit()


if __name__ == "__main__":
    async def main():
        # 单 event loop 串行跑:asyncpg 连接池绑 loop,不能每个场景各起 asyncio.run
        scenario_b_pure()
        await scenario_a_find_dup()
        await scenario_c_dup_extract_skip()
        await scenario_d_concurrency_smoke()
    asyncio.run(main())
    print("All tests passed.")
