"""API 访问鉴权中间件。纯 ASGI(与 RequestLogMiddleware 同模式,不用 BaseHTTPMiddleware)。

统一 Bearer Token 鉴权:
- 前端(员工):账号密码登录(POST /api/auth/login 校验 admin_user/admin_password),
  通过后返回 biz_api_key 作为会话 token,前端带 Authorization: Bearer <biz_api_key>
- 业务方:直接用 biz_api_key 走 Bearer

凭证统一为 config.json.auth.biz_api_key,校验逻辑只有一套。
账号密码 / biz_api_key 都在 config.json.auth 段。

白名单(免鉴权):
- POST /api/auth/login    登录接口
- GET  /api/healthz       健康检查探活
- /docs, /redoc, /openapi, /swagger  文档
"""

import os
import json

# 配置缓存:_load_auth() 首次调用读 config.json,之后用缓存。
# main.py startup 会调 load_config 重置,使配置变更生效。
_AUTH_CACHE: dict | None = None

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config.json")


def _load_auth() -> dict:
    """从 config.json 读 auth 段。失败返回空 dict(开发环境放行)。"""
    global _AUTH_CACHE
    if _AUTH_CACHE is not None:
        return _AUTH_CACHE
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _AUTH_CACHE = (json.load(f) or {}).get("auth", {}) or {}
    except Exception:
        _AUTH_CACHE = {}
    return _AUTH_CACHE


def reset_auth_cache() -> None:
    """重置配置缓存(配置变更后由 main.py startup 调用)。"""
    global _AUTH_CACHE
    _AUTH_CACHE = None


def get_biz_api_key() -> str:
    return (_load_auth().get("biz_api_key") or "").strip()


def get_admin_credentials() -> tuple[str, str]:
    """返回 (admin_user, admin_password)。"""
    a = _load_auth()
    return (a.get("admin_user") or "").strip(), (a.get("admin_password") or "")


WHITELIST = [
    ("POST", "/api/auth/login"),
    ("GET", "/api/healthz"),
]

# 业务方集成接口(提交+轮询)按前缀放行:服务器上业务方不带 token,与历史行为一致。
# 覆盖 POST /api/archive-detect/business/batch(提交) 和 GET .../batch/{id}(轮询)。
WHITELIST_PREFIXES = ("/api/archive-detect/business/batch",)

DOC_PREFIXES = ("/docs", "/redoc", "/openapi", "/swagger")


def _is_whitelisted(method: str, path: str) -> bool:
    for m, p in WHITELIST:
        if (m == "*" or m == method) and path == p:
            return True
    if any(path == p or path.startswith(p + "/") for p in WHITELIST_PREFIXES):
        return True
    # 文档及 OpenAPI schema(/docs /redoc /openapi.json /swagger-...)放行
    if path in DOC_PREFIXES or path.startswith(tuple(x + "/" for x in DOC_PREFIXES)) \
            or path == "/openapi.json":
        return True
    return False


def _get_authorization(scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"authorization":
            return v.decode("latin-1", errors="replace")
    return None


def _is_valid_token(auth_header: str) -> bool:
    """校验 Bearer token 是否等于 biz_api_key。"""
    if not auth_header:
        return False
    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    token = parts[1].strip()
    biz_key = get_biz_api_key()
    return bool(biz_key and token == biz_key)


async def _send_401(send, message: str = "未授权"):
    body = json.dumps({"detail": message, "status": 401}).encode("utf-8")
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    """纯 ASGI 鉴权中间件。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "")

        if _is_whitelisted(method, path):
            return await self.app(scope, receive, send)

        # 未配置 biz_api_key:开发环境放行(避免本地起不来)
        if not get_biz_api_key():
            return await self.app(scope, receive, send)

        if not _is_valid_token(_get_authorization(scope)):
            return await _send_401(send, "未授权或凭证无效")

        return await self.app(scope, receive, send)
