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
from db.models import (CustomerFile, DocExtractResult, ProfileAsset, ProfileCase,
                       ProfileHousehold, ProfileImportTask, ProfilePerson, ProfilePersonField)

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

            # ---- declared 与 verified 同等,可更新(不因来源层跳过) ----
            res = await profile_crud.apply_extracted_fields_v2(hh_id,
                {"person_id": main_id, "matched_by": "id_number"}, [
                    {"key": "birth_date", "value": "1969/03/26", "column": "birth_date", "layer": "declared"},
                    {"key": "gender", "value": "女", "column": "gender"},  # AI 可更新 AI 值
                ])
            actions = {m["field"]: m["action"] for m in res["mapped"]}
            assert actions["birth_date"] == "updated", actions
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

            # ---- 资产写入(entity=asset):新建 → AI 判定去重 → masked 跳过 ----
            # 方案 C:去重靠 llm_service.judge_asset_duplicate,测试环境 mock 掉 LLM
            import llm_service
            _orig_judge = llm_service.judge_asset_duplicate
            _judge_calls: list = []
            _judge_response: dict = {"match_id": None, "confidence": 0, "reason": "mock 默认不匹配", "_fallback": False}

            def _mock_judge(new_attrs, candidates, **ctx):
                _judge_calls.append({"attrs": dict(new_attrs), "n_candidates": len(candidates),
                                     "candidate_ids": [c.id for c in candidates]})
                resp = dict(_judge_response)
                # 若响应指定 candidate_index,把它翻译成实际 candidates 里的 id
                if resp.get("_use_candidate_index") is not None:
                    idx = resp["_use_candidate_index"]
                    if 0 <= idx < len(candidates):
                        resp["match_id"] = candidates[idx].id
                    resp.pop("_use_candidate_index")
                return resp

            llm_service.judge_asset_duplicate = _mock_judge

            try:
                # 首次:无候选,LLM 不调
                aw = await profile_crud.apply_extracted_asset(hh_id, main_id, [
                    {"key": "address", "value": "政和路388弄35号301室"},
                    {"key": "area", "value": "344.31"},
                    {"key": "cert_no", "value": "杨2015021659"},
                    {"key": "account", "value": "[银行卡]"},  # masked 不入 attrs
                ], source_file_id=1)
                assert aw["stats"]["asset_created"] == 1, aw
                assert len(_judge_calls) == 0, f"首次无候选不该调 LLM: {_judge_calls}"
                asset_id = aw["asset_id"]
                assert asset_id
                assets = await profile_crud.list_assets(hh_id)
                assert len(assets) == 1, assets
                a = assets[0]
                assert a["name"] == "政和路388弄35号301室" and a["owner_person_id"] == main_id, a
                assert a["attrs"].get("cert_no") == "杨2015021659" and "account" not in a["attrs"], a

                # AI 判定命中 candidates[0]:去重更新不新建
                _judge_response = {"match_id": None, "confidence": 95, "reason": "mock 命中",
                                   "_fallback": False, "_use_candidate_index": 0}
                aw2 = await profile_crud.apply_extracted_asset(hh_id, main_id, [
                    {"key": "address", "value": "上海市杨浦区政和路388弄35号301室"},
                    {"key": "cert_no", "value": "HFDYZI(2015) No.021659"},
                    {"key": "right_type", "value": "公寓"},
                ], source_file_id=2)
                assert aw2["stats"]["asset_updated"] == 1 and aw2["asset_id"] == asset_id, aw2
                assert len(_judge_calls) == 1 and _judge_calls[0]["n_candidates"] == 1
                assets = await profile_crud.list_assets(hh_id)
                assert len(assets) == 1, assets
                assert assets[0]["attrs"].get("right_type") == "公寓", assets[0]["attrs"]

                # 全空不建行(LLM 不调)
                aw3 = await profile_crud.apply_extracted_asset(hh_id, None, [{"key": "area", "value": None}])
                assert aw3["asset_id"] is None and aw3["stats"]["asset_created"] == 0, aw3
                assert len(_judge_calls) == 1, "全空不该调 LLM"

                # 低置信(<60):新建
                _judge_response = {"match_id": None, "confidence": 55, "reason": "mock 低置信",
                                   "_fallback": False, "_use_candidate_index": 0}
                aw4 = await profile_crud.apply_extracted_asset(hh_id, main_id, [
                    {"key": "address", "value": "怀疑相关的一处房"},
                ], source_file_id=3)
                assert aw4["stats"]["asset_created"] == 1 and aw4["asset_id"] != asset_id, aw4

                # AI 判定为不同资产:新建
                _judge_response = {"match_id": None, "confidence": 0, "reason": "mock 不匹配",
                                   "_fallback": False}
                aw5 = await profile_crud.apply_extracted_asset(hh_id, main_id, [
                    {"key": "address", "value": "闵行区龙茗路100号501室"},
                    {"key": "cert_no", "value": "沪2020000001"},
                ], source_file_id=4)
                assert aw5["stats"]["asset_created"] == 1, aw5
                assets = await profile_crud.list_assets(hh_id)
                assert len(assets) == 3, assets

                # LLM 异常降级(_fallback=True):新建
                _judge_response = {"match_id": None, "confidence": 0, "reason": "LLM 超时",
                                   "_fallback": True}
                aw6 = await profile_crud.apply_extracted_asset(hh_id, main_id, [
                    {"key": "address", "value": "政和路388弄35号301(复式)"},
                    {"key": "cert_no", "value": "杨2015021659"},
                ], source_file_id=5)
                assert aw6["stats"]["asset_created"] == 1, aw6
                assets = await profile_crud.list_assets(hh_id)
                assert len(assets) == 4, assets
            finally:
                llm_service.judge_asset_duplicate = _orig_judge

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
            # 释放连接池:本文件有多个 asyncio.run,旧 loop 关闭后池内连接全失效
            from db.engine import async_engine
            await async_engine.dispose()

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
    s = [(1, "birth_date", "1969-03-25", "身份证.jpg", "id_card", 11),
         (1, "birth_date", "1969/03/25", "批复.pdf", "approval", 12)]
    assert profile_crud.collect_field_conflicts(s) == {}
    # 拼音名粘连/空格差异 → 无冲突(真实数据样式)
    assert profile_crud.collect_field_conflicts(
        [(2, "name_en", "NICHENG", "倪成.jpg", "passport", 13),
         (2, "name_en", "NI CHENG", "倪成.jpg", "passport", 13)]) == {}
    # 第三源不一致 → 冲突,两种值的来源都列出
    s.append((1, "birth_date", "1969-03-26", "无犯罪.pdf", "no_crime", 14))
    c = profile_crud.collect_field_conflicts(s)
    assert "birth_date" in c.get(1, {}), c
    vals = c[1]["birth_date"]["values"]
    assert len(vals) == 2, vals
    srcs = {s2["source"] for v in vals for s2 in v["sources"]}
    assert srcs == {"身份证.jpg", "批复.pdf", "无犯罪.pdf"}, srcs
    # 非白名单字段(declared)不比
    assert profile_crud.collect_field_conflicts(
        [(1, "phone", "138", "a", "kyc_form", 15), (1, "phone", "139", "b", "kyc_form", 16)]) == {}
    # 不同人互不影响
    assert profile_crud.collect_field_conflicts(
        [(1, "gender", "男", "a", "id_card", 17), (2, "gender", "女", "b", "id_card", 18)]) == {}
    # masked 值被剔除,不与有效值构成冲突
    assert profile_crud.collect_field_conflicts(
        [(1, "id_number", "[身份证]", "a", "id_card", 19),
         (1, "id_number", "310110196903251619", "b", "no_crime", 20)]) == {}


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


