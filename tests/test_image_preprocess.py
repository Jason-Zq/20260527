"""image_preprocess 单元测试:纠偏/放大/对比度增强(纯函数,合成图,不依赖 OCR 引擎)。

运行: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_image_preprocess.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import cv2
import numpy as np

import image_preprocess as ip


def _make_doc_image(w=900, h=1200, lines=12):
    """合成文档图:白底 + 多条黑色横线(模拟文字行)。"""
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    for i in range(lines):
        y = 80 + i * 80
        cv2.rectangle(img, (80, y), (w - 80, y + 24), (0, 0, 0), -1)
    return img


def _rotate(img, angle):
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_CONSTANT,
                          borderValue=(255, 255, 255))


def test_estimate_skew_zero_on_straight():
    img = _make_doc_image()
    angle = ip.estimate_skew_angle(img)
    assert abs(angle) < 0.5, f"平直文档应估出≈0°, 实际 {angle}"
    print("  OK   test_estimate_skew_zero_on_straight")


def test_estimate_skew_detects_rotation():
    img = _rotate(_make_doc_image(), 5.0)
    angle = ip.estimate_skew_angle(img)
    # deskew 用估计角旋转回去后,重估角应接近 0
    fixed = ip.deskew(img, angle)
    residual = ip.estimate_skew_angle(fixed)
    assert abs(residual) < 1.0, f"5° 倾斜矫正后残角应<1°, 实际 {residual}(估角 {angle})"
    print(f"  OK   test_estimate_skew_detects_rotation (est={angle:.2f}, residual={residual:.2f})")


def test_estimate_skew_negative_rotation():
    img = _rotate(_make_doc_image(), -3.0)
    angle = ip.estimate_skew_angle(img)
    fixed = ip.deskew(img, angle)
    residual = ip.estimate_skew_angle(fixed)
    assert abs(residual) < 1.0, f"-3° 倾斜矫正后残角应<1°, 实际 {residual}"
    print("  OK   test_estimate_skew_negative_rotation")


def test_estimate_skew_blank_image():
    img = np.full((800, 600, 3), 255, dtype=np.uint8)
    assert ip.estimate_skew_angle(img) == 0.0
    print("  OK   test_estimate_skew_blank_image")


def test_deskew_keeps_size_and_white_border():
    img = _rotate(_make_doc_image(), 4.0)
    fixed = ip.deskew(img, 4.0)
    assert fixed.shape == img.shape
    # 角落应为白边填充(文档旋转不产生黑边)
    assert fixed[0, 0].tolist() == [255, 255, 255] or fixed[-1, -1].tolist() == [255, 255, 255]
    print("  OK   test_deskew_keeps_size_and_white_border")


def test_maybe_upscale_small_image():
    img = np.full((1000, 800, 3), 255, dtype=np.uint8)  # 1200/800=1.5 倍,不触顶
    out = ip.maybe_upscale(img)
    assert min(out.shape[:2]) >= ip.MIN_SHORT_SIDE, f"小图应放大到短边≥{ip.MIN_SHORT_SIDE}"
    print("  OK   test_maybe_upscale_small_image")


def test_maybe_upscale_factor_cap():
    img = np.full((200, 200, 3), 255, dtype=np.uint8)  # 1200/200=6 倍,应被 3 倍封顶
    out = ip.maybe_upscale(img)
    assert out.shape[0] == 600, f"放大倍数应封顶 {ip.MAX_UPSCALE}x, 实际 {out.shape}"
    print("  OK   test_maybe_upscale_factor_cap")


def test_maybe_upscale_noop_on_large():
    img = np.full((2000, 1500, 3), 255, dtype=np.uint8)
    out = ip.maybe_upscale(img)
    assert out is img, "大图应原样返回(零成本)"
    print("  OK   test_maybe_upscale_noop_on_large")


def test_normalize_contrast_low_contrast():
    # 低对比图:窄灰度范围噪声(模拟发黄扫描件)
    rng = np.random.default_rng(42)
    img = rng.integers(150, 190, size=(400, 400, 3), dtype=np.uint8)
    before = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).std()
    out = ip.normalize_contrast(img)
    after = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).std()
    assert after > before, f"低对比图增强后 std 应提升({before:.1f} -> {after:.1f})"
    print(f"  OK   test_normalize_contrast_low_contrast (std {before:.1f} -> {after:.1f})")


def test_normalize_contrast_noop_on_good():
    img = _make_doc_image()  # 黑白文档,对比度高
    out = ip.normalize_contrast(img)
    assert out is img, "高对比图应原样返回"
    print("  OK   test_normalize_contrast_noop_on_good")


def test_preprocess_for_ocr_unicode_path():
    img = _rotate(_make_doc_image(), 5.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "歪扫描件_户口页.png")
        cv2.imwrite(path, img)
        out = ip.preprocess_for_ocr(path)
    assert out is not None, "中文路径读图不应失败"
    residual = ip.estimate_skew_angle(out)
    assert abs(residual) < 1.0, f"端到端:5° 倾斜矫正后残角应<1°, 实际 {residual}"
    print("  OK   test_preprocess_for_ocr_unicode_path")


def test_preprocess_for_ocr_missing_file():
    assert ip.preprocess_for_ocr("不存在的文件.png") is None
    print("  OK   test_preprocess_for_ocr_missing_file")


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
