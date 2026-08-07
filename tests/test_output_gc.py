"""output/(= /uploads 公开目录) 全量 30 天 GC 回归测试（纯函数,无外部依赖）。

背景（2026-08-07）：/uploads 在生产由 nginx 直发、本地已加白名单对齐——
output/ 是公开产物目录,文件必须限期回收。`_cleanup_expired_output` 从只扫
YYMMDDHHmmss_ 任务目录扩展为全量 30 天清扫:
  - 顶层除 templates/customer_files 外的所有条目(任务目录/杂散/散文件)超期即删
  - templates/{id}/{preview,fills}/ 子目录删,但 *.docx 模板原件与 {id}/ 目录本体不碰
    (模板库是业务资产,删了模板页全废)
  - customer_files/ 原件不碰(file_keep_until DB 驱动 GC 管,删早破坏在线查看),
    只兜底 previews/ 下的 Office 预览 PDF 缓存
  - templates/_parse/ 不碰(由 60 分钟 GC _cleanup_stale_template_temp 管)

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 ./.venv312/Scripts/python.exe tests/test_output_gc.py
"""
import sys
import os
import time
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import main as m

_OLD_AGE = 31 * 86400  # 31 天前(超 30 天阈值)
_FRESH_AGE = 60        # 1 分钟前(未超期)


def _touch(path: str, age: int) -> None:
    """把文件/目录 mtime 拨到 age 秒前。"""
    t = time.time() - age
    os.utime(path, (t, t))


def _build_tree(root: str) -> None:
    """在 root 下搭一棵模拟 output/ 的目录树。"""
    # 1. 过期任务目录(带内容)
    task = os.path.join(root, "260701000000_abcdef12", "images")
    os.makedirs(task)
    with open(os.path.join(task, "page_1.png"), "wb") as f:
        f.write(b"png")
    _touch(os.path.join(root, "260701000000_abcdef12"), _OLD_AGE)

    # 2. 未过期任务目录(应保留)
    task_new = os.path.join(root, "260807060000_zzzzzzzz")
    os.makedirs(task_new)
    _touch(task_new, _FRESH_AGE)

    # 3. 杂散目录 + 散文件(过期应删)
    stray = os.path.join(root, "fetched_tmp_xxx")
    os.makedirs(stray)
    _touch(stray, _OLD_AGE)
    stray_file = os.path.join(root, "orphan.png")
    with open(stray_file, "wb") as f:
        f.write(b"x")
    _touch(stray_file, _OLD_AGE)

    # 4. templates/{id}/: preview/fills 过期应删, docx 原件与目录本体永不删
    tpl = os.path.join(root, "templates", "42")
    for sub in ("preview", "fills"):
        d = os.path.join(tpl, sub)
        os.makedirs(d)
        with open(os.path.join(d, "x.bin"), "wb") as f:
            f.write(b"x")
        _touch(d, _OLD_AGE)
    docx = os.path.join(tpl, "template.docx")
    with open(docx, "wb") as f:
        f.write(b"docx")
    _touch(docx, _OLD_AGE)  # 原件即使 mtime 很老也不能删

    # 5. templates/_parse/(60 分钟 GC 管,本函数不碰,即使过期)
    parse_dir = os.path.join(root, "templates", "_parse")
    os.makedirs(parse_dir)
    _touch(parse_dir, _OLD_AGE)

    # 6. customer_files/ 原件(过期也不删,file_keep_until DB GC 管)
    cf_orig = os.path.join(root, "customer_files", "F-1.pdf")
    os.makedirs(os.path.dirname(cf_orig))
    with open(cf_orig, "wb") as f:
        f.write(b"pdf")
    _touch(cf_orig, _OLD_AGE)

    # 7. customer_files/previews/ 预览缓存(过期应删,未过期保留)
    prev = os.path.join(root, "customer_files", "previews")
    os.makedirs(prev)
    with open(os.path.join(prev, "old.pdf"), "wb") as f:
        f.write(b"old")
    _touch(os.path.join(prev, "old.pdf"), _OLD_AGE)
    with open(os.path.join(prev, "new.pdf"), "wb") as f:
        f.write(b"new")
    _touch(os.path.join(prev, "new.pdf"), _FRESH_AGE)


def test_cleanup_expired_output():
    root = tempfile.mkdtemp(prefix="output_gc_test_")
    orig_output_dir = m.OUTPUT_DIR
    try:
        _build_tree(root)
        m.OUTPUT_DIR = root
        m._cleanup_expired_output(30)

        # 顶层:过期任务目录/杂散删,未过期保留
        assert not os.path.exists(os.path.join(root, "260701000000_abcdef12")), "过期任务目录应删"
        assert not os.path.exists(os.path.join(root, "fetched_tmp_xxx")), "杂散目录应删"
        assert not os.path.exists(os.path.join(root, "orphan.png")), "散文件应删"
        assert os.path.isdir(os.path.join(root, "260807060000_zzzzzzzz")), "未过期任务目录应保留"

        # templates/{id}/: preview/fills 删, docx 原件与目录本体保留
        tpl = os.path.join(root, "templates", "42")
        assert not os.path.exists(os.path.join(tpl, "preview")), "preview 缓存应删"
        assert not os.path.exists(os.path.join(tpl, "fills")), "fills 生成物应删"
        assert os.path.isfile(os.path.join(tpl, "template.docx")), "模板 docx 原件绝不能删"
        assert os.path.isdir(tpl), "templates/{id}/ 目录本体应保留"

        # templates/_parse/ 由 60 分钟 GC 管,本函数不碰
        assert os.path.isdir(os.path.join(root, "templates", "_parse")), "_parse 不归本函数管"

        # customer_files/: 原件保留, previews 里过期删/未过期留
        assert os.path.isfile(os.path.join(root, "customer_files", "F-1.pdf")), \
            "customer_files 原件由 file_keep_until GC 管,本函数不删"
        assert not os.path.exists(os.path.join(root, "customer_files", "previews", "old.pdf")), \
            "过期预览缓存应删"
        assert os.path.isfile(os.path.join(root, "customer_files", "previews", "new.pdf")), \
            "未过期预览缓存应保留"
    finally:
        m.OUTPUT_DIR = orig_output_dir
        shutil.rmtree(root, ignore_errors=True)


def test_cleanup_missing_dir():
    """output/ 不存在时静默返回,不抛异常。"""
    orig = m.OUTPUT_DIR
    try:
        m.OUTPUT_DIR = os.path.join(tempfile.gettempdir(), "output_gc_nonexistent_zzz")
        m._cleanup_expired_output(30)  # 不应抛
    finally:
        m.OUTPUT_DIR = orig


if __name__ == "__main__":
    test_cleanup_expired_output()
    test_cleanup_missing_dir()
    print("All tests passed.")
