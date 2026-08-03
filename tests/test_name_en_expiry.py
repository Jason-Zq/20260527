"""name_en 建人 / sponsor 关系推导 / 证件到期提醒 测试(纯函数 + DB,测后清理)。

覆盖 2026-07-30 两个功能:
1) 英文证件(批复/准证)无中文名时按 name_en 建人卡(≥2 词拉丁名门槛);
2) approval 规则 sponsor_name(家属准证主签持证人)→ infer_family_relations 写 子/女;
3) approval_expiry_date / id_card_expiry_date 入库 + list_expiry_reminders 全库提醒。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_name_en_expiry.py
"""
import sys
import os
import asyncio
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete, select

import extract_rules
import profile_import_service as pis
from db import profile_crud
from db.engine import async_session_maker
from db.models import ProfileHousehold, ProfilePerson, ProfilePersonField

_HH = "测试英文建人家庭"


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


def test_plausible_latin_name():
    ok = profile_crud.plausible_latin_name
    assert ok("LIU SONGHAO") is True
    assert ok("Song Guoe") is True
    assert ok("MARIA DA SILVA") is True
    assert ok("Jean-Luc Picard") is True
    # 单词噪声(OCR 把证件词当名)、过短、含数字、超 4 词 一律拒
    assert ok("SGWORKPASS") is False
    assert ok("PASSPORT") is False
    assert ok("A B") is False
    assert ok("LIU SONGHAO2") is False
    assert ok("A B C D E") is False
    assert ok("") is False and ok(None) is False


def test_is_self_sponsor():
    f = pis._is_self_sponsor
    # 拉丁 vs 拉丁:词序无关
    assert f("SONG GUOE", None, "GUOE SONG") is True
    # CJK vs CJK
    assert f("宋国娥", "宋国娥", None) is True
    # 跨字母表:拉丁连写 vs 中文名拼音
    assert f("SONGGUOE", "宋国娥", None) is True
    assert f("SONG GUOE", "宋国娥", None) is True
    # 非本人
    assert f("SONG GUOE", None, "LIU SONGHAO") is False
    assert f("LIU SONGHAO", "宋国娥", None) is False
    assert f("", "宋国娥", None) is False


def test_clean_field_items_drops_self_sponsor():
    rule = extract_rules.get_rule("approval")
    # sponsor == 本人 name_en → 丢弃
    items, *_ = pis._clean_field_items(rule, {
        "name_en": "SONG GUOE", "sponsor_name": "SONG GUOE", "gender": "女"})
    assert not any(it["column"] == "sponsor_name" for it in items), items
    # sponsor == 本人中文名(跨字母表拼音) → 丢弃
    items, *_ = pis._clean_field_items(rule, {
        "name": "宋国娥", "name_en": "SONG GUOE", "sponsor_name": "SONGGUOE"})
    assert not any(it["column"] == "sponsor_name" for it in items), items
    # sponsor 是别人 → 保留
    items, *_ = pis._clean_field_items(rule, {
        "name_en": "LIU SONGHAO", "sponsor_name": "SONG GUOE"})
    sponsor = next(it for it in items if it["column"] == "sponsor_name")
    assert sponsor["value"] == "SONG GUOE", items
    # LLM 占位字符串("None"/"null")按空值处理,不进 field_items
    items, *_ = pis._clean_field_items(rule, {
        "name_en": "SONG GUOE", "sponsor_name": "None", "birth_place": "null"})
    assert not any(it["column"] == "sponsor_name" for it in items), items
    assert not any(it["column"] == "birth_place" for it in items), items


