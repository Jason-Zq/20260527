"""credibility.compute_field_credibility 打分测试(纯函数,不依赖 DB 连接)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_credibility.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from credibility import compute_field_credibility as cred


def _src(value, fid, doc_type="id_card", source=None):
    return {"value": value, "customer_file_id": fid,
            "doc_type": doc_type, "source": source or f"file{fid}.pdf"}


def test_human_status_short_circuit():
    # confirmed → 100/高;corrected → 100/高(理由文案区分)
    r = cred(layer="verified", status="confirmed", current_value="1990-01-01",
             field="birth_date", samples=[_src("1990-01-01", 1)])
    assert r["score"] == 100 and r["level"] == "高", r
    assert r["reasons"] == ["人工已确认"], r
    r = cred(layer="declared", status="corrected", current_value="X",
             field="phone", samples=[])
    assert r["score"] == 100 and r["level"] == "高" and r["reasons"] == ["人工已修正"], r


def test_verified_base_mid():
    # 单来源官方证件 AI = 70 中
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=[_src("1990-01-01", 1)])
    assert r["score"] == 70 and r["level"] == "中", r
    assert r["corroboration"] == 1 and r["conflict_count"] == 0, r
    assert "官方证件来源" in r["reasons"][0], r


def test_declared_base():
    # 自报单来源 = 50 中
    r = cred(layer="declared", status="ai", current_value="13612345678",
             field="phone", samples=[_src("13612345678", 1, "kyc_form")])
    assert r["score"] == 50 and r["level"] == "中", r
    assert "客户自报来源" in r["reasons"][0], r


def test_corroboration_bonus():
    # 2 文件一致 = 70+15=85 高;3 文件一致 = 70+20=90;跨类型再 +5
    s2 = [_src("1990-01-01", 1), _src("1990-01-01", 2)]
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=s2)
    assert r["score"] == 85 and r["level"] == "高" and r["corroboration"] == 2, r
    s3 = [_src("1990-01-01", 1), _src("1990-01-01", 2), _src("1990-01-01", 3)]
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=s3)
    assert r["score"] == 90 and r["corroboration"] == 3, r
    s3t = [_src("1990-01-01", 1, "id_card"), _src("1990-01-01", 2, "passport"),
           _src("1990-01-01", 3, "hukou")]
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=s3t)
    assert r["score"] == 95 and "跨证件类型互证" in r["reasons"], r
    # 同一文件重复出现只算一次(不同 customer_file_id 去重)
    s_dup = [_src("1990-01-01", 1), _src("1990-01-01", 1)]
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=s_dup)
    assert r["corroboration"] == 1 and r["score"] == 70, r


def test_conflict_penalty():
    # 有 1 种不一致取值 = 70-25=45 低
    s = [_src("1990-01-01", 1), _src("1991-02-02", 2)]
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=s)
    assert r["score"] == 45 and r["level"] == "低" and r["conflict_count"] == 1, r
    assert any("不一致" in x for x in r["reasons"]), r
    # declared + 冲突 = 25,clamps ≥0
    r = cred(layer="declared", status="ai", current_value="A", field="occupation",
             samples=[_src("A", 1, "kyc_form"), _src("B", 2, "kyc_form"),
                      _src("C", 3, "kyc_form")])
    assert r["score"] == 25 and r["level"] == "低" and r["conflict_count"] == 2, r


def test_normalization_agreement():
    # 日期多格式一致(1990/1/1 == 1990-01-01);拼音名大小写/空格不敏感;masked 剔除
    s = [_src("1990-01-01", 1), _src("1990/1/1", 2)]
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=s)
    assert r["corroboration"] == 2 and r["conflict_count"] == 0, r
    s = [_src("NI CHENG", 1, "passport"), _src("nicheng", 2, "approval")]
    r = cred(layer="verified", status="ai", current_value="Ni Cheng",
             field="name_en", samples=s)
    assert r["corroboration"] == 2 and r["conflict_count"] == 0, r
    s = [_src("1990-01-01", 1), _src("[身份证]", 2)]
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=s)
    assert r["corroboration"] == 1, r  # masked 样本不计互证也不算冲突


def test_sources_flags():
    s = [_src("1990-01-01", 1, "id_card", "身份证.pdf"),
         _src("1991-02-02", 2, "hukou", "户口本.pdf")]
    r = cred(layer="verified", status="ai", current_value="1990-01-01",
             field="birth_date", samples=s)
    flags = [(x["value"], x["agrees"], x["source"], x["doc_type"], x["customer_file_id"])
             for x in r["sources"]]
    assert ("1990-01-01", True, "身份证.pdf", "id_card", 1) in flags, flags
    assert ("1991-02-02", False, "户口本.pdf", "hukou", 2) in flags, flags


def test_empty_samples():
    r = cred(layer="verified", status="ai", current_value="张三",
             field="name", samples=[])
    assert r["score"] == 70 and r["corroboration"] == 0 and r["sources"] == [], r


# ==================== DB 集成:读时溯源 + attach(依赖真实 DB,测后清理) ====================

_HH = "测试可信度家庭"


def test_attach_db():
    """_collect_field_provenance + attach_field_credibility/attach_field_conflicts 全链路。"""
    import asyncio
    from sqlalchemy import delete, select
    from db import profile_crud
    from db.engine import async_session_maker
    from db.models import (CustomerFile, DocExtractResult, ProfileHousehold,
                           ProfileImportTask, ProfilePerson, ProfilePersonField)

    async def _cleanup(hh_id):
        async with async_session_maker() as s:
            if hh_id:
                await s.execute(delete(ProfilePersonField).where(
                    ProfilePersonField.person_id.in_(
                        select(ProfilePerson.id).where(ProfilePerson.household_id == hh_id))))
                await s.execute(delete(ProfilePerson).where(ProfilePerson.household_id == hh_id))
                h = await s.get(ProfileHousehold, hh_id)
                if h:
                    await s.delete(h)
            else:
                await s.execute(delete(ProfileHousehold).where(ProfileHousehold.name == _HH))
            await s.commit()

    async def run():
        hh_id, task_id = None, None
        try:
            await _cleanup(None)
            async with async_session_maker() as s:
                await s.execute(delete(ProfileImportTask).where(
                    ProfileImportTask.client_name == _HH))
                await s.commit()
            hh = await profile_crud.get_or_create_household(_HH)
            hh_id = hh["id"]
            main_id = hh["main_person_id"]
            await profile_crud.apply_extracted_fields_v2(
                hh_id, {"person_id": main_id, "matched_by": "test"}, [
                    {"key": "name", "value": "测可信", "column": "name"},
                    {"key": "birth_date", "value": "1990-01-01", "column": "birth_date"},
                    {"key": "phone", "value": "13600000000", "column": "phone"},
                ])

            async with async_session_maker() as s:
                t = ProfileImportTask(filename="t.xlsx", client_name=_HH,
                                      household_id=hh_id, status="done")
                s.add(t)
                await s.flush()
                task_id = t.id
                await s.commit()

            # 文件1 身份证: birth_date 一致(互证);文件2 KYC: birth_date 一致(跨类型互证) + phone 不同(冲突)
            async with async_session_maker() as s:
                cf1 = CustomerFile(file_code="cred-f1", import_task_id=task_id, status="done",
                                   filename="身份证.pdf", doc_type="id_card")
                cf2 = CustomerFile(file_code="cred-f2", import_task_id=task_id, status="done",
                                   filename="KYC表.xlsx", doc_type="kyc_form")
                s.add_all([cf1, cf2])
                await s.flush()
                fid1, fid2 = cf1.id, cf2.id
                ws = {"person_id": main_id, "matched_by": "id_number"}
                s.add(DocExtractResult(
                    customer_file_id=fid1, import_task_id=task_id, file_id="cred-f1",
                    doc_type="id_card", status="done",
                    extracted={"name": "测可信", "birth_date": "1990-01-01", "phone": "13600000000"},
                    mapped=[{"key": "birth_date", "field": "birth_date", "action": "written"},
                            {"key": "phone", "field": "phone", "action": "written"}],
                    write_stats=ws))
                s.add(DocExtractResult(
                    customer_file_id=fid2, import_task_id=task_id, file_id="cred-f2",
                    doc_type="kyc_form", status="done",
                    extracted={"birth_date": "1990/1/1", "phone": "13911111111"},
                    mapped=[{"key": "birth_date", "field": "birth_date", "action": "skipped_same"},
                            {"key": "phone", "field": "phone", "action": "skipped_confirmed"}],
                    write_stats=ws))
                await s.commit()

            prov = await profile_crud._collect_field_provenance(hh_id)
            samples, fnames = prov
            assert fnames.get(fid1) == "身份证.pdf" and fnames.get(fid2) == "KYC表.xlsx", fnames

            persons = await profile_crud.list_persons(hh_id)
            await profile_crud.attach_field_conflicts(persons, hh_id, provenance=prov)
            await profile_crud.attach_field_credibility(persons, hh_id, provenance=prov)
            p = next(x for x in persons if x["id"] == main_id)
            fmap = {f["field"]: f for f in p["fields"]}

            # birth_date: 2 文件一致 + 跨类型 → 70+15+5=90 高
            c = fmap["birth_date"]["credibility"]
            assert c["score"] == 90 and c["level"] == "高", c
            assert c["corroboration"] == 2 and c["conflict_count"] == 0, c
            assert len(c["sources"]) == 2, c["sources"]
            # phone: 当前值 136...,另一文件 139... → 50-25=25 低;sources 带一致/不一致
            c = fmap["phone"]["credibility"]
            assert c["score"] == 25 and c["level"] == "低" and c["conflict_count"] == 1, c
            flags = {(s["value"], s["agrees"]) for s in c["sources"]}
            assert ("13600000000", True) in flags and ("13911111111", False) in flags, flags
            # 全字段采样(phone 不在 8 个交叉验证字段里,但可信度照样打分)
            # field_conflicts 行为不变:phone 不在交叉验证字段 → 无冲突项
            assert "phone" not in (p.get("field_conflicts") or {}), p.get("field_conflicts")
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
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed}/{len(tests)} 失败")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
