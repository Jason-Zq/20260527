"""同名人员合并测试(merge_persons 字段仲裁/重挂/回写 + 自动分组守卫;依赖真实 DB,测后清理)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_person_merge.py
"""
import sys
import os
import asyncio
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete, select

from db import customer_file_crud, profile_crud
from db.engine import async_session_maker
from db.models import (CustomerFile, DocExtractResult, ProfileAsset,
                       ProfileHousehold, ProfileImportTask, ProfilePerson,
                       ProfilePersonField)

_HH1 = "测试合并家庭甲"
_HH2 = "测试合并家庭乙"
_HH3 = "测试合并家庭丙"
_HH4 = "测试姓名同步家庭"

_IDN_A = "31011019690325161X"
_IDN_B = "11010119900307777X"


async def _cleanup():
    """按家庭名清掉全部测试残留(person/field 走 DB CASCADE,文件/任务/结果手动清)。"""
    async with async_session_maker() as s:
        hh_ids = (await s.execute(
            select(ProfileHousehold.id).where(ProfileHousehold.name.in_([_HH1, _HH2, _HH3, _HH4]))
        )).scalars().all()
        for hh_id in hh_ids:
            task_ids = (await s.execute(
                select(ProfileImportTask.id).where(ProfileImportTask.household_id == hh_id)
            )).scalars().all()
            if task_ids:
                await s.execute(delete(DocExtractResult).where(
                    DocExtractResult.import_task_id.in_(task_ids)))
                await s.execute(delete(CustomerFile).where(
                    CustomerFile.import_task_id.in_(task_ids)))
                await s.execute(delete(ProfileImportTask).where(
                    ProfileImportTask.id.in_(task_ids)))
            h = await s.get(ProfileHousehold, hh_id)
            if h:
                await s.delete(h)  # persons/person_fields/assets/cases 由 DB CASCADE 清
        await s.commit()


async def _force_person(hh_id, name, relation="待确认", is_main=False, avatar_file_id=None,
                        null_fold=False):
    """绕过查重直接插卡(模拟 023 回填前的存量双卡)。

    null_fold=True 时 name_folded 置 NULL:024 唯一索引对 NULL 天然放行,
    用于构造同折叠键双卡(分组按 person_name_fold(name) Python 侧计算,不受影响);
    撞名守卫(_find_person_by_fold 查 name_folded 列)相关 fixture 保持默认(折叠键落列)。
    """
    async with async_session_maker() as s:
        p = ProfilePerson(household_id=hh_id, name=name, relation_to_main=relation,
                          is_main=is_main, avatar_file_id=avatar_file_id,
                          name_folded=None if null_fold else (profile_crud.person_name_fold(name) or None),
                          created_at=datetime.now(), updated_at=datetime.now())
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return p.id


async def _apply_fields(hh_id, person_id, items):
    return await profile_crud.apply_extracted_fields_v2(
        hh_id, {"person_id": person_id, "matched_by": "test"},
        [{"key": f, "value": v, "column": f} for f, v in items])


async def _set_field_time(person_id, field, dt):
    async with async_session_maker() as s:
        f = (await s.execute(select(ProfilePersonField).where(
            ProfilePersonField.person_id == person_id,
            ProfilePersonField.field == field))).scalars().first()
        f.updated_at = dt
        await s.commit()


async def _fields_of(person_id):
    async with async_session_maker() as s:
        rows = (await s.execute(select(ProfilePersonField).where(
            ProfilePersonField.person_id == person_id))).scalars().all()
        return {f.field: f for f in rows}


