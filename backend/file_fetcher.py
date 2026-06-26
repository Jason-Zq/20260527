"""
URL 文件下载工具（通用，可被任何接口复用）。

职责：
  - 从 http(s) URL 下载文件到 temp/，返回本地路径 + 文件名 + MIME
  - 大小上限 50 MB（用户决策）
  - 超时 60s 连接 / 5min 总下载
  - 不做 SSRF 防护（用户决策；公网 / 内网均允许）

返回错误：
  ValueError       - URL scheme 非法 / 文件名提取失败
  FileTooLargeError - 文件超过 50 MB
  httpx.HTTPError  - 下载层失败（网络/HTTP 状态码）
"""

import os
import re
import json
import time
import asyncio
import threading
import mimetypes
import uuid
from urllib.parse import urlparse, unquote
from typing import Tuple
import httpx


MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024   # 50 MB
DOWNLOAD_TIMEOUT_S = 300                 # 5 min 总超时
CONNECT_TIMEOUT_S = 60

# 与 OCR/解析支持的扩展名对齐
_SUPPORTED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif", ".docx", ".xls", ".xlsx", ".pptx"}


class FileTooLargeError(Exception):
    """文件超过 MAX_DOWNLOAD_BYTES。"""


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _temp_dir() -> str:
    d = os.path.normpath(os.path.join(_project_root(), "..", "temp", "fetched"))
    os.makedirs(d, exist_ok=True)
    return d


def _filename_from_response(resp: httpx.Response, url: str) -> str:
    """优先 Content-Disposition，回落 URL 末段。"""
    cd = resp.headers.get("content-disposition", "")
    # filename*=UTF-8''xxx
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1)).strip(' "')
    # filename="xxx"
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1)).strip()
    # URL 末段
    path = urlparse(url).path
    name = os.path.basename(unquote(path))
    return name or "downloaded"


def _sanitize_filename(name: str) -> str:
    """去掉路径分隔符等危险字符。"""
    # 只保留字母数字/中文/下划线/点/空格/横线
    safe = re.sub(r"[^\w一-龥.\- ]+", "_", name).strip(" .")
    return safe or "downloaded"


def _guess_mime(filename: str, content_type_header: str = None) -> str:
    """优先 Content-Type，回落后缀。返回小写 MIME 字符串。"""
    if content_type_header:
        # 去掉 charset 等参数
        ct = content_type_header.split(";")[0].strip().lower()
        if ct and ct != "application/octet-stream":
            return ct
    guess, _ = mimetypes.guess_type(filename)
    return (guess or "application/octet-stream").lower()


async def fetch_url_to_temp(url: str) -> Tuple[str, str, str]:
    """下载 URL 到 temp/fetched/，返回 (local_path, filename, mime_type)。

    抛出：
      ValueError                 - scheme 非法
      FileTooLargeError          - 超过 50 MB
      httpx.HTTPStatusError 等   - 下载层失败
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL 不能为空")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http/https URL（收到 scheme={parsed.scheme!r}）")

    timeout = httpx.Timeout(DOWNLOAD_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()

            # 早期 Content-Length 检查
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit():
                if int(content_length) > MAX_DOWNLOAD_BYTES:
                    raise FileTooLargeError(
                        f"文件 {int(content_length)/1024/1024:.1f} MB 超过 {MAX_DOWNLOAD_BYTES/1024/1024:.0f} MB 上限"
                    )

            # 推断文件名 + MIME
            raw_name = _filename_from_response(resp, url)
            filename = _sanitize_filename(raw_name)
            mime_type = _guess_mime(filename, resp.headers.get("content-type"))

            # 落盘到 temp/fetched/<uuid>_<filename>
            unique_id = uuid.uuid4().hex[:8]
            local_path = os.path.join(_temp_dir(), f"{unique_id}_{filename}")

            written = 0
            with open(local_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        # 下载到一半发现超限 → 删除半成品
                        f.close()
                        try:
                            os.remove(local_path)
                        except OSError:
                            pass
                        raise FileTooLargeError(
                            f"下载已超过 {MAX_DOWNLOAD_BYTES/1024/1024:.0f} MB 上限，已中止"
                        )
                    f.write(chunk)

    return local_path, filename, mime_type


def _load_config() -> dict:
    """轻量读取根目录 config.json，避免 file_fetcher 反向依赖 llm_service。"""
    config_path = os.path.normpath(os.path.join(_project_root(), "..", "config.json"))
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        print(f"[file_fetcher] 读取 config.json 失败: {e}")
        return {}


def _is_expired_url_error(exc: Exception) -> bool:
    """仅对明确的签名/地址失效状态码做刷新：401/403/404。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (401, 403, 404)
    return False


