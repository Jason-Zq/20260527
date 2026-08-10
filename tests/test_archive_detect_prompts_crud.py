"""提示词库 CRUD + update_batch_overall2 测试(依赖真实 DB,测后清理)。

覆盖:create/get/list/update/set_prompt2/delete 全路径、五元组唯一冲突(409 映射依据)、
get_or_create 幂等(同键两次同 id 且不覆盖已有 prompt1)、update 撞他人键 IntegrityError、
update_batch_overall2 写 archive_detect_batches.overall_*2 三列、
apply_to_overall1 开关(set/查询回读)与总体1 适用行查询 _get_applicable_prompt_row 的门控语义、
list_prompts 按 apply_to_overall1 三态筛选。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_archive_detect_prompts_crud.py
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.exc import IntegrityError

import archive_detect_service as ads
from db import archive_detect_crud, prompt_library_crud as plc
from db.engine import async_session_maker
from db.models import ArchiveDetectBatch, ArchiveDetectPrompt


async def main():
    suffix = uuid.uuid4().hex[:8]
    key = plc.normalize_prompt_key(f"项目A-{suffix}", f"C1-{suffix}", "详情A", f"D1-{suffix}", "进展X")
    key2 = plc.normalize_prompt_key(f"项目B-{suffix}", "", "", "", "进展Y")
    batch_id = f"prompt-test-{suffix}"
    try:
        # ---- create + get_by_key + get_by_id ----
        row = await plc.create_prompt(key, prompt1="模板1", prompt2="标准2")
        assert row["id"] > 0
        by_key = await plc.get_prompt_by_key(key)
        assert by_key and by_key["id"] == row["id"] and by_key["prompt1"] == "模板1"
        by_id = await plc.get_prompt_by_id(row["id"])
        assert by_id and by_id["prompt2"] == "标准2"

        # 归一化等价命中(strip 后同键)
        same = await plc.get_prompt_by_key(
            plc.normalize_prompt_key(f" 项目A-{suffix} ", f"C1-{suffix}", "详情A", f"D1-{suffix}", "进展X "))
        assert same and same["id"] == row["id"]

        # ---- 五元组唯一冲突 ----
        dup = False
        try:
            await plc.create_prompt(key, prompt1="x")
        except IntegrityError:
            dup = True
        assert dup, "同五元组重复 create 应抛 IntegrityError"

        # ---- get_or_create 幂等 ----
        r1, c1 = await plc.get_or_create_prompt(key, default_prompt1="默认模板")
        assert not c1 and r1["id"] == row["id"] and r1["prompt1"] == "模板1", "已存在行不应被默认模板覆盖"
        r2, c2 = await plc.get_or_create_prompt(key2, default_prompt1="默认模板")
        assert c2 and r2["prompt1"] == "默认模板" and r2["prompt2"] is None
        r3, c3 = await plc.get_or_create_prompt(key2, default_prompt1="别的模板")
        assert not c3 and r3["id"] == r2["id"] and r3["prompt1"] == "默认模板"

        # ---- set_prompt2 / update_prompt ----
        await plc.set_prompt2(r2["id"], "AI生成的标准")
        assert (await plc.get_prompt_by_id(r2["id"]))["prompt2"] == "AI生成的标准"
        upd = await plc.update_prompt(r2["id"], key2, prompt1="新模板", prompt2=None)
        assert upd["prompt1"] == "新模板" and upd["prompt2"] is None
        # update 撞他人五元组 → IntegrityError
        clash = False
        try:
            await plc.update_prompt(r2["id"], key, prompt1="z")
        except IntegrityError:
            clash = True
        assert clash, "update 撞他人五元组应抛 IntegrityError"
        assert await plc.update_prompt(99999999, key2) is None

        # ---- apply_to_overall1 开关 + 总体1 适用行查询门控 ----
        assert row["apply_to_overall1"] is False, "新建行开关默认 False"
        ctx = {"project_name": key[0], "project_code": key[1], "project_detail_name": key[2],
               "project_detail_code": key[3], "progress_name": key[4], "progress_id": "prog-1"}
        # 开关关 → 不适用
        assert await ads._get_applicable_prompt_row(ctx) is None
        # 开关开 + prompt2 非空 → 适用
        assert await plc.set_apply_to_overall1(row["id"], True) is True
        app = await ads._get_applicable_prompt_row(ctx)
        assert app and app["id"] == row["id"], "开启开关且有 prompt2 应命中"
        # prompt2 置空 → 不适用(空标准不能驱动总体1)
        await plc.set_prompt2(row["id"], "")
        assert await ads._get_applicable_prompt_row(ctx) is None
        await plc.set_prompt2(row["id"], "标准2")
        # 无 progress_id(历史 quick 批次) / 五元组全空 → 不适用
        assert await ads._get_applicable_prompt_row({**ctx, "progress_id": None}) is None
        assert await ads._get_applicable_prompt_row({"progress_id": "prog-1"}) is None
        # 不存在行 → False;取消开关 → 回到不适用
        assert await plc.set_apply_to_overall1(99999999, True) is False
        assert await plc.set_apply_to_overall1(row["id"], False) is True
        assert await ads._get_applicable_prompt_row(ctx) is None

        # ---- list 筛选 + 分页 ----
        lst = await plc.list_prompts(project_name=f"项目A-{suffix}")
        assert lst["total"] == 1 and lst["items"][0]["id"] == row["id"]
        assert lst["items"][0]["apply_to_overall1"] is False, "list 序列化应带开关字段"
        lst2 = await plc.list_prompts(progress_name="进展", limit=100)
        ids = {p["id"] for p in lst2["items"]}
        assert {row["id"], r2["id"]} <= ids
        assert (await plc.list_prompts(project_name=f"不存在-{suffix}"))["total"] == 0

        # ---- apply_to_overall1 三态列表筛选(row=开, r2=关) ----
        await plc.set_apply_to_overall1(row["id"], True)
        on_ids = {p["id"] for p in (await plc.list_prompts(
            progress_name="进展", apply_to_overall1=True, limit=200))["items"]}
        assert row["id"] in on_ids and r2["id"] not in on_ids, "True 应只含生效中行"
        off_ids = {p["id"] for p in (await plc.list_prompts(
            progress_name="进展", apply_to_overall1=False, limit=200))["items"]}
        assert r2["id"] in off_ids and row["id"] not in off_ids, "False 应只含未生效行"
        # 不传 = 全部(上行 lst2 已覆盖两行都在)
        await plc.set_apply_to_overall1(row["id"], False)

        # ---- update_batch_overall2 写列 ----
        async with async_session_maker() as s:
            s.add(ArchiveDetectBatch(
                batch_id=batch_id, user_prompt="标准", source_kind="batch",
                status="done", total_files=1, done_files=1,
                created_at=datetime.now(), updated_at=datetime.now()))
            await s.commit()
        await archive_detect_crud.update_batch_overall2(batch_id, "partial", 65, "理由2")
        async with async_session_maker() as s:
            b = (await s.execute(
                select(ArchiveDetectBatch).where(ArchiveDetectBatch.batch_id == batch_id))).scalar_one()
            assert b.overall_verdict2 == "partial" and b.overall_score2 == 65 and b.overall_reason2 == "理由2"

        # ---- delete ----
        assert await plc.delete_prompt(row["id"]) is True
        assert await plc.delete_prompt(row["id"]) is False
        assert await plc.get_prompt_by_key(key) is None
    finally:
        async with async_session_maker() as s:
            await s.execute(sa_delete(ArchiveDetectPrompt).where(
                ArchiveDetectPrompt.project_name.in_([f"项目A-{suffix}", f"项目B-{suffix}"])))
            await s.execute(sa_delete(ArchiveDetectBatch).where(ArchiveDetectBatch.batch_id == batch_id))
            await s.commit()
    print("All tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
