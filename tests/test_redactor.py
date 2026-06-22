"""redactor 单元测试（无外部依赖）。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_redactor.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from redactor import redact, redact_list, redact_dict


def test_amount_with_prefix():
    # 注意：¥12,345.6 整段被吃掉，紧随的"元整"中没数字所以保持原文
    assert redact("总金额 ¥12,345.6 元整") == "总金额 [金额] 元整"
    assert redact("about $1000") == "about [金额]"
    # €500.50 整段被吞，后面 EUR 不再有数字
    assert redact("约 €500.50 EUR") == "约 [金额] EUR"


def test_amount_with_unit():
    assert redact("年薪 50万") == "年薪 [金额]"
    assert redact("总价 1.5亿") == "总价 [金额]"
    assert redact("付款 12000元 整") == "付款 [金额] 整"
    assert redact("salary 5000 RMB monthly") == "salary [金额] monthly"


def test_phone():
    assert redact("电话 13800138000") == "电话 [手机号]"
    assert redact("call me at 18612345678 anytime") == "call me at [手机号] anytime"


def test_id_card():
    # 真实格式 18 位身份证
    assert redact("身份证号 110101199003078212") == "身份证号 [身份证]"
    assert redact("ID: 11010119900307821X") == "ID: [身份证]"


def test_bank_card():
    # 16 位
    assert redact("卡号 6222020200000000123") == "卡号 [银行卡]"
    # 带空格 / 短横
    assert redact("4111 1111 1111 1111") == "[银行卡]"


def test_landline():
    assert redact("座机 010-12345678") == "座机 [座机]"
    assert redact("(0755)87654321") == "[座机]"


def test_no_match_passthrough():
    assert redact("这是一段没有敏感信息的文本") == "这是一段没有敏感信息的文本"
    assert redact("") == ""
    assert redact(None) is None


def test_redact_list():
    out = redact_list(["年薪 50万", "正常文本", "电话 13800138000"])
    assert out == ["年薪 [金额]", "正常文本", "电话 [手机号]"]
    assert redact_list([]) == []
    assert redact_list(None) == []


def test_redact_dict():
    d = {
        "is_archival": True,
        "confidence": 88,
        "reason": "客户支付了 ¥12345 给项目方",
        "key_points": ["年薪 50万元", "电话 13800138000"],
        "doc_category": "合同协议",
    }
    out = redact_dict(d)
    # 原 dict 不变
    assert d["reason"] == "客户支付了 ¥12345 给项目方"
    # 新 dict 已脱敏
    assert out["reason"] == "客户支付了 [金额] 给项目方"
    # "50万元" 中 "50万" 段被金额规则吞掉，剩 "元" 字符
    assert out["key_points"] == ["年薪 [金额]元", "电话 [手机号]"]
    # 其他字段不变
    assert out["is_archival"] is True
    assert out["confidence"] == 88
    assert out["doc_category"] == "合同协议"


def test_redact_dict_missing_keys():
    """缺少字段时不报错。"""
    out = redact_dict({"is_archival": False})
    assert out == {"is_archival": False}


def test_combined_real_world():
    # 模拟 LLM 输出
    text = "该客户姓张，身份证号 110101199003078212，手机 13800138000，年薪 50万元，银行卡号 6222020200000000123"
    redacted = redact(text)
    assert "110101199003078212" not in redacted
    assert "13800138000" not in redacted
    assert "50万" not in redacted
    assert "6222020200000000123" not in redacted
    assert "[身份证]" in redacted
    assert "[手机号]" in redacted
    assert "[金额]" in redacted
    assert "[银行卡]" in redacted


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
    print(f"\n{len(tests)}/{len(tests)} 通过")