def test_normalize_relation():
    n = profile_crud.normalize_relation
    assert n("户主") == "户主" and n("本人") == "户主"
    assert n("妻子") == "配偶" and n("丈夫") == "配偶" and n("爱人") == "配偶"
    assert n("长子") == "子" and n("儿子") == "子" and n("长女") == "女"
    assert n("父亲") == "父" and n("母亲") == "母"
    # 超出 6 种合法关系 → None(skipped_invalid,人照建关系待确认)
    assert n("祖父") is None and n("儿媳") is None and n("兄弟") is None
    assert n("") is None and n(None) is None and n("  ") is None


_HH2 = "测试关系推导家庭"
_MAIN_IDN = "110101197001010011"
_MOM_IDN = "110101197501010022"


async def _mk_person(hh_id, name, fields):
    """建待确认 person 并写字段,返回 person_id。"""
    p = await profile_crud.create_person(hh_id, name)
    if fields:
        await profile_crud.apply_extracted_fields_v2(
            hh_id, {"person_id": p["id"], "matched_by": "test"},
            [{"key": k, "value": v, "column": k} for k, v in fields.items()])
    return p["id"]


async def _mk_result(task_id, file_id, doc_type, extracted, write_stats, code):
    """直接建行:CustomerFile + DocExtractResult(done)。"""
    async with async_session_maker() as s:
        cf = CustomerFile(file_code=code, import_task_id=task_id, status="done",
                          filename=f"{code}.pdf", doc_type=doc_type)
        s.add(cf)
        await s.flush()
        r = DocExtractResult(customer_file_id=cf.id, import_task_id=task_id,
                             file_id=code, doc_type=doc_type, status="done",
                             extracted=extracted, write_stats=write_stats)
        s.add(r)
        await s.commit()
        return r.id


