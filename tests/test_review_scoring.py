"""review_service.evaluate_file_quality 纯函数测试(无 DB 依赖)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_review_scoring.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import review_service as rs


def _reason(r):
    return r["review_reason"]


def test_no_text():
    r = rs.evaluate_file_quality(ocr_text="")
    assert r["review_status"] == "needs_review" and _reason(r) == "no_text" and r["quality_score"] == 0, r
    r = rs.evaluate_file_quality(ocr_text=None)
    assert _reason(r) == "no_text", r


def test_photo_folder_exempt():
    """证件照文件夹无文本/极短文本是预期,不判待复核。"""
    r = rs.evaluate_file_quality(ocr_text="", folder_name="证件照2026")
    assert r["review_status"] == "none", r
    r = rs.evaluate_file_quality(ocr_text="1", folder_name="证件照2026")
    assert r["review_status"] == "none", r


def test_garbled():
    # 实测出生证乱码样本风格:希腊/拉丁/符号噪声为主,CJK 极少
    garbage = "〓 Φ 一 〓 " * 30 + "�" * 20 + "o c E ° · ~ α β" * 10
    assert rs.is_garbled(garbage)
    r = rs.evaluate_file_quality(ocr_text=garbage)
    assert _reason(r) == "garbled" and r["quality_score"] == 10, r


def test_real_garbled_sample():
    """贴近真实乱码样本(实测 〓2.2%/Φ3.6%/CJK~15%)。"""
    sample = ("△ J J J 1 1 1\n1\n■ ■ ˇ Ⅲ ■\nr 一 〓 Φ 0 一 t Φ B 至 ~8ε 0Φ\n态\n"
              "8一 ° υ 一 一 〓 ∞ ~ 一 Φ 0°o的 0 ε E一 o一 of〓\n一 0 〓 〓 工 一 一 ° .· Φ 〓" * 6)
    assert rs.is_garbled(sample), (rs.cjk_ratio(sample), rs.garbled_ratio(sample))


def test_normal_text_not_garbled():
    text = "出生医学证明 新生儿姓名 倪成 性别 男 出生日期 2015年3月2日 母亲姓名 刘小娟 父亲姓名 倪朝晖"
    assert not rs.is_garbled(text)
    r = rs.evaluate_file_quality(ocr_text=text, doc_type="birth_cert", classify_by="keyword", classify_score=90)
    assert r["review_status"] == "none" and r["quality_score"] == 100, r


def test_english_doc_not_garbled():
    """纯英文件(如永居批复):CJK 低但怪字符少,不能误判乱码。"""
    text = "VANUATU PERMANENT RESIDENCY VISA CARD Surname: NI GivenName: ZHAOHUI Gender: Male Date of Birth: 25-MAR-1969 Nationality: Chinese"
    assert not rs.is_garbled(text), (rs.cjk_ratio(text), rs.garbled_ratio(text))


def test_ocr_short():
    r = rs.evaluate_file_quality(ocr_text="签发机关 某局")
    assert _reason(r) == "ocr_short" and r["quality_score"] == 30, r


def test_extract_error():
    text = "中华人民共和国居民身份证 姓名 张三 公民身份号码 110101199001011234 签发机关 某局"
    r = rs.evaluate_file_quality(ocr_text=text, doc_type="id_card",
                                 classify_by="keyword", classify_score=90,
                                 extract_status="error")
    assert _reason(r) == "extract_error" and r["quality_score"] == 15, r


def test_no_person():
    text = "签发机关 北京市公安局朝阳分局 有效期限 2020.01.01-2040.01.01 中华人民共和国居民身份证"
    r = rs.evaluate_file_quality(ocr_text=text, doc_type="id_card",
                                 classify_by="keyword", classify_score=80,
                                 extract_status="skipped", extract_skip_reason="no_person")
    assert _reason(r) == "no_person" and r["quality_score"] == 20, r


def test_masked_id():
    text = "姓名 张三 性别 男 民族 汉 出生 1990年1月1日 住址 北京市朝阳区 公民身份号码 [身份证] 某某分局"
    r = rs.evaluate_file_quality(ocr_text=text, doc_type="id_card",
                                 classify_by="keyword", classify_score=80,
                                 extract_status="done", id_masked=True)
    assert _reason(r) == "masked_id" and r["quality_score"] == 40, r


def test_low_confidence():
    text = "某段不太典型的证件文字内容,长度足够超过三十个字符的阈值限制要求"
    r = rs.evaluate_file_quality(ocr_text=text, doc_type="hukou",
                                 classify_by="llm", classify_score=45)
    assert _reason(r) == "low_confidence" and r["quality_score"] == 50, r
    r2 = rs.evaluate_file_quality(ocr_text=text, doc_type="hukou",
                                  classify_by="llm", classify_score=85)
    assert r2["review_status"] == "none", r2


def test_priority_order():
    """extract_error 优先于 low_confidence(先判严重问题)。"""
    text = "中华人民共和国居民身份证 姓名 张三 公民身份号码 110101199001011234 签发机关"
    r = rs.evaluate_file_quality(ocr_text=text, doc_type="id_card",
                                 classify_by="llm", classify_score=40,
                                 extract_status="error")
    assert _reason(r) == "extract_error", r


def test_field_validation():
    text = "姓名 张三 性别 男 民族 汉 出生 1990年1月1日 住址 北京市朝阳区 公民身份号码 110101199003077715"
    r = rs.evaluate_file_quality(ocr_text=text, doc_type="id_card",
                                 classify_by="keyword", classify_score=90,
                                 extract_status="done", validation_flags=1)
    assert _reason(r) == "field_validation" and r["quality_score"] == 45, r
    # 无疑点不受影响
    r2 = rs.evaluate_file_quality(ocr_text=text, doc_type="id_card",
                                  classify_by="keyword", classify_score=90,
                                  extract_status="done", validation_flags=0)
    assert r2["review_status"] == "none", r2


def test_field_validation_priority():
    """masked_id(40) 优先于 field_validation(45);field_validation 优先于 low_confidence(50)。"""
    text = "姓名 张三 性别 男 民族 汉 出生 1990年1月1日 住址 北京市朝阳区 公民身份号码 [身份证] 某某分局"
    r = rs.evaluate_file_quality(ocr_text=text, doc_type="id_card",
                                 classify_by="llm", classify_score=40,
                                 extract_status="done", id_masked=True, validation_flags=2)
    assert _reason(r) == "masked_id", r
    r2 = rs.evaluate_file_quality(ocr_text=text, doc_type="id_card",
                                  classify_by="llm", classify_score=40,
                                  extract_status="done", validation_flags=2)
    assert _reason(r2) == "field_validation", r2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个测试通过")