async def refresh_download_url(file_id: str, type_: str = None) -> tuple[str, dict]:
    """用业务方 file_id 获取新的 OSS 临时下载地址。

    返回 (file_url, data)。
    """
    if not file_id:
        raise ValueError("刷新下载地址需要 file_id")

    cfg = (_load_config().get("file_url_service") or {})
    if not cfg.get("enabled", False):
        raise ValueError("未启用 file_url_service")
    base_url = (cfg.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("file_url_service.base_url 未配置")

    type_value = type_ or cfg.get("default_type") or "preview"
    timeout_sec = int(cfg.get("timeout_sec") or 20)
    timeout = httpx.Timeout(timeout_sec, connect=min(timeout_sec, 10))

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(base_url, params={"file_id": file_id, "type": type_value})
        resp.raise_for_status()
        payload = resp.json()

    if payload.get("ret") != 200 or payload.get("code") != 0:
        raise ValueError(f"刷新下载地址失败: {payload.get('msg') or payload}")
    data = payload.get("data") or {}
    file_url = data.get("file_url")
    if not file_url:
        raise ValueError("刷新下载地址响应缺少 data.file_url")
    return file_url, data


async def fetch_url_to_temp_with_refresh(
    url: str,
    file_id: str = None,
    type_: str = None,
) -> tuple[str, str, str, dict | None]:
    """先下载原 URL；若 401/403/404 且有 file_id，则刷新 URL 后重试一次。

    返回 (local_path, filename, mime_type, refresh_info)。
    refresh_info=None 表示未刷新。
    """
    try:
        local_path, filename, mime_type = await fetch_url_to_temp(url)
        return local_path, filename, mime_type, None
    except Exception as first_err:
        if not file_id or not _is_expired_url_error(first_err):
            raise
        new_url, data = await refresh_download_url(file_id, type_)
        local_path, filename, mime_type = await fetch_url_to_temp(new_url)
        return local_path, filename, mime_type, {
            "old_url": url,
            "new_url": new_url,
            "data": data,
        }


def is_supported_extension(filename: str) -> bool:
    """快速判断扩展名是否在支持列表里。"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in _SUPPORTED_EXT


def get_unsupported_hint(filename: str) -> str:
    """针对常见但暂不支持的旧格式给出明确提示。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".doc":
        return "暂不支持旧版 Word(.doc)，请转换为 .docx 后上传"
    return f"不支持的文件类型：{filename}（支持 {', '.join(get_supported_extensions())}）"


def get_supported_extensions() -> list[str]:
    return sorted(_SUPPORTED_EXT)


def cleanup_temp_file(local_path: str) -> None:
    """安全删除单个临时文件。

    Windows 上 pdfplumber/PaddleOCR 句柄释放有延迟，立即删常报 WinError 32。
    策略：立即试 → 失败 sleep 0.5s 重试一次 → 仍失败丢进延迟队列由后台清理。
    """
    if not local_path or not os.path.exists(local_path):
        return
    try:
        os.remove(local_path)
        return
    except OSError:
        pass
    # 短暂等待 GC 释放句柄后再试一次（覆盖大部分场景）
    time.sleep(0.5)
    try:
        os.remove(local_path)
        return
    except OSError as e:
        # 进入延迟队列，由 _periodic_cleanup_task 兜底
        with _cleanup_lock:
            _pending_cleanup.append((local_path, time.time() + 30))
        print(f"[file_fetcher] 临时文件占用中，已加入延迟清理队列: {local_path} ({e})")


# ==================== 延迟清理 + 启动时旧文件扫描 ====================

# 模块级延迟队列：[(path, retry_after_ts)]
_pending_cleanup: list[tuple[str, float]] = []
_cleanup_lock = threading.Lock()

# 启动时清理多久前的旧文件（兜底之前残留的）
_STALE_FILE_MAX_AGE_SEC = 3600  # 1 小时
# 后台任务扫描间隔
_CLEANUP_INTERVAL_SEC = 60


def _drain_pending_cleanup() -> None:
    """清空一次延迟队列里能删的，删不动的留到下次。"""
    now = time.time()
    with _cleanup_lock:
        remaining: list[tuple[str, float]] = []
        for path, ts in _pending_cleanup:
            if ts > now:
                # 还没到重试时间
                remaining.append((path, ts))
                continue
            if not os.path.exists(path):
                continue  # 已经被别处删掉
            try:
                os.remove(path)
            except OSError:
                # 继续等 60s 后再试
                remaining.append((path, now + _CLEANUP_INTERVAL_SEC))
        _pending_cleanup[:] = remaining


def _cleanup_stale_files(max_age_sec: int = _STALE_FILE_MAX_AGE_SEC) -> int:
    """扫描 temp/fetched/ 目录，删 max_age_sec 以前的残留文件。返回成功删除的个数。"""
    d = _temp_dir()
    now = time.time()
    deleted = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        p = os.path.join(d, name)
        try:
            if os.path.isfile(p) and now - os.path.getmtime(p) > max_age_sec:
                os.remove(p)
                deleted += 1
        except OSError:
            pass
    return deleted


async def periodic_cleanup_task() -> None:
    """后台周期任务：启动时扫一次旧文件 + 之后每 60s 处理延迟队列。

    由 main.py 的 startup 事件挂起 asyncio.create_task(periodic_cleanup_task())。
    """
    try:
        n = _cleanup_stale_files()
        if n > 0:
            print(f"[file_fetcher] 启动清理：删除 {n} 个超过 {_STALE_FILE_MAX_AGE_SEC}s 的残留临时文件")
    except Exception as e:
        print(f"[file_fetcher] 启动清理异常（忽略）: {e}")

    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL_SEC)
            _drain_pending_cleanup()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[file_fetcher] 后台清理异常（继续运行）: {e}")