def test_relation_infer():
    async def run():
        hh_id, task_id = None, None
        try:
            await _cleanup(None)
            async with async_session_maker() as s:  # 防历史残留
                await s.execute(delete(ProfileImportTask).where(
                    ProfileImportTask.client_name == _HH2))
                await s.commit()
            hh = await profile_crud.get_or_create_household(_HH2)
            hh_id = hh["id"]
            main_id = hh["main_person_id"]
            # 户主档案:1970 男 + 户籍地址
            await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": main_id, "matched_by": "test"}, [
                    {"key": "id_number", "value": _MAIN_IDN, "column": "id_number"},
                    {"key": "gender", "value": "男", "column": "gender"},
                    {"key": "birth_date", "value": "1970-01-01", "column": "birth_date"},
                    {"key": "hukou_address", "value": "测试省测试市测试路1号", "column": "hukou_address"},
                ])
            # 成员:B 妈妈 / C 小宝 / D 大弟(同姓同址小 20 岁) / E 李外人(异姓) / F 测邻居(异址)
            b_id = await _mk_person(hh_id, "测妈妈", {
                "id_number": _MOM_IDN, "gender": "女",
                "birth_date": "1975-01-01", "hukou_address": "测试省测试市测试路1号"})
            c_id = await _mk_person(hh_id, "测小宝", {
                "gender": "男", "birth_date": "2005-06-01"})
            d_id = await _mk_person(hh_id, "测大弟", {
                "gender": "男", "birth_date": "1990-01-01",
                "hukou_address": "测试省测试市测试路1号"})
            e_id = await _mk_person(hh_id, "李外人", {
                "gender": "男", "birth_date": "1990-01-01"})
            f_id = await _mk_person(hh_id, "测邻居", {
                "gender": "男", "birth_date": "1990-01-01",
                "hukou_address": "别的省别的市别的路9号"})

            # ---- _relation 通道:妻子→配偶落地;已有关系不再覆盖;非法值跳过 ----
            w = await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": b_id, "matched_by": "test"},
                [{"key": "relation", "value": "妻子", "column": "_relation"}])
            assert w["mapped"][0]["action"] == "relation_written", w["mapped"]
            rel = {p["id"]: p["relation_to_main"] for p in await profile_crud.list_persons(hh_id)}
            assert rel[b_id] == "配偶", rel
            w = await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": b_id, "matched_by": "test"},
                [{"key": "relation", "value": "子", "column": "_relation"}])
            assert w["mapped"][0]["action"] == "skipped_filled", w["mapped"]
            w = await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": c_id, "matched_by": "test"},
                [{"key": "relation", "value": "祖父", "column": "_relation"}])
            assert w["mapped"][0]["action"] == "skipped_invalid", w["mapped"]
            w = await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": main_id, "matched_by": "test"},
                [{"key": "relation", "value": "配偶", "column": "_relation"}])
            assert w["mapped"][0]["action"] == "skipped_filled", w["mapped"]
            # 户主守卫:非主申请人的"户主"卡不落(户口本户主 ≠ 画像主申请人)
            w = await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": c_id, "matched_by": "test"},
                [{"key": "relation", "value": "户主", "column": "_relation"}])
            assert w["mapped"][0]["action"] == "skipped_filled", w["mapped"]
            rel = {p["id"]: p["relation_to_main"] for p in await profile_crud.list_persons(hh_id)}
            assert rel[c_id] == "待确认", rel
            # 复位 B 为待确认,交给推导来写
            await profile_crud.set_person_relation(b_id, "待确认")

            # ---- 造提取结果:出生证(父母齐)+结婚证(户主持证,配偶=测妈妈) ----
            async with async_session_maker() as s:
                t = ProfileImportTask(filename="t.xlsx", client_name=_HH2,
                                      household_id=hh_id, status="done")
                s.add(t)
                await s.flush()
                task_id = t.id
                await s.commit()
            await _mk_result(task_id, "f-birth", "birth_cert", {
                "name": "测小宝", "father_name": _HH2, "father_id_number": _MAIN_IDN,
                "mother_name": "测妈妈", "mother_id_number": _MOM_IDN},
                {"person_id": c_id}, "test-code-birth")
            await _mk_result(task_id, "f-marriage", "marriage_cert", {
                "holder_name": _HH2, "spouse_name": "测妈妈"},
                {"person_id": main_id}, "test-code-marriage")

            # ---- 推导:C1 出生证父亲=户主 → 母亲写配偶;C3 同姓年长差 → D 写子 ----
            n_before = await profile_crud.count_persons(hh_id)
            r = await profile_crud.infer_family_relations(hh_id)
            assert r["checked_results"] == 2, r
            bases = {(i["person_id"], i["relation"], i["basis"]) for i in r["inferred"]}
            assert (b_id, "配偶", "birth_cert:father_is_main") in bases, bases
            assert (d_id, "子", "heuristic:surname+age_gap+addr") in bases, bases
            rel = {p["id"]: p["relation_to_main"] for p in await profile_crud.list_persons(hh_id)}
            assert rel[b_id] == "配偶", rel          # C1(或 C2)写配偶
            assert rel[d_id] == "子", rel            # C3 启发式(同姓同址小 20 岁)
            assert rel[c_id] == "子", rel            # C3 启发式(同姓无址小 35 岁)
            assert rel[e_id] == "待确认", rel        # 异姓不推
            assert rel[f_id] == "待确认", rel        # 地址冲突不推
            assert await profile_crud.count_persons(hh_id) == n_before  # 不建人

            # ---- 幂等:二次推导零写入 ----
            r2 = await profile_crud.infer_family_relations(hh_id)
            assert r2["inferred"] == [], r2
        finally:
            async with async_session_maker() as s:
                if task_id:
                    await s.execute(delete(DocExtractResult).where(
                        DocExtractResult.import_task_id == task_id))
                    await s.execute(delete(CustomerFile).where(
                        CustomerFile.import_task_id == task_id))
                    t = await s.get(ProfileImportTask, task_id)
                    if t:
                        await s.delete(t)
                    await s.commit()
            if hh_id:
                await _cleanup(hh_id)
            from db.engine import async_engine
            await async_engine.dispose()

    asyncio.run(run())


