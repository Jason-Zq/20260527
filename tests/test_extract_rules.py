"""extract_rules 常量测试(纯函数,不依赖 DB)。

规则已从 DB 迁至代码常量;本测试断言常量完整性与 get_rule 语义。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_extract_rules.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import extract_rules as er


# 迁移时 DB 里 11 类 active 规则(approval 取 v2),这里作为回归基线
_EXPECTED_TYPES = {
    "id_card", "hukou", "passport", "kyc_form", "marriage_cert", "birth_cert",
    "property_cert", "no_crime", "submission", "receipt", "approval",
}


def test_all_types_present():
    got = set(er.EXTRACT_RULES.keys())
    assert got == _EXPECTED_TYPES, f"类型不匹配: 缺 {_EXPECTED_TYPES - got}, 多 {got - _EXPECTED_TYPES}"


def test_get_rule_structure():
    r = er.get_rule("id_card")
    assert r is not None
    # 对齐原 get_active_rule 返回结构 + multi(多人模式标记,默认 False)
    assert set(r.keys()) == {"doc_type", "version", "fields", "prompt_extra", "multi"}, r.keys()
    assert r["doc_type"] == "id_card"
    assert r["multi"] is False
    assert isinstance(r["fields"], list) and len(r["fields"]) >= 1
    # 每个字段有 key/label/target
    for f in r["fields"]:
        assert f.get("key") and f.get("label"), f
        assert "target" in f and "entity" in f["target"], f


def test_hukou_is_multi():
    r = er.get_rule("hukou")
    assert r is not None and r["multi"] is True, r
    assert r["version"] == 2, r
    assert "所有常住人口登记卡成员" in (r["prompt_extra"] or ""), r["prompt_extra"]


def test_marriage_is_multi():
    """marriage_cert v2 起多人模式:抽全配偶字段+自动建配偶卡。"""
    r = er.get_rule("marriage_cert")
    assert r is not None and r["multi"] is True, r
    assert r["version"] == 2, r
    assert "恰好 2 个 person 对象" in (r["prompt_extra"] or ""), r["prompt_extra"]
    cols = {f["key"]: f["target"].get("column") for f in r["fields"]}
    # 本人字段 + spouse_name 交叉写 + 共享婚姻字段
    assert cols.get("name") == "name", cols
    assert cols.get("id_number") == "id_number", cols
    assert cols.get("birth_date") == "birth_date", cols
    assert cols.get("spouse_name") == "spouse_name", cols
    assert cols.get("marital_status") == "marital_status", cols
    assert cols.get("marriage_date") == "marriage_date", cols
    assert cols.get("cert_role") is None, cols  # 只抽不写库,供推导定位持证人/配偶


def test_approval_is_v2():
    r = er.get_rule("approval")
    assert r is not None and r["version"] == 2, r


def test_unknown_type_returns_none():
    assert er.get_rule("nope") is None
    assert er.get_rule("") is None


def test_rules_version_constant():
    assert isinstance(er.RULES_VERSION, int) and er.RULES_VERSION >= 1


if __name__ == "__main__":
    test_all_types_present()
    print("PASS test_all_types_present")
    test_get_rule_structure()
    print("PASS test_get_rule_structure")
    test_hukou_is_multi()
    print("PASS test_hukou_is_multi")
    test_marriage_is_multi()
    print("PASS test_marriage_is_multi")
    test_approval_is_v2()
    print("PASS test_approval_is_v2")
    test_unknown_type_returns_none()
    print("PASS test_unknown_type_returns_none")
    test_rules_version_constant()
    print("PASS test_rules_version_constant")
    print("\n全部 7 个测试通过")
