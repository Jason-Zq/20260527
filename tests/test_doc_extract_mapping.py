"""doc_extract_crud 归因 + 只补空写库测试(依赖真实 DB,测后清理)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_doc_extract_mapping.py
"""
import sys
import os
import asyncio
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete, select

from db import doc_extract_crud
from db.engine import async_session_maker
from db.models import Client, FamilyMember

_CLIENT_NAME = "测试提取甲"
_MEMBER_NAME = "测试配偶乙"
_NEWCOMER_NAME = "测试新人丙"
_CLIENT_IDN = "110101199001011234"
_MEMBER_IDN = "220202199002022022"


def test_pure_functions():
    assert doc_extract_crud.is_masked("[身份证]") is True
    assert doc_extract_crud.is_masked("身份证号:[身份证]") is True
    assert doc_extract_crud.is_masked("12*34") is True
    assert doc_extract_crud.is_masked(_CLIENT_IDN) is False
    assert doc_extract_crud.is_masked("") is False
    assert doc_extract_crud.is_masked(None) is False
    assert doc_extract_crud.valid_id_number(_CLIENT_IDN) is True
    assert doc_extract_crud.valid_id_number("11010119900101123X") is True
    assert doc_extract_crud.valid_id_number("11010119900101123x") is True
    assert doc_extract_crud.valid_id_number("12345") is False
    assert doc_extract_crud.valid_id_number("11010119900101123") is False  # 17 位
    assert doc_extract_crud.valid_id_number("[身份证]") is False