_HH3 = "测试婚姻多人家庭"
_M_IDN = "110101197001010033"
_M_MOM = "110101197501010044"
_M_BIG = "110101196801010055"
_M_SELF = "110101199901010066"


def test_relation_infer_marriage_multi():
    """marriage_cert 多人模式(rule v2):cert_role 定位 + 证件号强归因 + 配偶自动建卡幂等。"""
    async def run():
        hh_id, task_id = None, None
        try:
            async with async_session_maker() as s:  # 防历史残留
                await s.execute(delete(ProfileImportTask).where(
                    ProfileImportTask.client_name == _HH3))
                await s.commit()
            hh = await profile_crud.get_or_create_household(_HH3)
            hh_id = hh["id"]
            main_id = hh["main_person_id"]
            await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": main_id, "matched_by": "test"}, [
                    {"key": "name", "value": "测婚户主", "column": "name"},
                    {"key": "id_number", "value": _M_IDN, "column": "id_number"},
                    {"key": "gender", "value": "男", "column": "gender"},
                ])
            spouse_id = await _mk_person(hh_id, "测婚妈妈", {"id_number": _M_MOM, "gender": "女"})
            big_id = await _mk_person(hh_id, "测婚大哥", {"id_number": _M_BIG, "gender": "男"})

            async with async_session_maker() as s:
                t = ProfileImportTask(filename="t.xlsx", client_name=_HH3,
                                      household_id=hh_id, status="done")
                s.add(t)
                await s.flush()
                task_id = t.id
                await s.commit()

            # ---- 多人格式:户主持证( persons[0]=持证人),配偶证件号强归因 → 配偶写'配偶' ----
            await _mk_result(task_id, "f-marr-multi", "marriage_cert", {"persons": [
                {"cert_role": "持证人", "name": "测婚户主", "id_number": _M_IDN,
                 "spouse_name": "测婚妈妈", "marital_status": "已婚",
                 "marriage_date": "2020-05-20"},
                {"cert_role": "配偶", "name": "测婚妈妈", "id_number": _M_MOM,
                 "spouse_name": "测婚户主", "marital_status": "已婚",
                 "marriage_date": "2020-05-20"}]},
                {"person_id": main_id,
                 "persons": [{"person_id": main_id}, {"person_id": spouse_id}]},
                "test-code-marr-multi")

            # ---- 多人格式乱序兜底:persons[0]=配偶(=户主),cert_role 找到真持证人 → 持证人写'配偶' ----
            await _mk_result(task_id, "f-marr-flip", "marriage_cert", {"persons": [
                {"cert_role": "配偶", "name": "测婚户主", "id_number": _M_IDN,
                 "spouse_name": "测婚大哥", "marital_status": "已婚"},
                {"cert_role": "持证人", "name": "测婚大哥", "id_number": _M_BIG,
                 "spouse_name": "测婚户主", "marital_status": "已婚"}]},
                {"person_id": main_id,
                 "persons": [{"person_id": main_id}, {"person_id": big_id}]},
                "test-code-marr-flip")

            r = await profile_crud.infer_family_relations(hh_id)
            bases = {(i["person_id"], i["relation"], i["basis"]) for i in r["inferred"]}
            assert (spouse_id, "配偶", "marriage_cert:holder_is_main") in bases, bases
            assert (big_id, "配偶", "marriage_cert:spouse_is_main") in bases, bases
            rel = {p["id"]: p["relation_to_main"] for p in await profile_crud.list_persons(hh_id)}
            assert rel[spouse_id] == "配偶" and rel[big_id] == "配偶", rel

            # ---- 幂等:二次推导零写入 ----
            r2 = await profile_crud.infer_family_relations(hh_id)
            assert r2["inferred"] == [], r2

            # ---- 配偶自动建卡(模拟 _extract_one_multi 对配偶对象的 apply)+ 重跑幂等 ----
            spouse_items = [
                {"key": "name", "value": "测婚自建", "column": "name"},
                {"key": "id_number", "value": _M_SELF, "column": "id_number"},
                {"key": "spouse_name", "value": "测婚户主", "column": "spouse_name"},
                {"key": "marital_status", "value": "已婚", "column": "marital_status"},
                {"key": "marriage_date", "value": "2020-05-20", "column": "marriage_date"},
            ]
            res1 = await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": None, "matched_by": None}, spouse_items)
            new_pid = res1["person_id"]
            assert new_pid, res1
            assert res1["write_stats"].get("person_created"), res1["write_stats"]
            n1 = await profile_crud.count_persons(hh_id)
            persons = await profile_crud.list_persons(hh_id)
            fmap = {f["field"]: f for f in next(p for p in persons if p["id"] == new_pid)["fields"]}
            assert fmap["spouse_name"]["value"] == "测婚户主", fmap
            assert fmap["spouse_name"]["label"] == "配偶姓名", fmap["spouse_name"]
            assert fmap["spouse_name"]["layer"] == "verified", fmap["spouse_name"]

            # 同一提取再跑一遍(重复导入/重新生成):不新建人,字段 skipped_same
            m = await profile_crud.find_person_match(hh_id, _M_SELF, "测婚自建")
            assert m["person_id"] == new_pid, m
            res2 = await profile_crud.apply_extracted_fields_v2(hh_id, m, spouse_items)
            assert res2["person_id"] == new_pid, res2
            assert await profile_crud.count_persons(hh_id) == n1
            actions = {mt["field"]: mt["action"] for mt in res2["mapped"]}
            assert actions["spouse_name"] == "skipped_same", actions
            assert actions["marital_status"] == "skipped_same", actions
        finally:
            async with async_session_maker() as s:
                if task_id:
                    await s.execute(delete(DocExtractResult).where(
                        DocExtractResult.import_task_id == task_id))
                    await s.execute(delete(CustomerFile).where(
                        CustomerFile.import_task_id == task_id))
                    t = await s.get(ProfileImportTask, task_id)
                    if t:
                        await s.delete(t)
                    await s.commit()
            if hh_id:
                await _cleanup(hh_id)
            from db.engine import async_engine
            await async_engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    test_name_guard()
    print("PASS test_name_guard")
    test_normalize_relation()
    print("PASS test_normalize_relation")
    test_passport_expiry_info()
    print("PASS test_passport_expiry_info")
    test_field_conflicts()
    print("PASS test_field_conflicts")
    test_profile_domain()
    print("PASS test_profile_domain")
    test_relation_infer()
    print("PASS test_relation_infer")
    test_relation_infer_marriage_multi()
    print("PASS test_relation_infer_marriage_multi")
    print("\n全部 7 个测试通过")
