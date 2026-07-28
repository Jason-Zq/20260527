"""profile_import_service 接口清单适配测试(纯函数,不依赖 DB/网络)。

覆盖:
  - parse_api_manifest 扁平形态(entry.files[]);
  - parse_api_manifest 嵌套形态(entry.list[].files[] 拍平,结构见根目录 客户文件信息的接口.txt);
  - 垃圾文件("._" 开头)/无编号/重复编号过滤;
  - group_api_customers 同名合并 + 空姓名跳过。

运行(仓库根目录):
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_api_manifest.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from profile_import_service import group_api_customers, parse_api_manifest


def test_parse_flat():
    """扁平形态:垃圾文件过滤 / 无编号跳过 / 去重 / 字段映射。"""
    sample = {"customer_name": "张三", "files": [
        {"cloud_file_id": "1", "file_name": "张三身份证.pdf",
         "affter_progressname": "身份证", "relative_path": "张三/证件"},
        {"cloud_file_id": "2", "file_name": "._张三身份证.pdf",
         "affter_progressname": "身份证", "relative_path": None},
        {"cloud_file_id": "1", "file_name": "重复.pdf",
         "affter_progressname": "x", "relative_path": None},
        {"cloud_file_id": "", "file_name": "无编号.pdf",
         "affter_progressname": None, "relative_path": None},
    ]}
    m = parse_api_manifest(sample)
    assert m["client_name"] == "张三"
    assert len(m["files"]) == 1 and m["files"][0]["file_code"] == "1", m
    assert m["files"][0]["folder_name"] == "身份证"
    assert m["files"][0]["rel_path"] == "张三/证件"
    assert m["files"][0]["client_name"] == "张三"
    assert m["files"][0]["affter_entryoid"] is None  # 扁平形态无项目字段
    assert m["files"][0]["project_name"] is None
    assert m["projects"] == [], m  # 扁平形态无项目摘要
    assert m["skipped_junk"] == 2 and m["duplicates"] == 1, m
    print("[ok] parse_api_manifest 扁平形态")


def test_parse_nested():
    """嵌套形态(传 customer_code 的返回):按项目 list[].files[] 拍平,项目字段注入文件行。"""
    sample = {
        "customer_code": "U-001", "customer_name": "赵美琳", "crm_oid": "crm-1", "list_count": 4,
        "list": [
            {"affter_entryoid": "e1", "create_time": "2026-07-27 18:22:56",
             "projectno": "3382", "projectname": "高才通后续服务-续签",
             "projectno_detailed": "3549", "projectname_detailed": "A类无条件延期1年",
             "files": [
                {"cloud_file_id": "101", "file_name": "赵美琳-香港身份证.pdf",
                 "affter_progressname": "个人文件", "relative_path": "个人文件"},
                {"cloud_file_id": "102", "file_name": "递交截图.pdf",
                 "affter_progressname": "递交", "relative_path": "递交"},
            ]},
            {"affter_entryoid": "e2", "projectno": "3181", "projectname": "项目B",
             "files": [
                {"cloud_file_id": "103", "file_name": "户口本.pdf",
                 "affter_progressname": "个人信息", "relative_path": None},
                {"cloud_file_id": "101", "file_name": "跨项目重复.pdf",
                 "affter_progressname": "递交", "relative_path": None},
            ]},
            {"affter_entryoid": "e3", "projectname": "全垃圾项目",
             "files": [
                {"cloud_file_id": "104", "file_name": "._垃圾.pdf"},
            ]},
            {"affter_entryoid": None, "projectname": "无OID项目",
             "files": [
                {"cloud_file_id": "105", "file_name": "普通.pdf"},
            ]},
        ],
    }
    m = parse_api_manifest(sample)
    assert m["client_name"] == "赵美琳"
    assert len(m["files"]) == 4, m  # 5 个有效文件拍平,跨项目同编号去重剩 4
    assert m["duplicates"] == 1 and m["skipped_junk"] == 1
    assert [f["file_code"] for f in m["files"]] == ["101", "102", "103", "105"]
    assert m["files"][0]["folder_name"] == "个人文件"
    assert m["files"][2]["rel_path"] is None
    # 文件行项目字段:entryoid 路由键 + 显示名(二级优先,无二级回退一级)
    assert m["files"][0]["affter_entryoid"] == "e1"
    assert m["files"][0]["project_name"] == "A类无条件延期1年"
    assert m["files"][2]["affter_entryoid"] == "e2"
    assert m["files"][2]["project_name"] == "项目B"
    assert m["files"][3]["affter_entryoid"] is None
    assert m["files"][3]["project_name"] == "无OID项目"  # 显示名仍取 projectname;路由归默认案件(entryoid=None)
    # projects[]:按 entryoid 去重,file_count 只计清洗后文件;全垃圾项目保留(file_count=0);None 排除
    assert [p["affter_entryoid"] for p in m["projects"]] == ["e1", "e2", "e3"], m["projects"]
    p1, p2, p3 = m["projects"]
    assert p1["file_count"] == 2 and p2["file_count"] == 1 and p3["file_count"] == 0
    assert p1["projectno"] == "3382" and p1["projectname"] == "高才通后续服务-续签"
    assert p1["projectno_detailed"] == "3549" and p1["projectname_detailed"] == "A类无条件延期1年"
    assert p1["project_create_time"] == "2026-07-27 18:22:56"
    assert p2["projectname_detailed"] is None
    print("[ok] parse_api_manifest 嵌套形态拍平+项目摘要")


def test_group_customers():
    """同名多条目合并(不同 affter_entryoid);空姓名跳过;customer_code/crm_oid 取首个非空。"""
    customers = [
        {"customer_name": "田达", "customer_code": "U-100", "crm_oid": "crm-tian",
         "files": [{"cloud_file_id": "1", "file_name": "a.pdf"}]},
        {"customer_name": "", "customer_code": "U-000",
         "files": [{"cloud_file_id": "9", "file_name": "x.pdf"}]},
        {"customer_name": "田达", "customer_code": "U-100",
         "files": [{"cloud_file_id": "2", "file_name": "b.pdf"}]},
        {"customer_name": "李杰", "crm_oid": "crm-li", "list": [
            {"affter_entryoid": "e-li", "projectname": "项目L", "projectname_detailed": "L-续签",
             "files": [{"cloud_file_id": "3", "file_name": "c.pdf"}]}]},
    ]
    groups = group_api_customers(customers)
    assert [g["customer_name"] for g in groups] == ["田达", "李杰"], groups
    assert groups[0]["entry_count"] == 2
    assert groups[0]["customer_code"] == "U-100"
    assert groups[0]["crm_oid"] == "crm-tian"
    m = parse_api_manifest(groups[0]["customer"])
    assert len(m["files"]) == 2  # 两条目文件并起来
    assert m["projects"] == []  # 扁平条目无项目摘要
    assert groups[1]["entry_count"] == 1
    assert groups[1]["crm_oid"] == "crm-li"
    m2 = parse_api_manifest(groups[1]["customer"])
    assert len(m2["files"]) == 1  # 嵌套形态也能被合并后的扁平 files 覆盖
    # 嵌套注入的项目字段经合并后仍在文件行上,parse 出项目摘要
    assert m2["files"][0]["affter_entryoid"] == "e-li"
    assert m2["files"][0]["project_name"] == "L-续签"
    assert [p["affter_entryoid"] for p in m2["projects"]] == ["e-li"]
    print("[ok] group_api_customers 合并/跳过/crm_oid")


if __name__ == "__main__":
    test_parse_flat()
    test_parse_nested()
    test_group_customers()
    print("\n全部通过")
