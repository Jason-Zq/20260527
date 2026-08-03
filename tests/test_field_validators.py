"""field_validators 单元测试:身份证校验位/单字符修复/字段派生/日期合理性(纯函数)。

运行: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_field_validators.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import field_validators as fv

# 合法样本(校验位用实现自身的 id_check_char 计算,与 GB 11643 公开算法一致)
V1 = "110101199003077715"   # 男 1990-03-07 北京东城
V2 = "44030119850612482X"   # 女 1985-06-12 深圳(末位 X)


def _items(**cols):
    """构造 field_items:cols 形如 name='张三', birth_date='1990-03-07'。"""
    return [{"key": col, "label": col, "value": v, "column": col,
             "layer": None, "entity": "person"} for col, v in cols.items()]


# ---------- id_checksum_ok ----------

def test_checksum_valid():
    assert fv.id_checksum_ok(V1) and fv.id_checksum_ok(V2)
    print("  OK   test_checksum_valid")


def test_checksum_invalid_digit():
    bad = V1[:8] + ("0" if V1[8] != "0" else "1") + V1[9:]
    assert not fv.id_checksum_ok(bad)
    print("  OK   test_checksum_invalid_digit")


def test_checksum_format_reject():
    assert not fv.id_checksum_ok("11010119900307771")       # 17 位
    assert not fv.id_checksum_ok("1101011990030777159")     # 19 位
    assert not fv.id_checksum_ok("11010119900307771A")      # 末位非法字符
    assert not fv.id_checksum_ok("")
    assert not fv.id_checksum_ok(None)
    print("  OK   test_checksum_format_reject")


def test_checksum_known_vector():
    # GB 11643 校验算法向量:前 17 位全 1 → 加权和 100,100 mod 11 = 1 → 校验码 '0'
    assert fv.id_check_char("11111111111111111") == "0"
    print("  OK   test_checksum_known_vector")


# ---------- 内嵌信息 ----------

def test_embedded_birth_gender():
    assert fv.id_embedded_birth(V1) == "1990-03-07"
    assert fv.id_embedded_gender(V1) == "男"
    assert fv.id_embedded_birth(V2) == "1985-06-12"
    assert fv.id_embedded_gender(V2) == "女"
    print("  OK   test_embedded_birth_gender")


def test_embedded_birth_invalid_month():
    assert fv.id_embedded_birth("110101199013077715") is None  # 13 月
    assert fv.id_embedded_birth("110101189903077715") is None  # 1899 < 1900
    print("  OK   test_embedded_birth_invalid_month")


def test_embedded_15digit():
    assert fv.id_embedded_birth("110101900307771") == "1990-03-07"
    assert fv.id_embedded_gender("110101900307771") == "男"
    assert fv.id_embedded_gender("110101900307782") == "女"
    print("  OK   test_embedded_15digit")


# ---------- repair_id_number ----------

def test_repair_unique_invalid_month():
    # 月份 0→8(03→83 非法):唯一候选修复回原值
    r = fv.repair_id_number("110101199083077715", birth_date="1990-03-07", gender="男")
    assert r["repaired"] and r["value"] == V1, r
    print("  OK   test_repair_unique_invalid_month")


def test_repair_unique_gender_position():
    # 顺序码性别位 1→2(男→女):gender 交叉过滤后唯一
    r = fv.repair_id_number("110101199003077725", birth_date="1990-03-07", gender="男")
    assert r["repaired"] and r["value"] == V1, r
    print("  OK   test_repair_unique_gender_position")


def test_repair_ambiguous_check_digit():
    # 校验位误识 5→2:校验位修复与地区位修复竞争(≥2 候选),不改
    r = fv.repair_id_number(V1[:17] + "2", birth_date="1990-03-07", gender="男")
    assert not r["repaired"] and r["value"] is None and r["candidates"] >= 2, r
    print("  OK   test_repair_ambiguous_check_digit")


def test_repair_ambiguous_x_to_zero():
    # X 误识为 0:X 修复与其他位置修复竞争,多候选不改(宁复核不误改)
    r = fv.repair_id_number(V2[:17] + "0", birth_date="1985-06-12", gender="女")
    assert not r["repaired"] and r["candidates"] >= 2, r
    print("  OK   test_repair_ambiguous_x_to_zero")


def test_repair_no_candidate():
    r = fv.repair_id_number("123456789012345678")
    assert not r["repaired"] and r["candidates"] == 0, r
    print("  OK   test_repair_no_candidate")


def test_repair_already_valid():
    r = fv.repair_id_number(V1)
    assert not r["repaired"] and r["value"] == V1, r
    print("  OK   test_repair_already_valid")


def test_repair_without_cross_info_still_safe():
    # 顺序码误识但无 birth/gender 交叉信息:多候选 → 不改
    bad = V1[:15] + "1" + V1[16:]
    r = fv.repair_id_number(bad)
    assert not r["repaired"], r
    print("  OK   test_repair_without_cross_info_still_safe")


# ---------- validate_field_items ----------

def test_validate_repairs_and_updates_item():
    items = _items(name="张三", id_number="110101199083077715",
                   birth_date="1990-03-07", gender="男")
    items, repairs, flags = fv.validate_field_items(items)
    id_item = next(it for it in items if it["column"] == "id_number")
    assert id_item["value"] == V1, id_item
    assert any(r["reason"] == "checksum_repair" and r["from"] == "110101199083077715"
               and r["to"] == V1 for r in repairs), repairs
    assert not any(f["reason"] == "checksum_fail" for f in flags), flags
    print("  OK   test_validate_repairs_and_updates_item")


def test_validate_flags_ambiguous():
    items = _items(name="张三", id_number=V1[:17] + "2",
                   birth_date="1990-03-07", gender="男")
    items, repairs, flags = fv.validate_field_items(items)
    id_item = next(it for it in items if it["column"] == "id_number")
    assert id_item["value"] == V1[:17] + "2", "多候选不应改原值"
    assert any(f["reason"] == "checksum_fail" for f in flags), flags
    print("  OK   test_validate_flags_ambiguous")


def test_validate_derives_birth_and_gender():
    items = _items(name="张三", id_number=V1)
    items, repairs, flags = fv.validate_field_items(items)
    cols = {it["column"]: it["value"] for it in items}
    assert cols.get("birth_date") == "1990-03-07", cols
    assert cols.get("gender") == "男", cols
    assert sum(1 for r in repairs if r["reason"] == "derived_from_id") == 2, repairs
    print("  OK   test_validate_derives_birth_and_gender")


def test_validate_derive_15digit():
    items = _items(name="张三", id_number="110101900307771")
    items, repairs, flags = fv.validate_field_items(items)
    cols = {it["column"]: it["value"] for it in items}
    assert cols.get("birth_date") == "1990-03-07" and cols.get("gender") == "男", cols
    print("  OK   test_validate_derive_15digit")


def test_validate_birth_mismatch_flag():
    items = _items(name="张三", id_number=V1, birth_date="1990-03-08", gender="男")
    items, repairs, flags = fv.validate_field_items(items)
    cols = {it["column"]: it["value"] for it in items}
    assert cols["birth_date"] == "1990-03-08", "冲突不应自动改字段值"
    assert any(f["reason"] == "id_birth_mismatch" for f in flags), flags
    print("  OK   test_validate_birth_mismatch_flag")


def test_validate_gender_mismatch_flag():
    items = _items(name="张三", id_number=V1, birth_date="1990-03-07", gender="女")
    items, repairs, flags = fv.validate_field_items(items)
    assert any(f["reason"] == "id_gender_mismatch" for f in flags), flags
    print("  OK   test_validate_gender_mismatch_flag")


def test_validate_masked_id_skipped():
    items = _items(name="张三", id_number="[身份证号]", birth_date="1990-03-07")
    items, repairs, flags = fv.validate_field_items(items)
    assert not repairs and not flags, (repairs, flags)
    print("  OK   test_validate_masked_id_skipped")


def test_validate_normalizes_id():
    items = _items(name="张三", id_number=" " + V1.lower() + " ",
                   birth_date="1990-03-07", gender="男")
    items, repairs, flags = fv.validate_field_items(items)
    id_item = next(it for it in items if it["column"] == "id_number")
    assert id_item["value"] == V1
    assert any(r["reason"] == "normalize" for r in repairs), repairs
    print("  OK   test_validate_normalizes_id")


def test_validate_future_birth_flag():
    future = f"{date.today().year + 1}-01-01"
    items = _items(name="张三", birth_date=future)
    items, repairs, flags = fv.validate_field_items(items)
    assert any(f["reason"] == "future_birth_date" for f in flags), flags
    print("  OK   test_validate_future_birth_flag")


def test_validate_date_order_flag():
    items = _items(name="张三", passport_issue_date="2025-01-01",
                   passport_expiry_date="2020-01-01")
    items, repairs, flags = fv.validate_field_items(items)
    assert any(f["reason"] == "date_order" for f in flags), flags
    print("  OK   test_validate_date_order_flag")


def test_validate_bad_date_format_flag():
    items = _items(name="张三", birth_date="1990年3月7日")
    items, repairs, flags = fv.validate_field_items(items)
    assert any(f["reason"] == "bad_date_format" for f in flags), flags
    print("  OK   test_validate_bad_date_format_flag")


def test_validate_clean_items_no_noise():
    items = _items(name="张三", id_number=V1, birth_date="1990-03-07", gender="男",
                   passport_issue_date="2020-01-01", passport_expiry_date="2030-01-01")
    items, repairs, flags = fv.validate_field_items(items)
    assert not repairs and not flags, (repairs, flags)
    print("  OK   test_validate_clean_items_no_noise")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {name}: {e}")
        except Exception as e:
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\nTotal: {len(tests)} | Passed: {passed} | Failed: {len(tests) - passed}")
    sys.exit(0 if passed == len(tests) else 1)
