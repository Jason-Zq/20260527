"""多人提取(multi)测试:parse_persons_payload / multi prompt / LLM 重试(纯函数+mock,不依赖 DB)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_extract_multi.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import extract_rules
import llm_service


_RULE = extract_rules.get_rule("hukou")


def test_parse_persons_payload():
    p = llm_service.parse_persons_payload

    # 正常 persons 数组:只留规则 keys、空串→None、多余 key 剔除
    data = {"persons": [
        {"name": "张三", "gender": "男", "birth_date": "", "hack": "x"},
        {"name": "李四", "gender": None},
    ]}
    rows = p(data, _RULE)
    assert len(rows) == 2, rows
    assert rows[0]["name"] == "张三" and rows[0]["gender"] == "男", rows[0]
    assert rows[0]["birth_date"] is None and "hack" not in rows[0], rows[0]
    assert rows[1]["name"] == "李四" and rows[1]["gender"] is None, rows[1]
    # 规则 keys 全覆盖(缺的补 None)
    assert set(rows[0].keys()) == {f["key"] for f in _RULE["fields"]}, rows[0].keys()

    # 全空 dict / 非 dict 项被过滤
    data = {"persons": [{"name": None, "gender": ""}, "垃圾", None, {"name": "王五"}]}
    rows = p(data, _RULE)
    assert len(rows) == 1 and rows[0]["name"] == "王五", rows

    # 兼容模型误返回单人 {"fields": {...}} → 包成单元素
    rows = p({"fields": {"name": "赵六", "id_number": "110101199001011234"}}, _RULE)
    assert len(rows) == 1 and rows[0]["name"] == "赵六", rows

    # persons 缺失/非 list → []
    assert p({}, _RULE) == []
    assert p({"persons": "不是数组"}, _RULE) == []
    assert p({"persons": None, "fields": None}, _RULE) == []


def test_build_extract_multi_prompt():
    prompt = llm_service._build_extract_multi_prompt("某某 OCR 文本", _RULE)
    assert "每个人的信息" in prompt or "逐人提取" in prompt, prompt
    assert '{"persons"' in prompt, prompt
    # 含全部规则 key
    for f in _RULE["fields"]:
        assert f'"{f["key"]}"' in prompt, f["key"]
    # prompt_extra 进入 prompt(多人版第 1 条)
    assert "所有常住人口登记卡成员" in prompt, prompt


def test_extract_doc_fields_multi_retry():
    calls = []
    orig = llm_service._call_llm
    try:
        # 首次非法 JSON、第二次合法 → 解析成功,调了 2 次
        def fake_ok(prompt, **ctx):
            calls.append(1)
            if len(calls) == 1:
                return "不是 JSON"
            return '{"persons": [{"name": "张三", "gender": "男"}]}'
        llm_service._call_llm = fake_ok
        r = llm_service.extract_doc_fields_multi("OCR 文本", _RULE)
        assert len(calls) == 2, calls
        assert r["persons"][0]["name"] == "张三", r

        # 两次都非法 → ValueError
        calls.clear()
        llm_service._call_llm = lambda prompt, **ctx: calls.append(1) or "仍不是 JSON"
        try:
            llm_service.extract_doc_fields_multi("OCR 文本", _RULE)
            raise AssertionError("应当 ValueError")
        except ValueError:
            pass
        assert len(calls) == 2, calls

        # 空文本 → ValueError(不调 LLM)
        calls.clear()
        try:
            llm_service.extract_doc_fields_multi("", _RULE)
            raise AssertionError("应当 ValueError")
        except ValueError:
            pass
        assert len(calls) == 0, calls
    finally:
        llm_service._call_llm = orig


def test_clean_field_items_shared():
    """单/多人共用的字段清洗:masked 剔除归因、假名置 None、id_masked 标记。"""
    import profile_import_service as pis
    items, idn, name, name_en, id_masked, repairs, vflags = pis._clean_field_items(_RULE, {
        "name": "张三", "id_number": "110101199001011234", "gender": "男"})
    assert idn == "110101199001011234" and name == "张三" and id_masked is False
    keys = {it["key"] for it in items}
    assert {"name", "id_number", "gender"} <= keys, keys

    # masked 证件号:不参与归因,id_masked=True
    items, idn, name, name_en, id_masked, repairs, vflags = pis._clean_field_items(_RULE, {
        "name": "张三", "id_number": "[身份证]"})
    assert idn is None and name == "张三" and id_masked is True

    # 乱码假名置 None
    items, idn, name, name_en, id_masked, repairs, vflags = pis._clean_field_items(_RULE, {
        "name": "钅 lil蝴哪"})
    assert name is None


def test_result_person_ids():
    f = __import__("db.profile_crud", fromlist=["_result_person_ids"])._result_person_ids
    # 单人:顶层 person_id
    assert f({"person_id": 7, "matched_by": "name"}) == [7]
    # 多人:persons 明细优先
    assert f({"person_id": 7, "persons": [{"person_id": 7}, {"person_id": 8}]}) == [7, 8]
    # persons 里有 None 被过滤
    assert f({"persons": [{"person_id": None}, {"person_id": 9}]}) == [9]
    # 空
    assert f(None) == [] and f({}) == []


def test_marriage_persons_payload():
    """marriage_cert 多人规则:两 person 各自字段子集,cert_role 保留不写库,spouse_name 参与写库。"""
    import profile_import_service as pis
    rule = extract_rules.get_rule("marriage_cert")
    p = llm_service.parse_persons_payload
    data = {"persons": [
        {"cert_role": "持证人", "name": "张三", "gender": "男",
         "birth_date": "1990-01-01", "id_number": "110101199001011234",
         "spouse_name": "刘小娟", "marital_status": "已婚",
         "marriage_date": "2020-05-20", "marriage_authority": "北京市朝阳区民政局",
         "marriage_cert_no": "京朝结字2020-123456"},
        {"cert_role": "配偶", "name": "刘小娟", "gender": "女",
         "id_number": "110101199202022345", "spouse_name": "张三",
         "marital_status": "已婚", "marriage_date": "2020-05-20",
         "marriage_authority": "北京市朝阳区民政局",
         "marriage_cert_no": "京朝结字2020-123456"},
    ]}
    rows = p(data, rule)
    assert len(rows) == 2, rows
    keys = {f["key"] for f in rule["fields"]}
    assert set(rows[0].keys()) == keys and set(rows[1].keys()) == keys, rows
    assert rows[0]["cert_role"] == "持证人" and rows[1]["cert_role"] == "配偶", rows
    assert rows[0]["spouse_name"] == "刘小娟" and rows[1]["spouse_name"] == "张三", rows
    assert rows[1]["birth_date"] is None, rows[1]  # 缺的 key 补 None
    # 清洗:两人各自可归因(姓名+证件号);cert_role 无 column 不写库
    _, idn0, name0, _, _, _, _ = pis._clean_field_items(rule, rows[0])
    _, idn1, name1, _, _, _, _ = pis._clean_field_items(rule, rows[1])
    assert (name0, idn0) == ("张三", "110101199001011234")
    assert (name1, idn1) == ("刘小娟", "110101199202022345")
    items0, *_ = pis._clean_field_items(rule, rows[0])
    cols0 = {it["key"]: it["column"] for it in items0}
    assert cols0.get("spouse_name") == "spouse_name", cols0
    assert cols0.get("cert_role") is None, cols0


if __name__ == "__main__":
    test_parse_persons_payload()
    print("PASS test_parse_persons_payload")
    test_build_extract_multi_prompt()
    print("PASS test_build_extract_multi_prompt")
    test_extract_doc_fields_multi_retry()
    print("PASS test_extract_doc_fields_multi_retry")
    test_clean_field_items_shared()
    print("PASS test_clean_field_items_shared")
    test_result_person_ids()
    print("PASS test_result_person_ids")
    test_marriage_persons_payload()
    print("PASS test_marriage_persons_payload")
    print("\n全部 6 个测试通过")
