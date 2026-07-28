"""项目案件(按 affter_entryoid 路由)+ 家庭客户编号/CRM OID 测试(依赖真实 DB,测后清理)。

覆盖:
  - get_or_create_household: customer_code/crm_oid 新建写入、已存在只补空;
  - upsert_project_cases: 新建(案件名=二级项目名)/只补空/幂等/entryoid 空跳过;
  - apply_case_milestones 路由: entryoid→项目案件, NULL→默认案件, 同户两项目状态隔离;
  - 部分唯一约束: 同 (household, entryoid) 二次 insert 报错。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_cases_project.py
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from db import profile_crud
from db.engine import async_session_maker
from db.models import ProfileCase, ProfileHousehold

_HH = "测试项目案件家庭"


async def _cleanup(household_id=None):
    async with async_session_maker() as s:
        if household_id:
            await s.execute(delete(ProfileCase).where(ProfileCase.household_id == household_id))
            h = await s.get(ProfileHousehold, household_id)
            if h:
                await s.delete(h)
        else:
            ids = (await s.execute(
                select(ProfileHousehold.id).where(ProfileHousehold.name == _HH))).scalars().all()
            for hid in ids:
                await s.execute(delete(ProfileCase).where(ProfileCase.household_id == hid))
            await s.execute(delete(ProfileHousehold).where(ProfileHousehold.name == _HH))
        await s.commit()


def test_project_cases():
    async def run():
        hh_id = None
        try:
            await _cleanup()  # 防历史残留

            # ---- 家庭: customer_code/crm_oid 新建写入 + 只补空 ----
            hh = await profile_crud.get_or_create_household(
                _HH, customer_code="U-001", crm_oid="crm-1")
            hh_id = hh["id"]
            assert hh["customer_code"] == "U-001" and hh["crm_oid"] == "crm-1", hh
            hh2 = await profile_crud.get_or_create_household(
                _HH, customer_code="U-999", crm_oid="crm-999")
            assert hh2["customer_code"] == "U-001" and hh2["crm_oid"] == "crm-1", hh2  # 不覆盖
            print("[ok] household customer_code/crm_oid 只补空")

            # ---- upsert_project_cases: 新建 ----
            projects = [
                {"affter_entryoid": "e1", "projectno": "3382", "projectname": "高才通-续签",
                 "projectno_detailed": "3549", "projectname_detailed": "A类延期1年",
                 "project_create_time": "2026-07-27 18:22:56", "file_count": 2},
                {"affter_entryoid": "e2", "projectno": "3181", "projectname": "项目B",
                 "projectno_detailed": None, "projectname_detailed": None,
                 "project_create_time": "脏数据", "file_count": 1},
                {"affter_entryoid": "", "projectname": "无OID跳过", "file_count": 1},
            ]
            r = await profile_crud.upsert_project_cases(hh_id, projects)
            assert r == {"created": 2, "updated": 0}, r
            cases = await profile_crud.list_cases(hh_id)
            assert len(cases) == 2, cases
            by_eo = {c["affter_entryoid"]: c for c in cases}
            c1, c2 = by_eo["e1"], by_eo["e2"]
            assert c1["case_type"] == "A类延期1年" and c1["status"] == "进行中", c1
            assert c1["projectno"] == "3382" and c1["projectname"] == "高才通-续签"
            assert c1["projectno_detailed"] == "3549" and c1["projectname_detailed"] == "A类延期1年"
            assert c1["project_created_at"] == "2026-07-27 18:22:56", c1
            assert c2["case_type"] == "项目B"  # 无二级回退一级
            assert c2["project_created_at"] is None  # 脏时间解析失败→None 不杀导入

            # ---- upsert_project_cases: 幂等 + 只补空 ----
            r = await profile_crud.upsert_project_cases(hh_id, projects)
            assert r == {"created": 0, "updated": 0}, r
            projects[1]["projectno_detailed"] = "3182"  # 补空列
            projects[1]["projectname_detailed"] = "B-详细"
            r = await profile_crud.upsert_project_cases(hh_id, projects)
            assert r == {"created": 0, "updated": 1}, r
            cases = await profile_crud.list_cases(hh_id)
            c2 = {c["affter_entryoid"]: c for c in cases}["e2"]
            assert c2["projectno_detailed"] == "3182" and c2["projectname_detailed"] == "B-详细"
            assert c2["case_type"] == "项目B", c2  # 已有真实名不被项目名覆盖
            assert len(cases) == 2  # 不重复建
            print("[ok] upsert_project_cases 新建/幂等/只补空/None跳过")

            # ---- apply_case_milestones 路由: e1 递交 → e1 案件 ----
            r = await profile_crud.apply_case_milestones(hh_id, [
                {"key": "submit_date", "label": "递交", "value": "2026-07-20"},
            ], source_file_id=1, affter_entryoid="e1", project_name_hint="A类延期1年")
            assert r["stats"]["milestone_created"] == 1 and r["case_id"] == c1["id"], r

            # ---- e2 获批 → e2 案件(无 case_type 也能按文件路由,approval 场景) ----
            r = await profile_crud.apply_case_milestones(hh_id, [
                {"key": "case_approved", "label": "获批", "value": "2026-07-25"},
            ], source_file_id=2, affter_entryoid="e2")
            assert r["case_id"] == c2["id"], r

            # ---- entryoid NULL → 默认案件(自动建,project_name_hint 命名) ----
            r = await profile_crud.apply_case_milestones(hh_id, [
                {"key": "sign_date", "label": "签收", "value": "2026-07-26"},
            ], source_file_id=3, project_name_hint="历史项目")
            assert r["stats"]["case_created"] == 1, r
            default_case_id = r["case_id"]
            assert default_case_id not in (c1["id"], c2["id"])

            # 再写一次 NULL → 仍命中同一个默认案件(不重复建)
            r = await profile_crud.apply_case_milestones(hh_id, [
                {"key": "submit_date", "label": "递交", "value": "2026-07-01"},
            ], source_file_id=4)
            assert r["case_id"] == default_case_id and r["stats"]["case_created"] == 0, r

            # ---- 同户三案件状态/里程碑隔离 ----
            cases = await profile_crud.list_cases(hh_id)
            assert len(cases) == 3, cases
            by_id = {c["id"]: c for c in cases}
            assert by_id[c1["id"]]["status"] == "已递交"
            assert [m["name"] for m in by_id[c1["id"]]["milestones"]] == ["递交"]
            assert by_id[c2["id"]]["status"] == "已获批"
            assert [m["name"] for m in by_id[c2["id"]]["milestones"]] == ["获批"]
            assert by_id[default_case_id]["status"] == "已签收"
            assert by_id[default_case_id]["case_type"] == "历史项目"
            assert [m["name"] for m in by_id[default_case_id]["milestones"]] == ["递交", "签收"]
            print("[ok] apply_case_milestones 按 entryoid 路由 + 默认案件 + 状态隔离")

            # ---- 部分唯一约束: 同 (household, entryoid) 二次 insert 报错 ----
            async with async_session_maker() as s:
                from datetime import datetime
                s.add(ProfileCase(household_id=hh_id, case_type="x", status="进行中",
                                  milestones=[], affter_entryoid="e1",
                                  created_at=datetime.now(), updated_at=datetime.now()))
                try:
                    await s.commit()
                    raise AssertionError("唯一约束未生效: 同 entryoid 二次 insert 成功")
                except IntegrityError:
                    await s.rollback()
            # 默认案件唯一约束: 再插一条 NULL entryoid 也报错
            async with async_session_maker() as s:
                from datetime import datetime
                s.add(ProfileCase(household_id=hh_id, case_type="y", status="进行中",
                                  milestones=[], affter_entryoid=None,
                                  created_at=datetime.now(), updated_at=datetime.now()))
                try:
                    await s.commit()
                    raise AssertionError("唯一约束未生效: 默认案件二次 insert 成功")
                except IntegrityError:
                    await s.rollback()
            print("[ok] 部分唯一约束(entryoid + 默认案件)")
        finally:
            await _cleanup(hh_id)

    asyncio.run(run())


if __name__ == "__main__":
    test_project_cases()
    print("\n全部通过")