async def _run_name_en_and_sponsor():
    hh_id = None
    try:
        await _cleanup(None)
        hh = await profile_crud.get_or_create_household(_HH)
        hh_id = hh["id"]
        main_id = hh["main_person_id"]

        # ---- 户主:英文名 + 出生日期 + 性别(verified) ----
        w = await profile_crud.apply_extracted_fields_v2(
            hh_id, {"person_id": main_id, "matched_by": "test"}, [
                {"key": "name_en", "value": "SONG GUOE", "column": "name_en"},
                {"key": "birth_date", "value": "1976-12-30", "column": "birth_date"},
                {"key": "gender", "value": "女", "column": "gender"},
            ])
        assert w["write_stats"]["written"] == 3, w["write_stats"]

        # ---- 1) name_en 建卡:LIU SONGHAO(DP 卡,无中文名) ----
        w = await profile_crud.apply_extracted_fields_v2(
            hh_id, {"person_id": None, "matched_by": None}, [
                {"key": "name_en", "value": "LIU SONGHAO", "column": "name_en"},
                {"key": "gender", "value": "男", "column": "gender"},
                {"key": "birth_date", "value": "2009-04-10", "column": "birth_date"},
                {"key": "sponsor_name", "value": "SONG GUOE", "column": "sponsor_name"},
                {"key": "expiry_date", "value": "2028-06-22",
                 "column": "approval_expiry_date"},
            ])
        dep_id = w.get("person_id")
        assert dep_id and dep_id != main_id, w
        assert w["write_stats"]["person_created"] == 1, w["write_stats"]
        persons = await profile_crud.list_persons(hh_id)
        dep = next(p for p in persons if p["id"] == dep_id)
        assert dep["name"] == "LIU SONGHAO", dep
        assert dep["relation_to_main"] == "待确认", dep

        # ---- 2) 拼音互转归并:中文证件到达不重复建卡 ----
        match = await profile_crud.find_person_match(hh_id, None, "刘松昊", None)
        assert match.get("person_id") == dep_id, match
        assert await profile_crud.count_persons(hh_id) == 2

        # ---- 3) 垃圾拉丁名不建人 ----
        w = await profile_crud.apply_extracted_fields_v2(
            hh_id, {"person_id": None, "matched_by": None}, [
                {"key": "name_en", "value": "SGWORKPASS", "column": "name_en"},
            ])
        assert w.get("person_id") is None, w
        assert await profile_crud.count_persons(hh_id) == 2

        # ---- 4) sponsor 关系推导:持证人=户主 + 年龄差>15 → 子 ----
        r = await profile_crud.infer_family_relations(hh_id)
        hits = [i for i in r["inferred"] if i["person_id"] == dep_id]
        assert hits and hits[0]["relation"] == "子", r
        assert hits[0]["basis"] == "sponsor:main_pass_holder", hits
        persons = await profile_crud.list_persons(hh_id)
        dep = next(p for p in persons if p["id"] == dep_id)
        assert dep["relation_to_main"] == "子", dep
        # 幂等:二次推导不再产出
        r2 = await profile_crud.infer_family_relations(hh_id)
        assert not [i for i in r2["inferred"] if i["person_id"] == dep_id], r2
    finally:
        await _cleanup(hh_id)


async def _run_expiry_reminders():
    hh_id = None
    try:
        await _cleanup(None)
        hh = await profile_crud.get_or_create_household(_HH)
        hh_id = hh["id"]
        main_id = hh["main_person_id"]
        today = date.today()
        expiring = (today + timedelta(days=30)).isoformat()
        expired = (today - timedelta(days=10)).isoformat()
        ok_date = (today + timedelta(days=400)).isoformat()

        await profile_crud.apply_extracted_fields_v2(
            hh_id, {"person_id": main_id, "matched_by": "test"}, [
                {"key": "expiry_date", "value": expiring,
                 "column": "approval_expiry_date"},
                {"key": "id_card_expiry_date", "value": expired,
                 "column": "id_card_expiry_date"},
                {"key": "passport_expiry_date", "value": ok_date,
                 "column": "passport_expiry_date"},
            ])

        # 非法日期不入库(DATE_FIELDS 校验)
        w = await profile_crud.apply_extracted_fields_v2(
            hh_id, {"person_id": main_id, "matched_by": "test"}, [
                {"key": "expiry_date", "value": "不是日期",
                 "column": "approval_expiry_date"},
            ])
        assert w["mapped"][0]["action"] == "skipped_invalid", w["mapped"]

        # ---- 默认:只回 active(expired+expiring),按剩余天数升序 ----
        kw = {"keyword": _HH}
        res = await profile_crud.list_expiry_reminders(**kw)
        ours = [i for i in res["items"] if i["household_id"] == hh_id]
        assert len(ours) == 2, ours
        assert ours[0]["level"] == "expired" and ours[0]["days_left"] == -10, ours
        assert ours[1]["level"] == "expiring" and ours[1]["days_left"] == 30, ours
        assert {i["credential_type"] for i in ours} == {"身份证", "准证/批复"}, ours

        # ---- include_ok: 正常证件也回 ----
        res = await profile_crud.list_expiry_reminders(include_ok=True, **kw)
        ours = [i for i in res["items"] if i["household_id"] == hh_id]
        assert len(ours) == 3, ours
        assert ours[-1]["level"] == "ok" and ours[-1]["days_left"] == 400, ours

        # ---- 阈值收窄:days=20 → 只剩 expired ----
        res = await profile_crud.list_expiry_reminders(days=20, **kw)
        ours = [i for i in res["items"] if i["household_id"] == hh_id]
        assert [i["level"] for i in ours] == ["expired"], ours
    finally:
        await _cleanup(hh_id)


def test_name_en_create_and_sponsor_infer_db():
    asyncio.run(_run_name_en_and_sponsor())


def test_expiry_reminders_db():
    asyncio.run(_run_expiry_reminders())


async def _run_db_tests():
    # asyncpg 连接池绑 loop:全部 DB 测试共用一个 asyncio.run,不能各自起 loop
    await _run_name_en_and_sponsor()
    print("PASS test_name_en_create_and_sponsor_infer_db")
    await _run_expiry_reminders()
    print("PASS test_expiry_reminders_db")


if __name__ == "__main__":
    test_plausible_latin_name()
    print("PASS test_plausible_latin_name")
    test_is_self_sponsor()
    print("PASS test_is_self_sponsor")
    test_clean_field_items_drops_self_sponsor()
    print("PASS test_clean_field_items_drops_self_sponsor")
    asyncio.run(_run_db_tests())
    print("\n全部 5 个测试通过")
