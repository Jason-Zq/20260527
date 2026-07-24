"""ocrapi 单元测试:JWT + 清洗 + 用户库 + 鉴权依赖(无外部服务依赖,需 PyJWT/bcrypt)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_ocrapi_auth.py
"""
import sys
import os
import asyncio
import tempfile
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import jwt
from fastapi.security import HTTPAuthorizationCredentials

from ocrapi import auth, config, sanitize, users_store


def _reset_users():
    """每个测试前重置为干净临时用户库(不污染真实 users.json)。"""
    users_store.set_path(tempfile.mktemp(suffix=".json"))


# ==================== sanitize ====================

def test_sanitize_removes_control_chars():
    assert sanitize.sanitize_text(None) is None
    assert sanitize.sanitize_text("") == ""
    raw = "abc\x00de\x01f\tg\nh\rf"
    out = sanitize.sanitize_text(raw)
    assert "\x00" not in out and "\x01" not in out
    assert "\t" in out and "\n" in out and "\r" in out
    assert out == "abcdef\tg\nh\rf"
    assert sanitize.sanitize_text("a\x7fb") == "ab"


# ==================== bcrypt + 用户库 ====================

def test_bcrypt_roundtrip():
    h = users_store._hash_password("secret123")
    assert h != "secret123"
    assert users_store._verify_password("secret123", h) is True
    assert users_store._verify_password("wrong", h) is False


def test_users_store_add_verify_disable():
    _reset_users()
    users_store.add_user("alice", "alice-pass", role="admin", notes="管理员")
    users_store.add_user("bob", "bob-pass", role="user")

    u = users_store.verify_user("alice", "alice-pass")
    assert u and u["username"] == "alice" and u["role"] == "admin"
    # 错误密码 / 不存在
    assert users_store.verify_user("alice", "wrong") is None
    assert users_store.verify_user("nobody", "x") is None
    # 停用后拒绝,启用后恢复
    users_store.set_enabled("bob", False)
    assert users_store.verify_user("bob", "bob-pass") is None
    users_store.set_enabled("bob", True)
    assert users_store.verify_user("bob", "bob-pass") is not None
    # list_users 不含哈希
    for u in users_store.list_users():
        assert "password_hash" not in u


# ==================== JWT 签发/解码(不查库) ====================

def test_token_roundtrip():
    token, expires = auth.create_access_token("alice", role="admin")
    assert expires > 0
    info = auth.decode_token(token)
    assert info["username"] == "alice"
    assert info["role"] == "admin"


def test_decode_expired():
    payload = {
        "sub": "carol", "role": "user",
        "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10),
    }
    expired = jwt.encode(payload, config.jwt_secret_key(), algorithm=config.jwt_algorithm())
    try:
        auth.decode_token(expired)
        assert False, "过期 token 应抛 401"
    except Exception as e:
        assert getattr(e, "status_code", None) == 401


def test_decode_invalid():
    try:
        auth.decode_token("not.a.valid.jwt")
        assert False, "无效 token 应抛 401"
    except Exception as e:
        assert getattr(e, "status_code", None) == 401


# ==================== 鉴权依赖(查库) ====================

async def _get_current_user(cred):
    return await auth.get_current_user(cred)


def test_get_current_user_valid_and_disabled():
    _reset_users()
    users_store.add_user("bob", "bob-pass", role="user")
    token, _ = auth.create_access_token("bob", role="user")
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    user = asyncio.run(_get_current_user(cred))
    assert user["username"] == "bob" and user["role"] == "user"

    # 停用后 JWT 仍在有效期,但 get_current_user 查库 -> 立即失效
    users_store.set_enabled("bob", False)
    try:
        asyncio.run(_get_current_user(cred))
        assert False, "停用后应 401"
    except Exception as e:
        assert getattr(e, "status_code", None) == 401


def test_get_current_user_missing():
    try:
        asyncio.run(_get_current_user(None))
        assert False, "缺 token 应抛 401"
    except Exception as e:
        assert getattr(e, "status_code", None) == 401


async def _require_admin(user):
    return await auth.require_admin(user)


def test_require_admin():
    asyncio.run(_require_admin({"username": "x", "role": "admin"}))  # admin 通过
    try:
        asyncio.run(_require_admin({"username": "x", "role": "user"}))
        assert False, "非 admin 应 403"
    except Exception as e:
        assert getattr(e, "status_code", None) == 403


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
