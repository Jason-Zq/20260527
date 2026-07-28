"""人员去重测试(简体/繁体/拼音 同一人不重复建卡;依赖真实 DB,测后清理)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_person_dedup.py
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete, select

from db import profile_crud
from db.engine import async_session_maker
from db.models import ProfileHousehold, ProfilePerson, ProfilePersonField

_HH = "测试去重家庭"
_IDN = "31011019690325161X"


async def _cleanup(household_id):
    async with async_session_maker() as s:
        if household_id:
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


def test_pure_helpers():
    # 繁→简折叠 + 去空白
    assert profile_crud._fold_cjk("測試 去重") == "测试去重"
    assert profile_crud._fold_cjk("倪朝暉") == "倪朝晖"
    assert profile_crud._fold_cjk("简体不变") == "简体不变"
    assert profile_crud._fold_cjk("") == "" and profile_crud._fold_cjk(None) == ""
    # 证件号归一化:去非字母数字 + 大写
    assert profile_crud._normalize_id_number(" 310110x ") == "310110X"
    assert profile_crud._normalize_id_number("1101-10 19") == "11011019"
    assert profile_crud._normalize_id_number("") == "" and profile_crud._normalize_id_number(None) == ""
    # 拼音连写变体:姓前/姓后两序;拉丁名直接连写;空/单字
    assert profile_crud._pinyin_glued_variants("倪朝晖") == {"NIZHAOHUI", "ZHAOHUINI"}
    assert profile_crud._pinyin_glued_variants("王建国") == {"WANGJIANGUO", "JIANGUOWANG"}
    assert profile_crud._pinyin_glued_variants("NI ZHAOHUI") == {"NIZHAOHUI"}
    assert profile_crud._pinyin_glued_variants("王") == {"WANG"}
    assert profile_crud._pinyin_glued_variants("") == set()


def test_person_dedup_db():
    async def run():
        hh_id = None
        try:
            await _cleanup(None)  # 防历史残留
            hh = await profile_crud.get_or_create_household(_HH)
            hh_id = hh["id"]
            main_id = hh["main_person_id"]

            # ---- 家庭名繁简折叠:繁体名命中已有简体家庭,不重复建 ----
            hh2 = await profile_crud.get_or_create_household("測試去重家庭")
            assert hh2["id"] == hh_id, hh2
            assert await profile_crud.count_persons(hh_id) == 1

            # ---- 户主写字段:证件号(小写 x)+ name_en ----
            await profile_crud.apply_extracted_fields_v2(hh_id,
                {"person_id": main_id, "matched_by": "test"}, [
                    {"key": "id_number", "value": _IDN.lower(), "column": "id_number"},
                    {"key": "name_en", "value": "CE SHI", "column": "name_en"},
                ])

            # ---- 证件号归一化:空格/大小写变体命中 ----
            m = await profile_crud.find_person_match(hh_id, "310110 19690325161x", None)
            assert m == {"person_id": main_id, "matched_by": "id_number"}, m

            # ---- 姓名繁简折叠:繁体输入命中简体卡 ----
            m = await profile_crud.find_person_match(hh_id, None, "測試去重家庭")
            assert m == {"person_id": main_id, "matched_by": "name"}, m

            # ---- 拼音互转(英文→中文卡):王建国无 name_en 字段,英文输入仍命中 ----
            res = await profile_crud.apply_extracted_fields_v2(hh_id,
                {"person_id": None, "matched_by": None}, [
                    {"key": "name", "value": "王建国", "column": "name"},
                ])
            assert res["write_stats"]["person_created"] == 1, res["write_stats"]
            wang_id = res["write_stats"]["person_id"]
            m = await profile_crud.find_person_match(hh_id, None, None, "Wang Jianguo")
            assert m == {"person_id": wang_id, "matched_by": "pinyin"}, m
            m = await profile_crud.find_person_match(hh_id, None, None, "JIANGUO WANG")  # 名前姓后
            assert m == {"person_id": wang_id, "matched_by": "pinyin"}, m
            # 负例:不同拼音不中
            m = await profile_crud.find_person_match(hh_id, None, None, "LI QIANG")
            assert m == {"person_id": None, "matched_by": None}, m
            # 负例:仅姓不完整,不误中
            m = await profile_crud.find_person_match(hh_id, None, None, "WANG")
            assert m == {"person_id": None, "matched_by": None}, m

            # ---- 拼音互转(中文→英文卡):户主有 name_en=CE SHI,繁体中文名输入也能命中 ----
            m = await profile_crud.find_person_match(hh_id, None, "測試去重家庭")
            assert m["person_id"] == main_id and m["matched_by"] == "name", m  # 折叠先中

            # ---- create_person 查重:繁体名命中不新建 ----
            p = await profile_crud.create_person(hh_id, "王建国")
            assert p["id"] == wang_id and p.get("deduped") is True, p
            assert p.get("matched_by") == "name", p
            # 拉丁名输入走 name_en/拼音路查重
            p = await profile_crud.create_person(hh_id, "WANG JIANGUO")
            assert p["id"] == wang_id and p.get("deduped") is True, p
            # 新人照建(无 deduped 标记)
            p = await profile_crud.create_person(hh_id, "赵新蕾")
            assert p["id"] not in (main_id, wang_id) and not p.get("deduped"), p
            assert await profile_crud.count_persons(hh_id) == 3

            # ---- 端到端:繁体输入经 find_person_match 拦截,不会走到建人分支 ----
            m = await profile_crud.find_person_match(hh_id, None, "王建國")
            assert m == {"person_id": wang_id, "matched_by": "name"}, m
            assert await profile_crud.count_persons(hh_id) == 3  # 无新增
        finally:
            if hh_id:
                await _cleanup(hh_id)
            from db.engine import async_engine
            await async_engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    test_pure_helpers()
    print("PASS test_pure_helpers")
    test_person_dedup_db()
    print("PASS test_person_dedup_db")
    print("\n全部 2 个测试通过")
