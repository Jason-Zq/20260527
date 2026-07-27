"""ocr_service._page_has_big_image 单元测试(纯函数,mock pdfplumber page,不依赖真实 PDF)。

背景:扫描仪 App 会把劣质 OCR 文本层嵌进扫描 PDF,detect_pdf_type 只看文本长度
会误判 text 跳过真 OCR;现改为看页面是否被大图覆盖(扫描件特征)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_page_big_image.py
"""
import sys
import os
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from ocr_service import _page_has_big_image


def _page(w, h, images):
    return SimpleNamespace(width=w, height=h, images=images)


def _img(x0, top, x1, bottom):
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom}


def test_full_page_image_is_scan():
    # 整页大图(户口本扫描件实测: 522x841 页面 1 张 100% 覆盖图)
    p = _page(522, 841, [_img(0, 0, 522, 841)])
    assert _page_has_big_image(p) is True


def test_no_image_is_not_scan():
    # Word 导出数字页: 无图
    p = _page(595, 842, [])
    assert _page_has_big_image(p) is False


def test_small_logo_is_not_scan():
    # 数字页带小 logo: 100x80 << 60% 页面
    p = _page(595, 842, [_img(40, 40, 140, 120)])
    assert _page_has_big_image(p) is False


def test_partial_image_over_threshold_is_scan():
    # 覆盖 70% 的图(>60% 阈值)
    p = _page(100, 100, [_img(0, 0, 100, 70)])
    assert _page_has_big_image(p) is True


def test_partial_image_under_threshold_is_not_scan():
    # 覆盖 50% 的图(<60% 阈值)
    p = _page(100, 100, [_img(0, 0, 100, 50)])
    assert _page_has_big_image(p) is False


def test_zero_page_area_safe():
    p = _page(0, 0, [_img(0, 0, 10, 10)])
    assert _page_has_big_image(p) is False


if __name__ == "__main__":
    test_full_page_image_is_scan()
    print("PASS test_full_page_image_is_scan")
    test_no_image_is_not_scan()
    print("PASS test_no_image_is_not_scan")
    test_small_logo_is_not_scan()
    print("PASS test_small_logo_is_not_scan")
    test_partial_image_over_threshold_is_scan()
    print("PASS test_partial_image_over_threshold_is_scan")
    test_partial_image_under_threshold_is_not_scan()
    print("PASS test_partial_image_under_threshold_is_not_scan")
    test_zero_page_area_safe()
    print("PASS test_zero_page_area_safe")
    print("\n全部 6 个测试通过")
