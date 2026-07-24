"""profile_crud 归因v2 + person_fields 写入语义测试(依赖真实 DB,测后清理)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_crud.py
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete, select

from db import profile_crud
from db.engine import async_session_maker
from db.models import ProfileAsset, ProfileCase, ProfileHousehold, ProfilePerson, ProfilePersonField

_HH = "测试画像家庭"
_IDN = "110101199001011234"


async def _cleanup(household_id):
    async with async_session_maker() as s:
        if household_id:
            await s.execute(delete(ProfileCase).where(ProfileCase.household_id == household_id))
            await s.execute(delete(ProfileAsset).where(ProfileAsset.household_id == household_id))
            await s.execute(delete(ProfilePersonField).where(
                ProfilePersonField.person_id.in_(
                    select(ProfilePerson.id).where(ProfilePerson.household_id == household_id))))
            await s.execute(delete(ProfilePerson).where(ProfilePerson.household_id == household_id))
            h = await s.get(ProfileHousehold, household_id)
            if h:
                await s.delete(h)
        else:
            await s.execute(delete(ProfileHousehold).where(ProfileHousehold.name == _HH))
        await s.commit()


def test_profile_domain():
    async def run():
        hh_id = None
        try:
            await _cleanup(None)  # 防历史残留
            # ---- 建家庭(自动带主申请人) ----
            hh = await profile_crud.get_or_create_household(_HH)
            hh_id = hh["id"]
            assert hh["main_person_id"], hh
            persons = await profile_crud.list_persons(hh_id)
            assert len(persons) == 1 and persons[0]["is_main"] and persons[0]["relation_to_main"] == "户主", persons
            main_id = persons[0]["id"]

            # 再调一次不重复建
            hh2 = await profile_crud.get_or_create_household(_HH)
            assert hh2["id"] == hh_id
            assert await profile_crud.count_persons(hh_id) == 1

            # ---- AI 写入字段(身份) ----
            res = await profile_crud.apply_extracted_fields_v2(hh_id,
                {"person_id": main_id, "matched_by": "name"}, [
                    {"key": "gender", "value": "男", "column": "gender"},
                    {"key": "birth_date", "value": "1969-03-25", "column": "birth_date"},
                    {"key": "id_number", "value": _IDN, "column": "id_number"},
                    {"key": "phone", "value": "13651822227", "column": "phone"},  # declared 层
                    {"key": "bad_id", "value": "123", "column": "id_number2"},
                    {"key": "issuing", "value": "某局", "column": None},
                ], source_file_id=1)
            actions = {m["field"]: m["action"] for m in res["mapped"]}
            assert actions.get("gender") == "written" and actions.get("id_number") == "written", actions
            assert actions.get("birth_date") == "written", actions
            persons = await profile_crud.list_persons(hh_id)
            fmap = {f["field"]: f for f in persons[0]["fields"]}
            assert fmap["birth_date"]["value"] == "1969-03-25", fmap["birth_date"]
            assert fmap["birth_date"]["layer"] == "verified", fmap["birth_date"]
            assert fmap["phone"]["layer"] == "declared", fmap["phone"]
            assert fmap["gender"]["status"] == "ai", fmap["gender"]

            # ---- 归因:证件号命中 ----
            m = await profile_crud.find_person_match(hh_id, _IDN, None)
            assert m == {"person_id": main_id, "matched_by": "id_number"}, m
            m = await profile_crud.find_person_match(hh_id, None, _HH)
            assert m == {"person_id": main_id, "matched_by": "name"}, m

            # ---- 归因:拼音名(词序无关) ----
            await profile_crud.apply_extracted_fields_v2(hh_id,
                {"person_id": main_id, "matched_by": "name"}, [
                    {"key": "name_en", "value": "NI ZHAOHUI", "column": "name_en"},
                ])
            m = await profile_crud.find_person_match(hh_id, None, None, "Zhaohui Ni")
            assert m == {"person_id": main_id, "matched_by": "name_en"}, m
            m = await profile_crud.find_person_match(hh_id, None, None, "LIU XIAOJUAN")
            assert m == {"person_id": None, "matched_by": None}, m

            # ---- declared 不覆盖 verified ----
            res = await profile_crud.apply_extracted_fields_v2(hh_id,
                {"person_id": main_id, "matched_by": "id_number"}, [
                    {"key": "birth_date", "value": "1969/03/26", "column": "birth_date", "layer": "declared"},
                    {"key": "gender", "value": "女", "column": "gender"},  # AI 可更新 AI 值
                ])
            actions = {m["field"]: m["action"] for m in res["mapped"]}
            assert actions["birth_date"] == "skipped_layer", actions
            assert actions["gender"] == "updated", actions

            # ---- 人工修正后 AI 不再覆盖 ----
            r = await profile_crud.correct_person_field(main_id, "gender", "男", corrected_by="tester")
            assert r["action"] == "corrected", r
            res = await profile_crud.apply_extracted_fields_v2(hh_id,
                {"person_id": main_id, "matched_by": "id_number"}, [
                    {"key": "gender", "value": "女", "column": "gender"},
                ])
            assert res["mapped"][0]["action"] == "skipped_confirmed", res["mapped"]
            persons = await profile_crud.list_persons(hh_id)
            fmap = {f["field"]: f for f in persons[0]["fields"]}
            assert fmap["gender"]["value"] == "男" and fmap["gender"]["status"] == "corrected", fmap["gender"]

            # ---- 无命中新建人(待确认) ----
            res = await profile_crud.apply_extracted_fields_v2(hh_id,
                {"person_id": None, "matched_by": None}, [
                    {"key": "name", "value": "测试新人", "column": "name"},
                    {"key": "id_number", "value": "220202200002022022", "column": "id_number"},
                ])
            assert res["write_stats"]["person_created"] == 1, res["write_stats"]
            persons = await profile_crud.list_persons(hh_id)
            assert len(persons) == 2, [p["name"] for p in persons]
            newp = [p for p in persons if p["name"] == "测试新人"][0]
            assert newp["relation_to_main"] == "待确认"

            # ---- 修正关系 + 清除字段 ----
            await profile_crud.set_person_relation(newp["id"], "子")
            persons = await profile_crud.list_persons(hh_id)
            newp = [p for p in persons if p["name"] == "测试新人"][0]
            assert newp["relation_to_main"] == "子"
            r = await profile_crud.correct_person_field(newp["id"], "id_number", "", corrected_by="tester")
            assert r["action"] == "cleared", r
            persons = await profile_crud.list_persons(hh_id)
            newp = [p for p in persons if p["name"] == "测试新人"][0]
            assert all(f["field"] != "id_number" for f in newp["fields"])

            # ---- 日期非法 ----
            try:
                await profile_crud.correct_person_field(main_id, "birth_date", "不是日期", corrected_by="t")
                raise AssertionError("应当 ValueError")
            except ValueError:
                pass

            # ---- 资产写入(entity=asset):新建 → 同证号去重更新 → masked 跳过 ----
            aw = await profile_crud.apply_extracted_asset(hh_id, main_id, [
                {"key": "address", "value": "政和路388弄35号301室"},
                {"key": "area", "value": "344.31"},
                {"key": "cert_no", "value": "杨2015021659"},
                {"key": "account", "value": "[银行卡]"},  # masked 不入 attrs
            ], source_file_id=1)
            assert aw["stats"]["asset_created"] == 1, aw
            asset_id = aw["asset_id"]
            assert asset_id
            assets = await profile_crud.list_assets(hh_id)
            assert len(assets) == 1, assets
            a = assets[0]
            assert a["name"] == "政和路388弄35号301室" and a["owner_person_id"] == main_id, a
            assert a["attrs"].get("cert_no") == "杨2015021659" and "account" not in a["attrs"], a

            # 同证号再提取:去重更新不新建
            aw2 = await profile_crud.apply_extracted_asset(hh_id, main_id, [
                {"key": "address", "value": "政和路388弄35号301室"},
                {"key": "cert_no", "value": "杨2015021659"},
                {"key": "right_type", "value": "公寓"},
            ], source_file_id=2)
            assert aw2["stats"]["asset_updated"] == 1 and aw2["asset_id"] == asset_id, aw2
            assets = await profile_crud.list_assets(hh_id)
            assert len(assets) == 1, assets
            assert assets[0]["attrs"].get("right_type") == "公寓", assets[0]["attrs"]

            # 全空不建行
            aw3 = await profile_crud.apply_extracted_asset(hh_id, None, [{"key": "area", "value": None}])
            assert aw3["asset_id"] is None and aw3["stats"]["asset_created"] == 0, aw3

            # ---- 案件时间线(entity=case):建案 → 里程碑 upsert → 状态派生 → case_type 占位替换 ----
            cw = await profile_crud.apply_case_milestones(hh_id, [
                {"key": "submit_date", "label": "递交", "value": "2026-06-01"},
            ], source_file_id=1)
            assert cw["stats"]["case_created"] == 1 and cw["stats"]["milestone_created"] == 1, cw
            case_id = cw["case_id"]
            cases = await profile_crud.list_cases(hh_id)
            assert len(cases) == 1, cases
            c = cases[0]
            assert c["case_type"] == profile_crud.CASE_PLACEHOLDER_TYPE and c["status"] == "已递交", c
            assert [m["name"] for m in c["milestones"]] == ["递交"], c["milestones"]

            # case_type 占位时可被替换;获批里程碑后状态推进,按日期排序
            cw = await profile_crud.apply_case_milestones(hh_id, [
                {"key": "case_type", "label": "案件类型", "value": "瓦努阿图永居"},
                {"key": "approve_date", "label": "获批", "value": "2026-06-03"},
            ], source_file_id=2)
            assert cw["case_id"] == case_id and cw["stats"]["milestone_created"] == 1, cw
            cases = await profile_crud.list_cases(hh_id)
            c = cases[0]
            assert c["case_type"] == "瓦努阿图永居" and c["status"] == "已获批", c
            assert [m["name"] for m in c["milestones"]] == ["递交", "获批"], c["milestones"]

            # 同日期跳过;新日期更新;已有 case_type 不被覆盖
            cw = await profile_crud.apply_case_milestones(hh_id, [
                {"key": "submit_date", "label": "递交", "value": "2026-06-01"},
                {"key": "approve_date", "label": "获批", "value": "2026-06-05"},
                {"key": "case_type", "label": "案件类型", "value": "别的项目"},
            ], source_file_id=3)
            actions = {m["field"]: m["action"] for m in cw["mapped"]}
            assert actions["case:递交"] == "skipped_same" and actions["case:获批"] == "milestone_updated", actions
            cases = await profile_crud.list_cases(hh_id)
            c = cases[0]
            assert c["case_type"] == "瓦努阿图永居" and c["status"] == "已获批", c
            assert [m["date"] for m in c["milestones"]] == ["2026-06-01", "2026-06-05"], c["milestones"]

            # 签收 → 已签收;非法日期不入里程碑
            cw = await profile_crud.apply_case_milestones(hh_id, [
                {"key": "sign_date", "label": "签收", "value": "2026.6.11"},
                {"key": "bad", "label": "坏日期", "value": "不是日期"},
            ], source_file_id=4)
            actions = {m["field"]: m["action"] for m in cw["mapped"]}
            assert actions["case:bad"] == "skipped_invalid", actions
            cases = await profile_crud.list_cases(hh_id)
            c = cases[0]
            assert c["status"] == "已签收", c
            assert [m["name"] for m in c["milestones"]] == ["递交", "获批", "签收"], c["milestones"]
        finally:
            # 同一事件循环内清理(async 引擎连接池绑 loop)
            if hh_id:
                await _cleanup(hh_id)

    asyncio.run(run())


def test_passport_expiry_info():
    from datetime import date, timedelta
    today = date(2026, 7, 24)

    def f(value):
        return [{"field": "passport_expiry_date", "value": value}]

    # 已过期
    r = profile_crud.passport_expiry_info(f("2026-07-12"), today)
    assert r == {"date": "2026-07-12", "days_left": -12, "level": "expired"}, r
    # 临期边界:180 天整 → expiring,181 天 → ok
    d180 = (today + timedelta(days=180)).isoformat()
    r = profile_crud.passport_expiry_info(f(d180), today)
    assert r["days_left"] == 180 and r["level"] == "expiring", r
    d181 = (today + timedelta(days=181)).isoformat()
    r = profile_crud.passport_expiry_info(f(d181), today)
    assert r["days_left"] == 181 and r["level"] == "ok", r
    # 当天到期(0 天)→ expiring
    r = profile_crud.passport_expiry_info(f(today.isoformat()), today)
    assert r["days_left"] == 0 and r["level"] == "expiring", r
    # 无字段 / 空值 / 非法日期 → None
    assert profile_crud.passport_expiry_info([], today) is None
    assert profile_crud.passport_expiry_info(None, today) is None
    assert profile_crud.passport_expiry_info(f(""), today) is None
    assert profile_crud.passport_expiry_info(f("不是日期"), today) is None
    # 其它字段不影响
    r = profile_crud.passport_expiry_info([{"field": "passport_no", "value": "E1"}], today)
    assert r is None

    # attach:每人挂 passport_expiry,无字段为 None
    persons = [{"name": "甲", "fields": f("2026-07-12")}, {"name": "乙", "fields": []}]
    profile_crud.attach_passport_expiry(persons, today)
    assert persons[0]["passport_expiry"]["level"] == "expired", persons
    assert persons[1]["passport_expiry"] is None, persons


def test_field_conflicts():
    n = profile_crud._normalize_field_value
    # 归一化:性别中英映射 / 日期多格式 / 拼音名词序 / 证件号大小写 / 脱敏剔除
    assert n("gender", "Male") == "M" and n("gender", "男") == "M" and n("gender", "女") == "F"
    assert n("birth_date", "1969/03/25") == "1969-03-25"
    assert n("birth_date", "1969年3月25日") == "1969-03-25"
    # 拼音名:空格/粘连/大小写差异都归一(同一人才不报冲突);词序不同仍算不同
    assert n("name_en", "NI ZHAOHUI") == n("name_en", "NIZhaohui") == n("name", "NI Zhaohui")
    assert n("name_en", "NI ZHAOHUI") != n("name_en", "ZHAOHUI NI")
    # 中文名去空白
    assert n("name", " 倪朝晖 ") == "倪朝晖"
    assert n("id_number", " 310110x ") == "310110X"
    assert n("id_number", "[身份证]") == "" and n("name", "") == ""

    # 两源一致(归一化后同值)→ 无冲突
    s = [(1, "birth_date", "1969-03-25", "身份证.jpg", "id_card"),
         (1, "birth_date", "1969/03/25", "批复.pdf", "approval")]
    assert profile_crud.collect_field_conflicts(s) == {}
    # 拼音名粘连/空格差异 → 无冲突(真实数据样式)
    assert profile_crud.collect_field_conflicts(
        [(2, "name_en", "NICHENG", "倪成.jpg", "passport"),
         (2, "name_en", "NI CHENG", "倪成.jpg", "passport")]) == {}
    # 第三源不一致 → 冲突,两种值的来源都列出
    s.append((1, "birth_date", "1969-03-26", "无犯罪.pdf", "no_crime"))
    c = profile_crud.collect_field_conflicts(s)
    assert "birth_date" in c.get(1, {}), c
    vals = c[1]["birth_date"]["values"]
    assert len(vals) == 2, vals
    srcs = {s2["source"] for v in vals for s2 in v["sources"]}
    assert srcs == {"身份证.jpg", "批复.pdf", "无犯罪.pdf"}, srcs
    # 非白名单字段(declared)不比
    assert profile_crud.collect_field_conflicts(
        [(1, "phone", "138", "a", "kyc_form"), (1, "phone", "139", "b", "kyc_form")]) == {}
    # 不同人互不影响
    assert profile_crud.collect_field_conflicts(
        [(1, "gender", "男", "a", "id_card"), (2, "gender", "女", "b", "id_card")]) == {}
    # masked 值被剔除,不与有效值构成冲突
    assert profile_crud.collect_field_conflicts(
        [(1, "id_number", "[身份证]", "a", "id_card"),
         (1, "id_number", "310110196903251619", "b", "no_crime")]) == {}


def test_name_guard():
    assert profile_crud.plausible_person_name("倪朝晖") is True
    assert profile_crud.plausible_person_name("张三") is True
    assert profile_crud.plausible_person_name("阿不都·外力") is True
    assert profile_crud.plausible_person_name("钅 lil蝴哪") is False
    assert profile_crud.plausible_person_name("钅lil蝴哪") is False
    assert profile_crud.plausible_person_name("张3") is False
    assert profile_crud.plausible_person_name("A") is False
    assert profile_crud.plausible_person_name("") is False
    assert profile_crud.plausible_person_name(None) is False


if __name__ == "__main__":
    test_name_guard()
    print("PASS test_name_guard")
    test_passport_expiry_info()
    print("PASS test_passport_expiry_info")
    test_field_conflicts()
    print("PASS test_field_conflicts")
    test_profile_domain()
    print("PASS test_profile_domain")
    print("\n全部 4 个测试通过")
