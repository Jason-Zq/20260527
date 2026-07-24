"""doc_type_matcher 纯函数测试(无 DB/LLM 依赖)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_doc_type_matcher.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import doc_type_matcher as m


def test_id_card_back_with_folder_clue():
    """身份证人像面 OCR:单个 strong 命中 40 分,加 rel_path 线索 20 → 压线定类。"""
    text = "姓名 张三 性别 男 民族 汉 出生 1990年1月1日 住址 北京市朝阳区某某路1号 公民身份号码 110101199001011234"
    r = m.classify(None, "IMG_001.jpg", text, rel_path="身份证2026/倪朝晖")
    assert r["doc_type"] == "id_card" and r["by"] == "keyword", r
    assert r["score"] == 40 + m.CLUE_BONUS, r


def test_id_card_back_without_clue_goes_to_llm():
    """同一文本没有文件夹线索:40 < 60 不定类,交 LLM 兜底(宁漏不误)。"""
    text = "姓名 张三 性别 男 民族 汉 出生 1990年1月1日 住址 北京市朝阳区某某路1号 公民身份号码 110101199001011234"
    r = m.classify(None, "IMG_001.jpg", text)
    assert r["doc_type"] is None and r["scores"]["id_card"] == 40, r


def test_no_crime_cert():
    """无犯罪记录证明(真实件样式):strong×2 + positive 命中,直接定类,不误判身份证。"""
    text = "无犯罪记录证明 沪公(杨)证(2026)第0030975号 经查,被查询人:倪朝晖,国籍:中国,证件名称:身份证,证件号码:310110196903251619,(在1969-03-25至2026-05-24期间),未发现有犯罪记录。此证明书自开具之日起3个月内有效"
    r = m.classify("无犯罪记录", "倪朝晖无犯罪记录.pdf", text)
    assert r["doc_type"] == "no_crime" and r["by"] == "keyword", r
    assert r["scores"]["no_crime"] > r["scores"]["id_card"], r


def test_id_card_front_back_full():
    text = "中华人民共和国居民身份证 签发机关 北京市公安局朝阳分局 有效期限 2020.01.01-2040.01.01 姓名 张三 公民身份号码 110101199001011234"
    r = m.classify(None, "a.jpg", text)
    assert r["doc_type"] == "id_card" and r["score"] >= 60, r


def test_hukou_page():
    text = "常住人口登记卡 姓名 倪朝晖 户主 倪朝晖 与户主关系 户主 户号 123456 住址 上海市浦东新区 户口登记机关 浦东派出所"
    r = m.classify("户口本2026", "01.pdf", text)
    assert r["doc_type"] == "hukou" and r["by"] == "keyword", r


def test_hukou_book_cover():
    text = "居民户口簿 家庭户 户主姓名 倪朝晖 住址 上海市浦东新区某某路 签发机关 上海市公安局 户口登记机关"
    r = m.classify(None, "05.pdf", text)
    assert r["doc_type"] == "hukou", r


def test_degree_cert():
    text = "学士学位证书 倪朝晖 男 1988年5月生 在清华大学 计算机科学与技术专业 完成了本科学习计划 经学位评定委员会审议 授予工学学士学位 证书编号 100033456789"
    r = m.classify(None, "degree.pdf", text)
    assert r["doc_type"] == "degree_cert" and r["by"] == "keyword", r


def test_graduation_cert_is_not_degree():
    """毕业证 ≠ 学位证:负面词压分,不定类(交 LLM 兜底)。"""
    text = "毕业证书 学生 倪朝晖 性别 男 1988年5月生 于2006年9月至2010年7月在本校 计算机科学与技术专业 四年制本科学习 修完教学计划规定的全部课程 成绩合格 准予毕业 证书编号 100033999"
    r = m.classify(None, "grad.pdf", text)
    assert r["doc_type"] != "degree_cert", r


def test_birth_cert():
    text = "出生医学证明 新生儿姓名 倪成 性别 男 出生日期 2015年3月2日 出生孕周 39周 出生体重 3300克 出生身长 50厘米 助产机构 上海市第一妇婴保健院 母亲姓名 刘小娟 父亲姓名 倪朝晖"
    r = m.classify("出生证明类文件", "倪成出生证.pdf", text)
    assert r["doc_type"] == "birth_cert" and r["by"] == "keyword", r


def test_marriage_cert_is_not_birth_cert():
    """结婚证也含'出生日期',不能被误判为出生证明;现已是合法类型,应直接定类。"""
    text = "结婚证 持证人 倪朝晖 登记日期 2010年10月10日 结婚证字号 J310115-2010-001234 姓名 倪朝晖 性别 男 出生日期 1988年5月6日 身份证件号 310115198805061234 姓名 刘小娟 性别 女 出生日期 1990年8月9日"
    r = m.classify("结婚证、出生证", "wecom-temp-889191.jpg", text)
    assert r["doc_type"] == "marriage_cert", r
    assert r["scores"]["marriage_cert"] - r["scores"]["birth_cert"] >= m.TIE_MARGIN, r


def test_passport_is_passport():
    """护照已是合法类型(原 4 类时代判 other,现归入 passport)。"""
    text = "护照 Passport 类型 Type P 国家码 Country Code CHN 护照号 Passport No. E12345678 姓名 Name NI ZHAOHUI 国籍 Nationality 中国 出生日期 Date of birth 06 MAY 1988"
    r = m.classify("护照", "倪朝晖.jpg", text)
    assert r["doc_type"] == "passport", r


def test_no_text():
    r = m.classify("护照", "IMG_20260515_0021.jpg", "")
    assert r == {"doc_type": None, "score": 0, "by": "none",
                 "scores": {t: 0 for t in m.DOC_TYPES}}, r
    r2 = m.classify(None, "x.pdf", None)
    assert r2["by"] == "none" and r2["doc_type"] is None, r2


def test_clue_bonus_from_rel_path():
    """OCR 分不够时,文件夹/相对路径线索每类型至多 +20。"""
    # 只有 strong 命中(40 分)不够阈值;rel_path 命中"身份证" +20 → 60 压线定类
    text = "公民身份号码 110101199001011234"
    r_no_clue = m.classify(None, "a.jpg", text)
    assert r_no_clue["doc_type"] is None and r_no_clue["score"] == 40, r_no_clue
    r_clue = m.classify(None, "a.jpg", text, rel_path="身份证2026/倪朝晖")
    assert r_clue["doc_type"] == "id_card", r_clue
    assert r_clue["score"] == 40 + m.CLUE_BONUS, r_clue


def test_tie_goes_to_llm():
    """前两名分差 < TIE_MARGIN 时不定类(混合页交 LLM 判)。"""
    # 出生证明与户口信息同页的混合件:birth 60 vs hukou 60 平局
    text = "居民户口簿 出生医学证明 新生儿姓名 倪成 母亲姓名 刘小娟 父亲姓名 倪朝晖 户主 倪朝晖"
    r = m.classify(None, "x.pdf", text)
    assert r["doc_type"] is None, r
    assert abs(r["scores"]["birth_cert"] - r["scores"]["hukou"]) < m.TIE_MARGIN, r


def test_ambiguous_folder_gives_no_clue():
    """文件夹名'结婚证、出生证'不含任一类型 strong/positive 原词,不应加线索分。"""
    text = "出生医学证明 新生儿姓名 倪想 性别 女"
    r = m.classify("结婚证、出生证", "wecom-temp-889191.jpg", text)
    assert r["scores"]["birth_cert"] == 55, r  # strong 40 + positive 15,无 clue(+20 未加)
    assert r["doc_type"] is None, r


def test_passport_page():
    text = "护照 Passport 类型 Type P 国家码 Country Code CHN 护照号 Passport No. E12345678 姓名 Name NI ZHAOHUI 国籍 Nationality 中国 出生日期 Date of birth 25 MAR 1969 签发地点 Place of issue 上海 签发日期 Date of issue 01 JAN 2020 有效期至 Date of expiry 31 DEC 2029"
    r = m.classify("护照", "倪朝晖.jpg", text)
    assert r["doc_type"] == "passport" and r["by"] == "keyword", r


def test_kyc_form():
    text = "A1: Note: For multiple customers, please use ONE CDD Form for EACH Customer A2: Reason for CDD: C2: Account Opening A3: Customer's Name(as per ID document): 客户姓名 C3: ni zhaohui 倪朝晖 B4: Customer's NRIC / Passport No.: 护照号码 C4: ED2080944 B5: Customer's Nationality: 国籍 C5: 中国 B17: 客户的资产来源 C17: 存款/投资"
    r = m.classify("中文信息表", "8.KYC信息收集表--倪朝晖.xlsx", text)
    assert r["doc_type"] == "kyc_form" and r["by"] == "keyword", r
    # KYC 表含"护照号码/Passport No.",不能被误判为护照
    assert r["scores"]["kyc_form"] - r["scores"]["passport"] >= m.TIE_MARGIN, r


def test_pr_card_is_approval():
    """永居批复(英文 PR 卡):归入 approval,不误判护照/KYC。"""
    text = "VANUATU PERMANENT RESIDENCY VISA CARD Surname: NI No.: HK215530(a) GivenName: ZHAOHUI Gender: Male Date of Birth: 25-MAR-1969 Nationality: Chinese Residency Status Permanent Date of Issue: 03-JUN-2026 Director of Immigration Immigration Act No.17 of 2010"
    r = m.classify("获批", "倪总瓦努阿图永居批复20260603.pdf", text)
    assert r["doc_type"] == "approval", r


def test_approval_glued_ocr():
    """批复 OCR 粘连空格(VANUATUPERMANENTRESIDENCYVISACARD):粘连变体关键词仍命中。"""
    text = "VANUATUPERMANENTRESIDENCYVISACARD Surname:NI No.:HK215530(a) GivenName:ZHAOHUI Gender:Male DateofBirth:25-MAR-1969 Nationality:Chinese ResidencyStatusPermanent DateofIsSue:03-JUN-2026 Directorof Immigration with ImmigrationAct No.17of2010"
    r = m.classify("获批", "倪总瓦努阿图永居批复20260603.pdf", text)
    assert r["doc_type"] == "approval" and r["by"] == "keyword", r


def test_property_cert_chache():
    """不动产登记查册(真实件样式):登记簿/坐落/面积/权利人命中,直接定类。"""
    text = "上海市不动产登记簿 房屋状况及产权人信息 NO.202605196637 房屋坐落 政和路388弄35号 幢号 388弄35号 部位 301(复式) 建筑面积 344.31 房屋类型 公寓 权利人 倪朝晖 房地产权证号 杨2015021659 受理日期 2015-09-11 核准日期 2015-09-23"
    r = m.classify("房产证", "倪朝晖查册.pdf", text)
    assert r["doc_type"] == "property_cert" and r["by"] == "keyword", r


def test_application_form_is_submission():
    """递交申请包(中英申请表):归入 submission,不被内含护照字段带偏。"""
    text = "APPLICATION FORM FOR PERMANENT RESIDENCY 申请表 申请人1 护照号 E12345678 护照 Passport 姓名 Name 递交日期 公证 代理 Shanghai Huanyida Business Consulting Co., Ltd."
    r = m.classify("递交", "application.pdf", text)
    assert r["doc_type"] == "submission", r


def test_submission_real_head():
    """真实递交包头部(瓦努阿图永居申请表):strong+positive 命中定类。"""
    text = "VanuatuImmigration andPassportServices ApplicationforPermanentResidenceVisa 永久居留权申请表 (lmmigrationActNo.17of2010) A. PERSONALDETAILS(个人资料) FamilyName(姓氏) NI 倪 GivenName(名字) ZHAOHUI 朝晖 2.Gender(性别) Male 3. Maritl status (婚姻状况) Married 7.PassportNo(护照号) ER7814773"
    r = m.classify("递交", "Shanghai Huanyida-倪朝晖-4PERSONS-20260601.pdf", text)
    assert r["doc_type"] == "submission" and r["by"] == "keyword", r


def test_receipt():
    """签收回执(真实件样式):签收函/交付/签收日期/项目名称命中,直接定类。"""
    text = "重要文件签收函 尊敬的环球客户:环球项目部于2026年6月2日向您交付了以下重要文件 项目名称:瓦努阿图永居 客户姓名:倪朝晖 瓦努阿图永居卡 瓦努阿图永居证明书 签收人(客户名称):倪朝晖 签收日期:2026.6.11"
    r = m.classify("原件扫描+签收回执", "倪总签收回执.jpg", text)
    assert r["doc_type"] == "receipt" and r["by"] == "keyword", r


def test_scores_always_returned():
    r = m.classify("护照", "x.jpg", "随便一段不含关键词的文字" * 10)
    assert set(r["scores"].keys()) == set(m.DOC_TYPES), r
    assert r["doc_type"] is None, r


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个测试通过")