def test_merge_persons():
    async def run():
        try:
            await _cleanup()  # 防历史残留
            hh1 = (await profile_crud.get_or_create_household(_HH1))["id"]
            hh2 = (await profile_crud.get_or_create_household(_HH2))["id"]
            hh3 = (await profile_crud.get_or_create_household(_HH3))["id"]

            # ==================== HH1: 字段仲裁 + 重挂 + write_stats 回写 ====================
            k = await _force_person(hh1, "李四")
            d = await _force_person(hh1, "李 四", avatar_file_id=123, null_fold=True)

            # K: gender=男(ai,旧) birth_date=1990-01-01(人工) phone=111(ai,旧) occupation=工程师(ai)
            await _apply_fields(hh1, k, [("gender", "男"), ("phone", "111"), ("occupation", "工程师")])
            await profile_crud.correct_person_field(k, "birth_date", "1990-01-01", corrected_by="测试员")
            # D: gender=女(ai,新) birth_date=1992-02-02(ai) phone=222(人工) email=x@y.com(ai)
            await _apply_fields(hh1, d, [("gender", "女"), ("birth_date", "1992-02-02"), ("email", "x@y.com")])
            await profile_crud.correct_person_field(d, "phone", "222", corrected_by="测试员")
            # 钉死 AI 行时间:gender K 旧 D 新(D 胜);phone 时间无关(人工必赢)
            await _set_field_time(k, "gender", datetime(2026, 1, 1))
            await _set_field_time(d, "gender", datetime(2026, 6, 1))

            # 文件/资产/提取结果挂到 D
            task = await customer_file_crud.create_import_task(
                filename="测试合并任务", client_name=_HH1, client_id=None,
                total_files=1, household_id=hh1)
            async with async_session_maker() as s:
                cf = CustomerFile(file_code="TEST-MERGE-001", import_task_id=task["id"],
                                  client_name=_HH1, filename="李四身份证.pdf", status="done",
                                  person_id=d, created_at=datetime.now(),
                                  updated_at=datetime.now())
                s.add(cf)
                await s.flush()
                cf_id = cf.id
                res = DocExtractResult(
                    customer_file_id=cf_id, import_task_id=task["id"],
                    doc_type="id_card", status="done",
                    write_stats={"person_id": d, "matched_by": "name",
                                 "persons": [{"person_id": d, "name": "李 四"}]},
                    mapped=[{"key": "name", "field": "name", "person_id": d, "action": "written"}],
                    created_at=datetime.now())
                s.add(res)
                await s.flush()
                res_id = res.id
                asset = ProfileAsset(household_id=hh1, owner_person_id=d,
                                     asset_type="property", name="测试房产", attrs={},
                                     status="ai", created_at=datetime.now(),
                                     updated_at=datetime.now())
                s.add(asset)
                await s.flush()
                asset_id = asset.id
                await s.commit()

            r = await profile_crud.merge_persons(hh1, k, d)
            assert r["fields_moved"] == 1, r            # email 直接迁
            assert r["fields_arbitrated"] == 3, r       # gender/birth_date/phone 冲突
            assert len(r["fields_lost"]) == 3, r        # 败方快照:K.gender男 / D.birth_date / K.phone
            assert r["files_repointed"] == 1, r
            assert r["assets_repointed"] == 1, r
            assert r["results_rewritten"] == 1, r
            lost = {(x["field"], x["value"]) for x in r["fields_lost"]}
            assert ("gender", "男") in lost and ("birth_date", "1992-02-02") in lost \
                and ("phone", "111") in lost, r["fields_lost"]

            # 仲裁结果: AI vs AI 晚者胜(D.gender=女);人工必赢(K.birth_date / D.phone)
            fields = await _fields_of(k)
            assert fields["gender"].value == "女" and fields["gender"].status == "ai"
            assert fields["birth_date"].value == "1990-01-01" \
                and fields["birth_date"].status == "corrected"
            assert fields["phone"].value == "222" and fields["phone"].status == "corrected"
            assert fields["email"].value == "x@y.com"
            assert fields["occupation"].value == "工程师"
            # D 已删,字段不残留
            assert await _fields_of(d) == {}
            async with async_session_maker() as s:
                assert await s.get(ProfilePerson, d) is None
            # avatar 补空
            async with async_session_maker() as s:
                kp = await s.get(ProfilePerson, k)
                assert kp.avatar_file_id == 123
                assert kp.name == "李四"  # person.name 恒保持 keep 原值
            # 重挂:文件/资产/write_stats 顶层+persons[]+mapped[]
            async with async_session_maker() as s:
                assert (await s.get(CustomerFile, cf_id)).person_id == k
                assert (await s.get(ProfileAsset, asset_id)).owner_person_id == k
                rr = await s.get(DocExtractResult, res_id)
                assert rr.write_stats["person_id"] == k, rr.write_stats
                assert rr.write_stats["persons"][0]["person_id"] == k, rr.write_stats
                assert rr.mapped[0]["person_id"] == k, rr.mapped
            # 三路并集查询:合并后 K 的「查看文件」能看到原 D 的文件
            person_files = await customer_file_crud.list_person_files(k)
            assert any(f["id"] == cf_id for f in person_files), person_files

            # ==================== HH2: is_main / main_person_id 交接 ====================
            hh2_row = await profile_crud.get_household(hh2)
            main2 = hh2_row["main_person_id"]
            k2 = await _force_person(hh2, "王五")
            d2 = await _force_person(hh2, "王 五", is_main=True, null_fold=True)
            async with async_session_maker() as s:  # 人为把家庭主申请人指向 D2(模拟异常态)
                h = await s.get(ProfileHousehold, hh2)
                h.main_person_id = d2
                await s.commit()
            r2 = await profile_crud.merge_persons(hh2, k2, d2)
            async with async_session_maker() as s:
                kp = await s.get(ProfilePerson, k2)
                assert kp.is_main is True and kp.relation_to_main == "户主"
                assert (await s.get(ProfileHousehold, hh2)).main_person_id == k2
            # 原 household 自动建的户主卡不受影响
            async with async_session_maker() as s:
                assert (await s.get(ProfilePerson, main2)).is_main is True

            # ==================== HH3: 自动分组 + id_number 冲突守卫 ====================
            z1 = await _force_person(hh3, "赵六")
            z2 = await _force_person(hh3, "赵 六", null_fold=True)
            s1 = await _force_person(hh3, "孙七")
            s2 = await _force_person(hh3, "孙 七", null_fold=True)
            await _apply_fields(hh3, s1, [("id_number", _IDN_A)])
            await _apply_fields(hh3, s2, [("id_number", _IDN_B)])
            await _force_person(hh3, "周八")  # 单卡不成组

            groups = await profile_crud.find_duplicate_person_groups(hh3)
            by_fold = {g["folded"]: g for g in groups}
            assert set(by_fold) == {"赵六", "孙七"}, groups
            assert by_fold["赵六"].get("skipped_reason") is None
            assert by_fold["赵六"]["keep_id"] == z1  # id 小者为 keep
            assert by_fold["赵六"]["drop_ids"] == [z2]
            assert by_fold["孙七"]["skipped_reason"] == "conflict_id_number"

            # 自动合并:赵六组执行,孙七组被守卫跳过
            import profile_import_service
            out = await profile_import_service.run_merge_duplicate_persons(hh3, trigger="test")
            assert out["groups"] == 2 and len(out["merged"]) == 1 and len(out["skipped"]) == 1, out
            persons = await profile_crud.list_persons(hh3)
            names = sorted(p["name"] for p in persons)
            assert names.count("赵六") + names.count("赵 六") == 1, names
            assert "孙七" in names and "孙 七" in names  # 冲突组原样保留
            assert "周八" in names

            # ==================== 校验:跨家庭/同人 拒绝 ====================
            other_k = await _force_person(hh1, "钱九")
            try:
                await profile_crud.merge_persons(hh1, other_k, s1)  # s1 属于 hh3
                raise AssertionError("跨家庭合并应报错")
            except ValueError:
                pass
            try:
                await profile_crud.merge_persons(hh1, other_k, other_k)
                raise AssertionError("同人合并应报错")
            except ValueError:
                pass
        finally:
            await _cleanup()
            from db.engine import async_engine
            await async_engine.dispose()

    asyncio.run(run())


