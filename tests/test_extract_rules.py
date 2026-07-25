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
    # 对齐原 get_active_rule 返回结构:doc_type/version/fields/prompt_extra
    assert set(r.keys()) == {"doc_type", "version", "fields", "prompt_extra"}, r.keys()
    assert r["doc_type"] == "id_card"
    assert isinstance(r["fields"], list) and len(r["fields"]) >= 1
    # 每个字段有 key/label/target
    for f in r["fields"]:
        assert f.get("key") and f.get("label"), f
        assert "target" in f and "entity" in f["target"], f


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
    test_approval_is_v2()
    print("PASS test_approval_is_v2")
    test_unknown_type_returns_none()
    print("PASS test_unknown_type_returns_none")
    test_rules_version_constant()
    print("PASS test_rules_version_constant")
    print("\n全部 5 个测试通过")
