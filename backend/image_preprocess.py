"""OCR 图像前处理(纯函数,可单测)。

针对歪扫描件/手机拍照件/低对比扫描件,在喂 OCR 引擎前做轻量矫正:
  - 纠偏(deskew): Otsu 二值化 → 文字像素 minAreaRect 估角,|角|≥阈值才转,±15° 截断
  - 小图放大: 短边过小时 INTER_CUBIC 放大(有倍数上限)
  - 低对比度增强: 灰度标准差过低时 LAB-CLAHE(发黄/底纹扫描件)

所有步骤自适应:好图原样通过(近零成本)。CPU 开销发生在 run_ocr 引擎锁外。
"""
import cv2
import numpy as np

# 纠偏:估计角度绝对值 ≥ 此值才旋转(避免好图被微旋引入插值模糊)
MIN_SKEW_ANGLE = 0.5
# 估计角度超过 ±15° 视为误判(深色边框/整版照片干扰),放弃纠偏
MAX_SKEW_ANGLE = 15.0
# minAreaRect 输入点数上限(超出则等距抽样,保速度)
_SKEW_SAMPLE_MAX = 200_000
# 前景点数低于此值认为图中没有可估角的文字内容
_SKEW_MIN_POINTS = 500

# 小图放大:短边低于此值(像素)才放大,倍数封顶
MIN_SHORT_SIDE = 1200
MAX_UPSCALE = 3.0

# 低对比度增强:灰度标准差低于此值才做 CLAHE(正常扫描件 std 60-90,发黄件常 <40)
CONTRAST_STD_MIN = 45.0


def load_image_bgr(img_path: str):
    """Unicode 路径安全读图(cv2.imread 在 Windows 中文路径下返回 None)。失败返回 None。"""
    try:
        data = np.fromfile(img_path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def estimate_skew_angle(bgr) -> float:
    """估计矫正角(度):把返回值直接传给 deskew/getRotationMatrix2D 即可转正。

    无法估计/角度不可信时返回 0.0。
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # 文字是暗像素:Otsu 反阈值取前景
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(bw > 0))  # (y, x)
    if len(coords) < _SKEW_MIN_POINTS:
        return 0.0
    if len(coords) > _SKEW_SAMPLE_MAX:
        step = len(coords) // _SKEW_SAMPLE_MAX + 1
        coords = coords[::step]
    rect = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))  # minAreaRect 要 (x, y)
    # OpenCV 4.5+ minAreaRect 返回 angle ∈ [-90,0);该角即内容旋转角,
    # 直接作为 getRotationMatrix2D 的矫正角使用(与内容旋转方向相反,实测验证)。
    # 接近 ±45° 的长宽歧义绕回:按旋转角不超过 45° 折算。
    angle = rect[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) > MAX_SKEW_ANGLE:
        return 0.0
    return angle


def deskew(bgr, angle: float):
    """按给定角度旋转矫正,白边填充(文档图旋转不产生黑边噪点)。"""
    h, w = bgr.shape[:2]
    m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(
        bgr, m, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def maybe_upscale(bgr, min_side: int = MIN_SHORT_SIDE, max_factor: float = MAX_UPSCALE):
    """短边 < min_side 时 INTER_CUBIC 放大(倍数封顶 max_factor),否则原样返回。"""
    h, w = bgr.shape[:2]
    short = min(h, w)
    if short >= min_side or short <= 0:
        return bgr
    factor = min(min_side / short, max_factor)
    if factor <= 1.0:
        return bgr
    return cv2.resize(bgr, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def normalize_contrast(bgr, std_min: float = CONTRAST_STD_MIN):
    """灰度标准差 < std_min 时做 LAB-CLAHE 对比度增强,否则原样返回。"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) >= std_min:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_chan = clahe.apply(l_chan)
    return cv2.cvtColor(cv2.merge([l_chan, a_chan, b_chan]), cv2.COLOR_LAB2BGR)


def preprocess_for_ocr(img_path: str):
    """前处理主入口:读图 → 纠偏(按需) → 放大(按需) → 对比度(按需)。

    返回 BGR ndarray;读图失败返回 None(调用方回退为把原路径直接给引擎,保持旧行为)。
    """
    bgr = load_image_bgr(img_path)
    if bgr is None:
        return None
    angle = estimate_skew_angle(bgr)
    if abs(angle) >= MIN_SKEW_ANGLE:
        bgr = deskew(bgr, angle)
    bgr = maybe_upscale(bgr)
    bgr = normalize_contrast(bgr)
    return bgr