def test_name_sync_and_merge_keep_name():
    """修正 name 字段同步名片 + merge keep_name 改名/撞名守卫(依赖真实 DB,测后清理)。"""
    async def run():
        try:
            await _cleanup()  # 防历史残留
            hh = (await profile_crud.get_or_create_household(_HH4))["id"]

            # ---------- 1) 修正 name 字段 → person.name/name_folded 同步 ----------
            p1 = await _force_person(hh, "陈十")
            await profile_crud.correct_person_field(p1, "name", "陈十十", corrected_by="测试员")
            async with async_session_maker() as s:
                p = await s.get(ProfilePerson, p1)
                assert p.name == "陈十十"
                assert p.name_folded == profile_crud.person_name_fold("陈十十")
            nf = (await _fields_of(p1))["name"]
            assert nf.value == "陈十十" and nf.status == "corrected"

            # ---------- 2) 改名撞家庭内另一人折叠键 → ValueError,原名不变 ----------
            p2 = await _force_person(hh, "陈十一")
            try:
                await profile_crud.correct_person_field(p1, "name", "陈 十一", corrected_by="测试员")
                raise AssertionError("撞名应报 ValueError")
            except ValueError:
                pass
            async with async_session_maker() as s:
                assert (await s.get(ProfilePerson, p1)).name == "陈十十"
                assert (await s.get(ProfilePerson, p2)).name == "陈十一"

            # ---------- 3) merge keep_name=drop 名 → keep 改名 + folded 重算 ----------
            p3 = await _force_person(hh, "林十二")
            p4 = await _force_person(hh, "林 十二", null_fold=True)
            await _apply_fields(hh, p4, [("gender", "男")])
            r = await profile_crud.merge_persons(hh, p3, p4, keep_name="林 十二")
            assert r["name_changed"] == {"from": "林十二", "to": "林 十二"}, r
            async with async_session_maker() as s:
                kp = await s.get(ProfilePerson, p3)
                assert kp.name == "林 十二"
                assert kp.name_folded == profile_crud.person_name_fold("林 十二")
                assert await s.get(ProfilePerson, p4) is None
            assert (await _fields_of(p3))["gender"].value == "男"  # 字段仲裁不受影响

            # ---------- 4) keep_name 撞家庭内第三人 → ValueError 且整体回滚 ----------
            p5 = await _force_person(hh, "赵十三")
            p6 = await _force_person(hh, "赵 十三", null_fold=True)
            p7 = await _force_person(hh, "钱十四")
            try:
                await profile_crud.merge_persons(hh, p5, p6, keep_name="钱 十四")
                raise AssertionError("撞第三人应报 ValueError")
            except ValueError:
                pass
            async with async_session_maker() as s:
                assert (await s.get(ProfilePerson, p5)).name == "赵十三"  # 未改名
                assert await s.get(ProfilePerson, p6) is not None       # 回滚,drop 还在
                assert await s.get(ProfilePerson, p7) is not None
        finally:
            await _cleanup()
            from db.engine import async_engine
            await async_engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    test_merge_persons()
    print("PASS test_merge_persons")
    test_name_sync_and_merge_keep_name()
    print("PASS test_name_sync_and_merge_keep_name")
    print("\n全部 2 个测试通过")
