"""证件字段提取规则(常量)。

规则从数据库(doc_extract_rules 表)迁至代码常量维护:改规则=改本文件+重启 worker。
draft->activate->disable 生命周期已移除;提取结果(doc_extract_results)的人工复核编辑闭环不受影响。

每条规则: {version, fields:[{key,label,description,required,target:{entity,column},example}], prompt_extra}
- target.entity: person(归因到人) / asset(家庭资产) / case(案件里程碑)
- target.column: person 字段名;entity=asset/case 时一律 None
提取读取点: profile_import_service._extract_one 调 get_rule(doc_type)。
"""
from typing import Optional

# 规则整体版本号:写入 doc_extract_results.rule_version 供溯源(改规则时手动 +1)
RULES_VERSION = 2


EXTRACT_RULES: dict[str, dict] = {
    "id_card": {
        "version": 1,
        "fields": [
            {
                "key": "name",
                "label": "姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "张三",
                "required": True,
                "description": "与证件上汉字一致"
            },
            {
                "key": "gender",
                "label": "性别",
                "target": {
                    "column": "gender",
                    "entity": "person"
                },
                "example": "男",
                "required": True,
                "description": "提取「男」或「女」"
            },
            {
                "key": "ethnicity",
                "label": "民族",
                "target": {
                    "column": "ethnicity",
                    "entity": "person"
                },
                "example": "汉族",
                "required": True,
                "description": "提取证件上的民族名称"
            },
            {
                "key": "birth_date",
                "label": "出生日期",
                "target": {
                    "column": "birth_date",
                    "entity": "person"
                },
                "example": "1990-01-01",
                "required": True,
                "description": "标准化为 YYYY-MM-DD 格式"
            },
            {
                "key": "id_number",
                "label": "公民身份号码",
                "target": {
                    "column": "id_number",
                    "entity": "person"
                },
                "example": "110101199001011234",
                "required": True,
                "description": "18位数字或末位X（大写）"
            },
            {
                "key": "hukou_address",
                "label": "住址",
                "target": {
                    "column": "hukou_address",
                    "entity": "person"
                },
                "example": "北京市东城区长安街1号",
                "required": True,
                "description": "身份证上的户籍地址"
            },
            {
                "key": "issue_authority",
                "label": "签发机关",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "北京市公安局",
                "required": False,
                "description": "发证机关全称"
            },
            {
                "key": "valid_period",
                "label": "有效期限",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "2020.01.01-2040.01.01",
                "required": False,
                "description": "如「2010.01.01-2030.01.01」或「长期」"
            }
        ],
        "prompt_extra": "1. 身份证有正反面，正面为个人信息（姓名、性别、民族、出生日期、住址、身份证号），反面为签发机关和有效期限，需合并提取。\n2. 出生日期、有效期限中的日期部分一律转换为 YYYY-MM-DD 标准格式，注意年份可能为两位（需补全为四位）或四位。\n3. 身份证号倒数第二位为性别校验位（奇男偶女），但直接提取性别字段优先取版面文字。\n4. 注意与临时身份证、护照等区分，临时身份证无有效期或不同；居民身份证号码固定18位，末位可能为X（大写）。"
    },
    "hukou": {
        "version": 2,
        "multi": True,
        "fields": [
            {
                "key": "name",
                "label": "姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "张三",
                "required": True,
                "description": ""
            },
            {
                "key": "relation_to_householder",
                "label": "与户主关系",
                "target": {
                    "column": "_relation",
                    "entity": "person"
                },
                "example": "子",
                "required": False,
                "description": "登记卡上「与户主关系」栏的值,原样输出(如:户主/配偶/子/女);封面或户主页取「户主」"
            },
            {
                "key": "gender",
                "label": "性别",
                "target": {
                    "column": "gender",
                    "entity": "person"
                },
                "example": "男",
                "required": True,
                "description": "提取为「男」或「女」"
            },
            {
                "key": "birth_date",
                "label": "出生日期",
                "target": {
                    "column": "birth_date",
                    "entity": "person"
                },
                "example": "1990-01-01",
                "required": True,
                "description": "格式化为YYYY-MM-DD"
            },
            {
                "key": "ethnicity",
                "label": "民族",
                "target": {
                    "column": "ethnicity",
                    "entity": "person"
                },
                "example": "汉族",
                "required": True,
                "description": "提取全称，如「汉族」"
            },
            {
                "key": "id_number",
                "label": "公民身份号码",
                "target": {
                    "column": "id_number",
                    "entity": "person"
                },
                "example": "110101199003071234",
                "required": True,
                "description": "保持原始字符串，含校验位"
            },
            {
                "key": "hukou_address",
                "label": "户籍地址",
                "target": {
                    "column": "hukou_address",
                    "entity": "person"
                },
                "example": "北京市东城区某街道1号",
                "required": True,
                "description": "完整地址，含省市区及详细"
            },
            {
                "key": "birth_place",
                "label": "出生地",
                "target": {
                    "column": "birth_place",
                    "entity": "person"
                },
                "example": "北京市东城区",
                "required": True,
                "description": "提取省/市/县名称"
            },
            {
                "key": "marital_status",
                "label": "婚姻状况",
                "target": {
                    "column": "marital_status",
                    "entity": "person"
                },
                "example": "未婚",
                "required": False,
                "description": "若OCR识别为空则不提取"
            }
        ],
        "prompt_extra": "1. 提取户口簿中所有常住人口登记卡成员（含户主本人），每张登记卡输出一条；索引页/封面/注意事项页忽略。\n2. 所有日期字段统一转换为YYYY-MM-DD格式，如「1990年1月1日」转为1990-01-01。\n3. 民族字段需提取完整名称，如「汉族」「蒙古族」，不要省略为「汉」「蒙」。\n4. 公民身份号码可能为15位或18位，保持OCR识别结果原样，不进行格式修正。"
    },
    "passport": {
        "version": 1,
        "fields": [
            {
                "key": "passport_no",
                "label": "护照号",
                "target": {
                    "column": "passport_no",
                    "entity": "person"
                },
                "example": "E12345678",
                "required": True,
                "description": "护照号码，字母加数字，如E12345678"
            },
            {
                "key": "name",
                "label": "姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "张三",
                "required": True,
                "description": "中文姓名，如张三"
            },
            {
                "key": "name_en",
                "label": "姓名(英文)",
                "target": {
                    "column": "name_en",
                    "entity": "person"
                },
                "example": "ZHANG SAN",
                "required": True,
                "description": "英文/拼音姓名，全大写，姓和名之间空格"
            },
            {
                "key": "gender",
                "label": "性别",
                "target": {
                    "column": "gender",
                    "entity": "person"
                },
                "example": "男",
                "required": True,
                "description": "男/女 或 M/F，统一转为中文"
            },
            {
                "key": "nationality",
                "label": "国籍",
                "target": {
                    "column": "nationality",
                    "entity": "person"
                },
                "example": "中国",
                "required": True,
                "description": "国家中文名称，如中国"
            },
            {
                "key": "birth_date",
                "label": "出生日期",
                "target": {
                    "column": "birth_date",
                    "entity": "person"
                },
                "example": "1990-01-01",
                "required": True,
                "description": "格式统一为YYYY-MM-DD"
            },
            {
                "key": "birth_place",
                "label": "出生地",
                "target": {
                    "column": "birth_place",
                    "entity": "person"
                },
                "example": "广东省广州市",
                "required": True,
                "description": "省/市/县或国家，如广东广州"
            },
            {
                "key": "passport_issue_date",
                "label": "签发日期",
                "target": {
                    "column": "passport_issue_date",
                    "entity": "person"
                },
                "example": "2020-01-01",
                "required": True,
                "description": "格式统一为YYYY-MM-DD"
            },
            {
                "key": "passport_expiry_date",
                "label": "有效期至",
                "target": {
                    "column": "passport_expiry_date",
                    "entity": "person"
                },
                "example": "2030-01-01",
                "required": True,
                "description": "格式统一为YYYY-MM-DD"
            },
            {
                "key": "issue_authority",
                "label": "签发机关",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "公安部出入境管理局",
                "required": False,
                "description": "签发机关名称，如公安部出入境管理局"
            },
            {
                "key": "passport_type",
                "label": "护照类型",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "P",
                "required": False,
                "description": "证件类型代码，如P(普通)、D(外交)等"
            }
        ],
        "prompt_extra": "1. 注意区分中文姓名与英文姓名（拼音），英文姓名通常全大写且姓在前（如ZHANG SAN）。2. 日期格式常见为DD-MMM-YYYY（如01-JAN-1990）或DD-MM-YYYY，需统一转换为YYYY-MM-DD。3. 护照号码以字母开头（如E、G、P等），与身份证号（纯数字18位）区分。4. 若OCR识别来自多面（如有些护照有个人信息页和签证页），需合并正面信息，忽略备注页内容。"
    },
    "kyc_form": {
        "version": 1,
        "fields": [
            {
                "key": "name",
                "label": "客户姓名",
                "target": {
                    "layer": "declared",
                    "column": "name",
                    "entity": "person"
                },
                "example": "倪朝晖",
                "required": True,
                "description": "表格中「客户姓名」中英并列,取中文名"
            },
            {
                "key": "passport_no",
                "label": "护照号码",
                "target": {
                    "layer": "declared",
                    "column": "passport_no",
                    "entity": "person"
                },
                "example": "ED2080944",
                "required": False,
                "description": ""
            },
            {
                "key": "phone",
                "label": "联络号码",
                "target": {
                    "layer": "declared",
                    "column": "phone",
                    "entity": "person"
                },
                "example": "13651822227",
                "required": False,
                "description": ""
            },
            {
                "key": "email",
                "label": "联络邮箱",
                "target": {
                    "layer": "declared",
                    "column": "email",
                    "entity": "person"
                },
                "example": "13651822227@163.com",
                "required": False,
                "description": ""
            },
            {
                "key": "current_address",
                "label": "邮寄地址",
                "target": {
                    "layer": "declared",
                    "column": "current_address",
                    "entity": "person"
                },
                "example": "上海政和路388弄35号301室",
                "required": False,
                "description": ""
            },
            {
                "key": "postal_code",
                "label": "邮政编码",
                "target": {
                    "layer": "declared",
                    "column": "postal_code",
                    "entity": "person"
                },
                "example": "200000",
                "required": False,
                "description": ""
            },
            {
                "key": "occupation",
                "label": "职务",
                "target": {
                    "layer": "declared",
                    "column": "occupation",
                    "entity": "person"
                },
                "example": "总经理",
                "required": False,
                "description": ""
            },
            {
                "key": "employer",
                "label": "公司名称",
                "target": {
                    "layer": "declared",
                    "column": "employer",
                    "entity": "person"
                },
                "example": "昱栋市政建设工程(上海)有限公司",
                "required": False,
                "description": "客户的公司/雇主名称"
            },
            {
                "key": "business_nature",
                "label": "公司性质",
                "target": {
                    "layer": "declared",
                    "column": "business_nature",
                    "entity": "person"
                },
                "example": "有限责任公司",
                "required": False,
                "description": ""
            },
            {
                "key": "annual_income",
                "label": "客户年收入",
                "target": {
                    "layer": "declared",
                    "column": "annual_income",
                    "entity": "person"
                },
                "example": "100万",
                "required": False,
                "description": "含币种/单位,按原文"
            },
            {
                "key": "shareholding",
                "label": "持股情况",
                "target": {
                    "layer": "declared",
                    "column": "shareholding",
                    "entity": "person"
                },
                "example": "Yes",
                "required": False,
                "description": "是否持有任何公司超过25%股权"
            },
            {
                "key": "source_of_funds",
                "label": "资产来源",
                "target": {
                    "layer": "declared",
                    "column": "source_of_funds",
                    "entity": "person"
                },
                "example": "存款/投资",
                "required": False,
                "description": ""
            },
            {
                "key": "planned_deposit",
                "label": "预计存款额度",
                "target": {
                    "layer": "declared",
                    "column": "planned_deposit",
                    "entity": "person"
                },
                "example": "30万美金",
                "required": False,
                "description": ""
            },
            {
                "key": "residence_plan",
                "label": "居住地计划",
                "target": {
                    "layer": "declared",
                    "column": "residence_plan",
                    "entity": "person"
                },
                "example": "中国",
                "required": False,
                "description": "现在或未来一年的居住地"
            }
        ],
        "prompt_extra": "1. 中英双语表格,取中文列的值;英文拼音名不必提取。2. 金额/数量含币种单位按原文输出。3. 只提取表格中明确填写的值,空项输出 None。"
    },
    "marriage_cert": {
        "version": 1,
        "fields": [
            {
                "key": "holder_name",
                "label": "持证人姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "张三",
                "required": True,
                "description": "提取持证人姓名，不含空格和标点"
            },
            {
                "key": "holder_gender",
                "label": "性别",
                "target": {
                    "column": "gender",
                    "entity": "person"
                },
                "example": "男",
                "required": False,
                "description": "提取持证人性别（男/女）"
            },
            {
                "key": "holder_birth_date",
                "label": "出生日期",
                "target": {
                    "column": "birth_date",
                    "entity": "person"
                },
                "example": "1990-01-01",
                "required": False,
                "description": "格式 YYYY-MM-DD"
            },
            {
                "key": "holder_id_number",
                "label": "身份证号码",
                "target": {
                    "column": "id_number",
                    "entity": "person"
                },
                "example": "110101199001011234",
                "required": True,
                "description": "18位或15位身份证号"
            },
            {
                "key": "marriage_date",
                "label": "结婚登记日期",
                "target": {
                    "column": "marriage_date",
                    "entity": "person"
                },
                "example": "2020-05-20",
                "required": True,
                "description": "格式 YYYY-MM-DD"
            },
            {
                "key": "marriage_authority",
                "label": "结婚登记机关",
                "target": {
                    "column": "marriage_authority",
                    "entity": "person"
                },
                "example": "北京市朝阳区民政局",
                "required": True,
                "description": "提取登记机关全称"
            },
            {
                "key": "marriage_cert_no",
                "label": "结婚证编号",
                "target": {
                    "column": "marriage_cert_no",
                    "entity": "person"
                },
                "example": "京朝结字2020-123456",
                "required": True,
                "description": "保留原始字符串，含汉字或数字"
            },
            {
                "key": "marital_status",
                "label": "婚姻状况",
                "target": {
                    "column": "marital_status",
                    "entity": "person"
                },
                "example": "已婚",
                "required": True,
                "description": "从证件类型推断，固定值「已婚」"
            },
            {
                "key": "spouse_name",
                "label": "配偶姓名",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "刘小娟",
                "required": False,
                "description": "结婚证上另一方(非持证人)的姓名;用于交叉核对,不写库"
            }
        ],
        "prompt_extra": "1. 注意区分持证人与配偶，仅提取持证人信息，不要混淆；2. 日期字段务必统一转换为 YYYY-MM-DD 格式；3. 身份证号可能为15位，保留原样不做转换；4. 结婚证编号可能包含汉字、数字或括号，完整保留原始字符串。 5. 配偶信息仅提取配偶姓名,配偶其他字段一律不抽。"
    },
    "birth_cert": {
        "version": 1,
        "fields": [
            {
                "key": "birth_cert_no",
                "label": "出生医学证编号",
                "target": {
                    "column": "birth_cert_no",
                    "entity": "person"
                },
                "example": "E4403012023XXXXXX",
                "required": True,
                "description": "证件右上角或首页顶部的唯一编号，字母与数字组合"
            },
            {
                "key": "name",
                "label": "新生儿姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "张三",
                "required": True,
                "description": "与证件上姓名栏完全一致，不含空格及标点"
            },
            {
                "key": "gender",
                "label": "性别",
                "target": {
                    "column": "gender",
                    "entity": "person"
                },
                "example": "男",
                "required": True,
                "description": "提取为「男」或「女」，注意区分「性别」栏"
            },
            {
                "key": "birth_date",
                "label": "出生日期",
                "target": {
                    "column": "birth_date",
                    "entity": "person"
                },
                "example": "2023-06-15",
                "required": True,
                "description": "格式统一为YYYY-MM-DD，注意年份与月份数字占位"
            },
            {
                "key": "birth_place",
                "label": "出生地点",
                "target": {
                    "column": "birth_place",
                    "entity": "person"
                },
                "example": "广东省广州市天河区",
                "required": True,
                "description": "提取省市区县全称，如「广东省广州市天河区」"
            },
            {
                "key": "birth_hospital",
                "label": "出生医院",
                "target": {
                    "column": "birth_hospital",
                    "entity": "person"
                },
                "example": "广州市妇女儿童医疗中心",
                "required": True,
                "description": "接生机构全称，可能与「签发机构」不同，以「出生地点」下方标注为准"
            },
            {
                "key": "nationality",
                "label": "国籍",
                "target": {
                    "column": "nationality",
                    "entity": "person"
                },
                "example": "中国",
                "required": False,
                "description": "通常为「中国」，若空白则默认为中国"
            },
            {
                "key": "ethnicity",
                "label": "民族",
                "target": {
                    "column": "ethnicity",
                    "entity": "person"
                },
                "example": "汉族",
                "required": False,
                "description": "如「汉族」「壮族」，注意与「国籍」不同"
            },
            {
                "key": "father_name",
                "label": "父亲姓名",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "李四",
                "required": False,
                "description": "证件「父亲」字段，用于家庭成员归因，无对应列时只提取不写入"
            },
            {
                "key": "mother_name",
                "label": "母亲姓名",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "王五",
                "required": False,
                "description": "证件「母亲」字段，用于家庭成员归因"
            },
            {
                "key": "father_id_number",
                "label": "父亲身份证号",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "440301198012345678",
                "required": False,
                "description": "18位或15位数字，可能含X，保留原样"
            },
            {
                "key": "mother_id_number",
                "label": "母亲身份证号",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "440301198109876543",
                "required": False,
                "description": "格式同上"
            },
            {
                "key": "relation_to_main",
                "label": "与户主关系",
                "target": {
                    "column": "_relation",
                    "entity": "person"
                },
                "example": "子",
                "required": False,
                "description": "新生儿与户主的关系,按新生儿性别推:男→子,女→女"
            }
        ],
        "prompt_extra": "1. 出生医学证明通常为单页或两页（正反面），注意正反面的信息合并提取，如正面有新生儿基本信息，反面可能有签发信息或盖章。2. 日期字段（出生日期、签发日期）统一转换为YYYY-MM-DD格式，年份不得省略前两位。3. 注意区分新生儿姓名与父母姓名，避免混淆；父母身份证号若模糊需提示人工复核。4. 与「户口簿」类似证件区分：出生证是首次登记凭证，无户籍地址和身份证号（新生儿无身份证号），且必有出生医院字段。"
    },
    "property_cert": {
        "version": 1,
        "fields": [
            {
                "key": "holder_name",
                "label": "权利人",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "张三",
                "required": True,
                "description": "提取第一权利人的姓名"
            },
            {
                "key": "id_number",
                "label": "证件号码",
                "target": {
                    "column": "id_number",
                    "entity": "person"
                },
                "example": "440301199001011234",
                "required": True,
                "description": "提取第一权利人的身份证号码"
            },
            {
                "key": "address",
                "label": "坐落",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "广州市天河区xx路xx号xx栋xx房",
                "required": True,
                "description": "房屋坐落地址"
            },
            {
                "key": "area",
                "label": "面积",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "123.45",
                "required": True,
                "description": "房屋建筑面积（平方米）"
            },
            {
                "key": "usage",
                "label": "用途",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "住宅",
                "required": False,
                "description": "房屋规划用途"
            },
            {
                "key": "right_type",
                "label": "权利类型",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "国有建设用地使用权/房屋所有权",
                "required": False,
                "description": "如国有建设用地使用权/房屋所有权"
            },
            {
                "key": "right_status",
                "label": "权利状态",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "现状",
                "required": False,
                "description": "如现状、抵押、查封等"
            },
            {
                "key": "register_date",
                "label": "登记日期",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "2020-06-15",
                "required": False,
                "description": "格式YYYY-MM-DD"
            },
            {
                "key": "cert_no",
                "label": "不动产权证书号",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "粤(2020)广州市不动产权第1234567号",
                "required": True,
                "description": "完整证书编号"
            },
            {
                "key": "co_ownership",
                "label": "共有情况",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "单独所有",
                "required": False,
                "description": "如单独所有、共同共有、按份共有"
            },
            {
                "key": "land_term",
                "label": "土地使用期限",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "2010-01-01至2080-01-01",
                "required": False,
                "description": "起止日期，如2010-01-01至2080-01-01"
            },
            {
                "key": "other_rights",
                "label": "他项权利",
                "target": {
                    "column": None,
                    "entity": "asset"
                },
                "example": "无",
                "required": False,
                "description": "抵押权等登记信息"
            }
        ],
        "prompt_extra": "1. 注意区分不动产权证和房产证，新版不动产权证有二维码和统一编号；2. 正反面信息需合并，如附记页可能有抵押信息；3. 日期格式统一为YYYY-MM-DD，注意OCR可能识别为中文日期；4. 多权利人时，只提取第一权利人信息。"
    },
    "no_crime": {
        "version": 1,
        "fields": [
            {
                "key": "name",
                "label": "姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "张三",
                "required": True,
                "description": "提取姓名，与身份证一致"
            },
            {
                "key": "gender",
                "label": "性别",
                "target": {
                    "column": "gender",
                    "entity": "person"
                },
                "example": "男",
                "required": False,
                "description": "提取性别，男/女"
            },
            {
                "key": "birth_date",
                "label": "出生日期",
                "target": {
                    "column": "birth_date",
                    "entity": "person"
                },
                "example": "1990-01-01",
                "required": False,
                "description": "格式转换为YYYY-MM-DD"
            },
            {
                "key": "id_number",
                "label": "身份证号码",
                "target": {
                    "column": "id_number",
                    "entity": "person"
                },
                "example": "110101199001011234",
                "required": True,
                "description": "18位身份证号，含字母大写"
            },
            {
                "key": "hukou_address",
                "label": "户籍地址",
                "target": {
                    "column": "hukou_address",
                    "entity": "person"
                },
                "example": "北京市东城区长安街1号",
                "required": False,
                "description": "完整户籍地址，与身份证一致"
            },
            {
                "key": "no_crime_cert_no",
                "label": "无犯罪记录证明编号",
                "target": {
                    "column": "no_crime_cert_no",
                    "entity": "person"
                },
                "example": "NO.20240001",
                "required": True,
                "description": "证明上的编号，含字母数字"
            },
            {
                "key": "no_crime_issue_date",
                "label": "开具日期",
                "target": {
                    "column": "no_crime_issue_date",
                    "entity": "person"
                },
                "example": "2024-06-15",
                "required": True,
                "description": "证明开具日期;若文本只给查询期间(如 在YYYY-MM-DD至YYYY-MM-DD期间),取期间止日期"
            },
            {
                "key": "issuing_authority",
                "label": "签发机关",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "北京市公安局海淀分局",
                "required": False,
                "description": "证明上的签发机关名称（仅提取不写入）"
            }
        ],
        "prompt_extra": "1. 注意日期格式可能为「2024年06月15日」，需统一转换为YYYY-MM-DD；2. 证明编号可能包含字母、数字或连字符，需完整保留；3. 姓名、身份证号、户籍地址需与身份证信息交叉核对，确保一致；4. 注意与「无犯罪记录公证」区分，本证件为公安机关出具，无公证书编号及公证处信息。"
    },
    "submission": {
        "version": 1,
        "fields": [
            {
                "key": "name",
                "label": "姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "倪朝晖",
                "required": True,
                "description": "主申请人中文姓名(FamilyName+GivenName 对应的中文)"
            },
            {
                "key": "case_type",
                "label": "案件类型",
                "target": {
                    "column": None,
                    "entity": "case"
                },
                "example": "瓦努阿图永居",
                "required": True,
                "description": "申请的项目类型(如 瓦努阿图永居)"
            },
            {
                "key": "submit_date",
                "label": "递交",
                "target": {
                    "column": None,
                    "entity": "case"
                },
                "example": "2026-06-01",
                "required": True,
                "description": "申请日期(ApplicationDate/申請日期),即递交日期"
            },
            {
                "key": "notary_date",
                "label": "公证",
                "target": {
                    "column": None,
                    "entity": "case"
                },
                "example": "2026-05-27",
                "required": False,
                "description": "申请包内附材料的公证日期(多份公证取其一);没有公证材料则 None"
            }
        ],
        "prompt_extra": "申请包通常是 申请表+护照/出生证/无犯罪/公证 等合订扫描件,只抽申请包层面的信息。姓名取主申请人(Principal)中文名。里程碑日期(递交/公证)从申请表签署区和公证页提取,统一 YYYY-MM-DD,找不到就 None,不要编造。表单里的护照号/地址等个人字段一律不抽。"
    },
    "receipt": {
        "version": 1,
        "fields": [
            {
                "key": "name",
                "label": "客户姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "倪朝晖",
                "required": False,
                "description": "回执上打印体的客户姓名(非手写签名)"
            },
            {
                "key": "case_type",
                "label": "案件类型",
                "target": {
                    "column": None,
                    "entity": "case"
                },
                "example": "瓦努阿图永居",
                "required": False,
                "description": "回执上的项目名称(如 瓦努阿图永居)"
            },
            {
                "key": "sign_date",
                "label": "签收",
                "target": {
                    "column": None,
                    "entity": "case"
                },
                "example": "2026-06-11",
                "required": True,
                "description": "签收日期"
            },
            {
                "key": "deliver_date",
                "label": "交付",
                "target": {
                    "column": None,
                    "entity": "case"
                },
                "example": "2026-06-02",
                "required": False,
                "description": "回执上「于X年X月X日向您交付」的交付日期;OCR 年份不清时按上下文推断,不确定就 None"
            }
        ],
        "prompt_extra": "签收函重点抽两类日期:签收日期与交付日期,统一 YYYY-MM-DD。项目名称原样提取。手写签名通常识别不清,客户姓名以打印体为准,没有就 None。"
    },
    "approval": {
        "version": 2,
        "fields": [
            {
                "key": "name",
                "label": "姓名",
                "target": {
                    "column": "name",
                    "entity": "person"
                },
                "example": "张三",
                "required": False,
                "description": "中文姓名;英文证件(永居卡等)上没有则 None"
            },
            {
                "key": "name_en",
                "label": "英文名/拼音",
                "target": {
                    "column": "name_en",
                    "entity": "person"
                },
                "example": "ZHANG SAN",
                "required": True,
                "description": "拼音/英文名,Surname + Given Name 合并(如 NI ZHAOHUI);系统按它归因到人"
            },
            {
                "key": "gender",
                "label": "性别",
                "target": {
                    "column": "gender",
                    "entity": "person"
                },
                "example": "男",
                "required": True,
                "description": "取值「男」或「女」"
            },
            {
                "key": "birth_date",
                "label": "出生日期",
                "target": {
                    "column": "birth_date",
                    "entity": "person"
                },
                "example": "1980-05-15",
                "required": True,
                "description": "统一转换为 YYYY-MM-DD 格式"
            },
            {
                "key": "nationality",
                "label": "国籍",
                "target": {
                    "column": "nationality",
                    "entity": "person"
                },
                "example": "美国",
                "required": True,
                "description": "证件上标注的国籍，如「中国」「美国」等"
            },
            {
                "key": "birth_place",
                "label": "出生地",
                "target": {
                    "column": "birth_place",
                    "entity": "person"
                },
                "example": "纽约",
                "required": False,
                "description": "如证件上有出生地信息则提取，无则留空"
            },
            {
                "key": "approval_no",
                "label": "批复号/获批卡号",
                "target": {
                    "column": "approval_no",
                    "entity": "person"
                },
                "example": "A123456789",
                "required": True,
                "description": "证件上的唯一编号，永居卡为卡号，批复函为批复号"
            },
            {
                "key": "approval_date",
                "label": "批复/签发日期",
                "target": {
                    "column": "approval_date",
                    "entity": "person"
                },
                "example": "2023-01-15",
                "required": True,
                "description": "统一转换为 YYYY-MM-DD 格式"
            },
            {
                "key": "passport_no",
                "label": "护照号",
                "target": {
                    "column": "passport_no",
                    "entity": "person"
                },
                "example": "E12345678",
                "required": False,
                "description": "如证件上关联有护照号则提取，无则留空"
            },
            {
                "key": "expiry_date",
                "label": "有效期至",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "2028-01-14",
                "required": False,
                "description": "证件过期日期，统一 YYYY-MM-DD 格式；无可写列对应，仅抽取不写入"
            },
            {
                "key": "issuing_authority",
                "label": "签发机关",
                "target": {
                    "column": None,
                    "entity": "person"
                },
                "example": "国家移民管理局",
                "required": False,
                "description": "证件签发机构名称；无可写列对应，仅抽取不写入"
            },
            {
                "key": "case_approved",
                "label": "获批",
                "target": {
                    "column": None,
                    "entity": "case"
                },
                "example": "2026-06-03",
                "required": False,
                "description": "获批/签发日期(与 approval_date 相同),作为案件里程碑"
            }
        ],
        "prompt_extra": "1. 正反面信息合并：永居卡正面通常有照片、姓名、卡号、出生日期等，背面可能包含签发机关、有效期、国籍等，需将正反面 OCR 结果合并后再提取。\n2. 日期格式统一：所有日期（出生日期、签发日期、有效期）均需转换为 YYYY-MM-DD 格式，原格式若为其他（如 DD/MM/YYYY 或 MMM DD, YYYY）需解析后转换。\n3. 区分永居卡与批复函：永居卡带有生物信息（照片、指纹）且卡号格式固定，批复函通常为公文格式且可能包含批准类别、关联护照号等信息，需根据版面特征区分并对应提取字段。\n4. 注意英文名大小写：英文名/拼音按照证件原文保留大写字母，无需强制全大写或首字母大写。 3. 一份文件含多张卡/多人时,只提取第一位(主卡持有人)。"
    }
}


def get_rule(doc_type: str) -> Optional[dict]:
    """取某证件类型的提取规则。返回 {doc_type, version, fields, prompt_extra, multi} 或 None。

    对齐原 doc_extract_crud.get_active_rule 的返回结构(去掉 id/status/reviewed_* 生命周期字段),
    调用方 profile_import_service 用到的 rule[version/fields/prompt_extra/doc_type] 均保留。
    multi=True 表示多人模式(如户口本整本),由 extract_doc_fields_multi 处理。
    """
    r = EXTRACT_RULES.get(doc_type)
    if r is None:
        return None
    return {'doc_type': doc_type, 'version': r['version'],
            'fields': r['fields'], 'prompt_extra': r.get('prompt_extra'),
            'multi': r.get('multi', False)}