def test_attribution_and_write():
    async def run():
        client_id = None
        try:
            # ---- 准备:测试客户(证件号已填) + 配偶成员 ----
            async with async_session_maker() as s:
                # 防重:清掉历史测试残留
                old = (await s.execute(select(Client).where(Client.name == _CLIENT_NAME))).scalars().all()
                for o in old:
                    await s.execute(delete(FamilyMember).where(FamilyMember.client_id == o.id))
                    await s.delete(o)
                c = Client(name=_CLIENT_NAME, id_number=_CLIENT_IDN,
                           created_at=datetime.now(), updated_at=datetime.now())
                s.add(c)
                await s.flush()
                client_id = c.id
                s.add(FamilyMember(client_id=client_id, relation="配偶", name=_MEMBER_NAME,
                                   id_number=_MEMBER_IDN,
                                   created_at=datetime.now(), updated_at=datetime.now()))
                await s.commit()

            # ---- case 1: 证件号命中客户本人,只补空 ----
            match = await doc_extract_crud.find_person_match(client_id, _CLIENT_IDN, None)
            assert match == {"entity": "client", "row_id": client_id, "matched_by": "id_number"}, match
            res = await doc_extract_crud.apply_extracted_fields(client_id, match, [
                {"key": "name", "label": "姓名", "value": _CLIENT_NAME, "column": "name"},
                {"key": "gender", "label": "性别", "value": "男", "column": "gender"},
                {"key": "birth_date", "label": "出生日期", "value": "1990-01-01", "column": "birth_date"},
                {"key": "id_number", "label": "公民身份号码", "value": "[身份证]", "column": "id_number"},
                {"key": "issuing", "label": "签发机关", "value": "某公安局", "column": None},
                {"key": "hukou_address", "label": "住址", "value": "北京市朝阳区某路1号", "column": "hukou_address"},
            ])
            actions = {m["key"]: m["action"] for m in res["mapped"]}
            assert actions == {
                "name": "skipped_filled", "gender": "written", "birth_date": "written",
                "id_number": "skipped_masked", "issuing": "unmapped", "hukou_address": "written",
            }, actions
            assert res["write_stats"]["client_fields"] == 3, res["write_stats"]
            async with async_session_maker() as s:
                c = await s.get(Client, client_id)
                assert c.gender == "男" and c.birth_date == date(1990, 1, 1), (c.gender, c.birth_date)
                assert c.hukou_address == "北京市朝阳区某路1号"
                assert c.id_number == _CLIENT_IDN  # masked 未写入

            # 只补空:已有值不覆盖
            res2 = await doc_extract_crud.apply_extracted_fields(client_id, match, [
                {"key": "gender", "label": "性别", "value": "女", "column": "gender"},
            ])
            assert res2["mapped"][0]["action"] == "skipped_filled", res2["mapped"]
            async with async_session_maker() as s:
                c = await s.get(Client, client_id)
                assert c.gender == "男"

            # ---- case 2: 姓名命中成员;member 无 hukou_address 列 → unmapped ----
            match = await doc_extract_crud.find_person_match(client_id, None, _MEMBER_NAME)
            assert match["entity"] == "member" and match["matched_by"] == "name", match
            res = await doc_extract_crud.apply_extracted_fields(client_id, match, [
                {"key": "birth_date", "label": "出生日期", "value": "1990/02/02", "column": "birth_date"},
                {"key": "hukou_address", "label": "住址", "value": "上海某处", "column": "hukou_address"},
                {"key": "id_number", "label": "公民身份号码", "value": "123", "column": "id_number"},
            ])
            actions = {m["key"]: m["action"] for m in res["mapped"]}
            assert actions == {"birth_date": "written", "hukou_address": "unmapped",
                               "id_number": "skipped_invalid"}, actions
            async with async_session_maker() as s:
                mrow = (await s.execute(select(FamilyMember).where(
                    FamilyMember.client_id == client_id, FamilyMember.name == _MEMBER_NAME))).scalar_one()
                assert mrow.birth_date == date(1990, 2, 2), mrow.birth_date

            # ---- case 3: 无命中 → 新建成员 relation='待确认' ----
            match = await doc_extract_crud.find_person_match(client_id, "330303200003033033", _NEWCOMER_NAME)
            assert match["entity"] is None, match
            res = await doc_extract_crud.apply_extracted_fields(client_id, match, [
                {"key": "name", "label": "姓名", "value": _NEWCOMER_NAME, "column": "name"},
                {"key": "gender", "label": "性别", "value": "女", "column": "gender"},
                {"key": "id_number", "label": "公民身份号码", "value": "330303200003033033", "column": "id_number"},
            ])
            assert res["write_stats"]["member_created"] == 1, res["write_stats"]
            assert res["write_stats"]["member_fields"] >= 2, res["write_stats"]
            async with async_session_maker() as s:
                nrow = (await s.execute(select(FamilyMember).where(
                    FamilyMember.client_id == client_id, FamilyMember.name == _NEWCOMER_NAME))).scalar_one()
                assert nrow.relation == "待确认", nrow.relation
                assert nrow.gender == "女" and nrow.id_number == "330303200003033033"

            # ---- case 4: clients.id_number unique 冲突 → skipped_conflict ----
            # 另一个客户已占用该证件号,本客户(此时 id_number 非空,先清空测)不会撞;
            # 直接对"配偶成员"验证实体隔离即可,此处验证 client 侧冲突分支:
            async with async_session_maker() as s:
                other = Client(name="测试提取丁", id_number="440404199404044044",
                               created_at=datetime.now(), updated_at=datetime.now())
                s.add(other)
                await s.commit()
                other_id = other.id
            try:
                async with async_session_maker() as s:
                    c = await s.get(Client, client_id)
                    c.id_number = None  # 先清空本客户证件号
                    await s.commit()
                res = await doc_extract_crud.apply_extracted_fields(client_id,
                    {"entity": "client", "row_id": client_id, "matched_by": "name"},
                    [{"key": "id_number", "label": "公民身份号码", "value": "440404199404044044", "column": "id_number"}])
                assert res["mapped"][0]["action"] == "skipped_conflict", res["mapped"]
            finally:
                async with async_session_maker() as s:
                    o = await s.get(Client, other_id)
                    if o:
                        await s.delete(o)
                        await s.commit()
        finally:
            if client_id:
                async with async_session_maker() as s:
                    await s.execute(delete(FamilyMember).where(FamilyMember.client_id == client_id))
                    c = await s.get(Client, client_id)
                    if c:
                        await s.delete(c)
                    await s.commit()

    asyncio.run(run())


if __name__ == "__main__":
    test_pure_functions()
    print("PASS test_pure_functions")
    test_attribution_and_write()
    print("PASS test_attribution_and_write")
    print("\n全部 2 个测试通过")
