"""profile_import_service.parse_excel_manifest 测试(真实 客户文件信息例子.xlsx + 构造坏表)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_profile_excel_parse.py
"""
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from openpyxl import Workbook

from profile_import_service import parse_excel_manifest

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
EXAMPLE_XLSX = os.path.join(REPO_ROOT, "客户文件信息例子.xlsx")


def test_parse_example_xlsx():
    m = parse_excel_manifest(EXAMPLE_XLSX)
    # 62 数据行(其中 file_code 重复行去重;文件编码+文件名皆空的才跳过)
    assert m["client_name"] == "倪朝晖", m["client_name"]
    assert len(m["files"]) >= 55, len(m["files"])
    codes = [f["file_code"] for f in m["files"] if f["file_code"]]
    # 文件编码是字符串,不是 float(防 openpyxl 把数字编码读成数值)
    assert all(c.isdigit() for c in codes), codes[:5]
    assert "3186353" in codes and "3189712" in codes, codes[-3:]
    # 编码唯一(parser 去重)
    assert len(codes) == len(set(codes)), "file_code 有重复"
    # 错名列"文件啊名称"被解析为 filename
    names = {f["filename"] for f in m["files"]}
    assert "倪成.jpg" in names and "倪想出生证.pdf" in names, list(names)[:5]
    # 文件夹/相对路径提取
    folders = {f["folder_name"] for f in m["files"]}
    assert "护照" in folders and "户口本2026" in folders, folders
    rels = {f["rel_path"] for f in m["files"] if f["rel_path"]}
    assert "身份证2026/倪朝晖" in rels, rels


def _write_tmp_xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


def test_missing_required_column():
    path = _write_tmp_xlsx([
        ["客户姓名", "文件编码"],  # 缺"文件名称"
        ["张三", "1001"],
    ])
    try:
        try:
            parse_excel_manifest(path)
            raise AssertionError("应当报缺列")
        except ValueError as e:
            assert "文件名称" in str(e), str(e)
    finally:
        os.remove(path)


def test_empty_client_name():
    path = _write_tmp_xlsx([
        ["售后文件夹名称", "文件编码", "客户姓名", "文件名称"],
        ["护照", "1001", "", "a.jpg"],
    ])
    try:
        try:
            parse_excel_manifest(path)
            raise AssertionError("应当报客户姓名为空")
        except ValueError as e:
            assert "客户姓名" in str(e), str(e)
    finally:
        os.remove(path)


def test_no_data_rows():
    path = _write_tmp_xlsx([["售后文件夹名称", "文件编码", "客户姓名", "文件名称"]])
    try:
        try:
            parse_excel_manifest(path)
            raise AssertionError("应当报无有效数据行")
        except ValueError as e:
            assert "无有效数据行" in str(e), str(e)
    finally:
        os.remove(path)


def test_int_and_float_codes_normalized():
    path = _write_tmp_xlsx([
        ["售后文件夹名称", "文件编码", "客户姓名", "文件名称"],
        ["护照", 3186353, "张三", "a.jpg"],      # int
        ["护照", 3186354.0, "张三", "b.jpg"],    # float(integral)
        ["护照", "3186355", "张三", "c.jpg"],
    ])
    try:
        m = parse_excel_manifest(path)
        codes = [f["file_code"] for f in m["files"]]
        assert codes == ["3186353", "3186354", "3186355"], codes
        assert m["client_name"] == "张三"
    finally:
        os.remove(path)


def test_duplicates_and_blank_rows():
    path = _write_tmp_xlsx([
        ["售后文件夹名称", "文件编码", "客户姓名", "文件名称"],
        ["护照", "1001", "张三", "a.jpg"],
        ["", "", "", ""],                        # 空行跳过
        ["护照", "1001", "张三", "a2.jpg"],      # 重复编码
        ["护照", "1002", "张三", "b.jpg"],
    ])
    try:
        m = parse_excel_manifest(path)
        assert len(m["files"]) == 2, m["files"]
        assert m["duplicates"] == 1, m
    finally:
        os.remove(path)


def test_mode_client_name_tie_break():
    """同票取先出现者。"""
    path = _write_tmp_xlsx([
        ["售后文件夹名称", "文件编码", "客户姓名", "文件名称"],
        ["a", "1", "张三", "a.jpg"],
        ["a", "2", "李四", "b.jpg"],
    ])
    try:
        m = parse_excel_manifest(path)
        assert m["client_name"] == "张三", m["client_name"]
    finally:
        os.remove(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个测试通过")
