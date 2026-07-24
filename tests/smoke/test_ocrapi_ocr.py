"""ocrapi 冒烟测试(依赖运行中的 ocrapi,默认 http://localhost:8001)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/smoke/test_ocrapi_ocr.py

环境变量:
  OCRAPI_BASE_URL   服务地址(默认 http://localhost:8001)
  OCRAPI_USER / OCRAPI_PASSWORD  登录凭证(默认读 ocrapi/config.json)
  OCR_TEST_URL      用于 OCR 测试的文件 URL(不设则跳过真实 OCR 调用)
"""
import sys
import os
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

BASE = os.getenv("OCRAPI_BASE_URL", "http://localhost:8001").rstrip("/")
USER = os.getenv("OCRAPI_USER", "admin")
PWD = os.getenv("OCRAPI_PASSWORD", "dev-pass-123")
TEST_URL = os.getenv("OCR_TEST_URL", "")


def test_healthz():
    r = httpx.get(f"{BASE}/healthz", timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"


def test_ocr_requires_token():
    r = httpx.post(f"{BASE}/ocr", json={"url": "https://example.com/x.png"}, timeout=10)
    assert r.status_code == 401, r.text


def test_token_wrong_password():
    r = httpx.post(
        f"{BASE}/token",
        json={"username": USER or "admin", "password": "wrong-password"},
        timeout=10,
    )
    assert r.status_code == 401, r.text


def test_ocr_full_flow():
    if not TEST_URL:
        print("  SKIP test_ocr_full_flow（未设置 OCR_TEST_URL）")
        return
    assert USER and PWD, "未配置登录凭证(OCRAPI_USER/PASSWORD 或 ocrapi/config.json)"
    # 1. 换 token
    r = httpx.post(f"{BASE}/token", json={"username": USER, "password": PWD}, timeout=10)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    # 2. OCR(大文件可能慢,给 5 分钟)
    r = httpx.post(
        f"{BASE}/ocr",
        json={"url": TEST_URL},
        headers={"Authorization": f"Bearer {token}"},
        timeout=300,
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    assert data["success"] is True
    assert "pages" in data and len(data["pages"]) >= 1
    assert "full_text" in data
    # 清洗后文本不含 NUL(避免下游 JSON 出错)
    assert "\x00" not in data["full_text"]


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
    else:
        print(f"\n全部通过 {len(tests)} 项")
