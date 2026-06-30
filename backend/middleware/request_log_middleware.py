"""API 请求记录中间件。只记录业务批次提交接口,fire-and-forget 写 DB。

实现细节:
- 使用纯 ASGI middleware(不用 starlette.BaseHTTPMiddleware)
  原因:BaseHTTPMiddleware 里 `await request.body()` 会消费 receive stream,导致下游
  Pydantic 拿不到 body 报 "There was an error parsing the body"。纯 ASGI 层可以缓冲
  body 后构造新 receive 喂给下游。

策略:
- 只记录 POST /api/archive-detect/business/batch
- application/json 请求: 解析 body 为 JSON 对象入库
- body > 64KB: 不读全 body,只记元数据(避免 OOM)
"""

import time
import asyncio
import json
from db import request_log_crud

LOG_ALLOWED_PATHS = {
    "/api/archive-detect/business/batch",
}
MAX_JSON_BODY_BYTES = 32768          # request_body 入库上限,超过截断为 _raw 字符串
MAX_READ_BODY_BYTES = 65536          # 内存上限,超过直接走 metadata,不读 body


def _parse_json_body(text: str) -> dict | None:
    """把 HTTP body 文本解析为可入库的 dict。失败时退化为 _raw 字符串。

    入库格式始终是 dict(JSONB 字段要求),所以基本类型/数组包一层。
    """
    if not text:
        return None
    if len(text) > MAX_JSON_BODY_BYTES:
        return {"_raw": text[:MAX_JSON_BODY_BYTES] + "...[truncated]"}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        return {"_array": obj}
    return {"_value": obj}


def _headers_get(headers_list, key: bytes) -> bytes | None:
    """从 ASGI scope['headers'](list of (bytes, bytes))中拿某个 header 值。"""
    for k, v in headers_list:
        if k == key:
            return v
    return None


def _decode_body(body_bytes: bytes, content_type: str) -> str:
    """按 Content-Type 的 charset 参数 + UTF-8 + GBK 三档兜底解码。

    避免业务方误用 GBK 客户端时整段乱码写入 DB。
    """
    if not body_bytes:
        return ""
    # 1) 显式 charset
    charset = None
    if "charset=" in content_type:
        try:
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"').strip("'")
        except (IndexError, ValueError):
            charset = None
    if charset:
        try:
            return body_bytes.decode(charset)
        except (UnicodeDecodeError, LookupError):
            pass
    # 2) UTF-8(严格)
    try:
        return body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # 3) GBK 兜底(中文 Windows 客户端常见)
    try:
        return body_bytes.decode("gbk")
    except UnicodeDecodeError:
        pass
    # 4) 实在不行 errors=replace,至少不抛
    return body_bytes.decode("utf-8", errors="replace")


class RequestLogMiddleware:
    """纯 ASGI middleware。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 只处理 HTTP 请求,websocket/lifespan 透传
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "")

        # 只记录业务批次提交接口,其他 archive-detect 请求全部透传
        if method != "POST" or path not in LOG_ALLOWED_PATHS:
            return await self.app(scope, receive, send)

        source = "business"

        headers = scope.get("headers", [])
        ct_bytes = _headers_get(headers, b"content-type") or b""
        ct = ct_bytes.decode("latin-1", errors="replace").lower()
        cl_bytes = _headers_get(headers, b"content-length")
        try:
            cl = int(cl_bytes.decode("latin-1")) if cl_bytes else None
        except (ValueError, AttributeError):
            cl = None

        # 是否需要缓冲 body
        capture_body = (
            "application/json" in ct
            and (cl is None or cl <= MAX_READ_BODY_BYTES)
        )

        body_captured: dict | None = None
        if "application/json" in ct and not capture_body:
            # JSON 但超大,不读,只记标记
            body_captured = {"_raw": f"[body too large: {cl} bytes, not captured]"}

        # 缓冲 body(如需)
        body_bytes = b""
        if capture_body:
            more_body = True
            while more_body:
                message = await receive()
                if message["type"] != "http.request":
                    # http.disconnect 等
                    more_body = False
                    break
                body_bytes += message.get("body", b"")
                more_body = message.get("more_body", False)
                # 二次保险,实际 body 超出
                if len(body_bytes) > MAX_READ_BODY_BYTES:
                    body_captured = {"_raw": f"[body too large: {len(body_bytes)} bytes, not captured]"}
                    capture_body = False  # 切换到 metadata
                    break

            if capture_body:
                # 优先 UTF-8;失败时按 charset 参数或 GBK 兜底,避免乱码污染 DB
                body_captured = _parse_json_body(_decode_body(body_bytes, ct))

            # 构造重放 receive
            _sent = False

            async def replay_receive():
                nonlocal _sent
                if not _sent:
                    _sent = True
                    return {"type": "http.request", "body": body_bytes, "more_body": False}
                # 后续读到 disconnect
                return {"type": "http.disconnect"}

            inner_receive = replay_receive
        else:
            inner_receive = receive

        # 拦截 response 拿状态码
        t0 = time.time()
        response_status: int | None = None

        async def wrapped_send(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status")
            await send(message)

        try:
            await self.app(scope, inner_receive, wrapped_send)
        finally:
            elapsed_ms = int((time.time() - t0) * 1000)
            client = scope.get("client")
            client_ip = client[0] if client else None

            # 构造 request_body
            if body_captured is not None:
                request_body = body_captured
            else:
                info: dict = {}
                qs = scope.get("query_string", b"")
                if qs:
                    from urllib.parse import parse_qs
                    parsed = parse_qs(qs.decode("latin-1", errors="replace"))
                    info["query"] = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
                if ct:
                    info["content_type"] = ct
                if cl is not None:
                    info["content_length_bytes"] = cl
                request_body = info or None

            try:
                asyncio.create_task(
                    request_log_crud.insert_request_log(
                        source=source,
                        method=method, path=path,
                        client_ip=client_ip, request_body=request_body,
                        response_status=response_status,
                        elapsed_ms=elapsed_ms,
                    )
                )
            except RuntimeError:
                pass


# 兼容旧 import:main.py 用 app.middleware("http")(request_log_middleware) 注册
# 改为 app.add_middleware(RequestLogMiddleware)
# 保留函数名当占位,实际注册改在 main.py
