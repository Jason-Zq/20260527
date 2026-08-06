"""
FastAPI 后端入口
智能文档审核工作台 API 服务
"""

import os
import mimetypes

# 进程级 OpenMP/MKL 线程上限,务必在 import paddlepaddle/numpy/opencv 之前设置
# 默认会拉满所有核心,每线程一份临时 buffer,在小内存机器上反而拖垮内存。
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")

import json
import asyncio
import time
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Form, Path, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional

import llm_service
import ocr_service
import split_ocr_service
import split_service
import template_service
from db.engine import init_db
from db import crud
from db import template_crud
from db import split_crud
from db import family_crud
from db import assets_crud
from db import summary_crud
from db import sales_crud

import client_profile_service
import file_fetcher
import text_extractor
import archive_detect_service
import event_service
from db import archive_detect_crud

# Bearer Token 鉴权声明(仅用于 OpenAPI 文档显示 Authorize 按钮 + docs 调用时带头)。
# auto_error=False:不在此真正校验,真正拦截由 AuthMiddleware(ASGI)统一做,避免两套逻辑。
_bearer_scheme = HTTPBearer(auto_error=False)


async def _bearer_dep(cred: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme)):
    """全局依赖:仅为在 OpenAPI 里声明 security scheme,不校验(交给中间件)。"""
    return cred


app = FastAPI(
    title="智能文档审核工作台",
    version="1.0.0",
    dependencies=[Depends(_bearer_dep)],
)

# 跨域配置
# CORS 配置:
# - 默认开发模式 allow_origins=["*"] + credentials=False(浏览器实际会忽略 credentials,符合规范)
# - 生产同源部署时不需要 CORS,这里保持 *  但 credentials=False 不会有副作用
# - 如确需 cookie 跨站,设 CORS_ALLOW_ORIGINS=https://app.example.com,https://...
_cors_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_origins_env == "*" else [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=(_cors_origins != ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 外部请求日志中间件(纯 ASGI,可安全读 body 并重放给下游)
from middleware.request_log_middleware import RequestLogMiddleware
app.add_middleware(RequestLogMiddleware)

# 鉴权中间件(纯 ASGI,统一 Bearer Token 校验)。
# Starlette 中间件后注册先执行:Auth 后注册 = 比 RequestLog 先执行,
# 未授权请求直接 401,不会被外部请求日志中间件入库。
from middleware.auth_middleware import AuthMiddleware
app.add_middleware(AuthMiddleware)

# 输出目录（统一用 output/）
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 静态文件：提供图片访问（URL 路径保持 /uploads/ 不变，前端无需改动）
app.mount("/uploads", StaticFiles(directory=OUTPUT_DIR), name="uploads")

# 内存中的任务状态缓存（仅用于轮询进度，不再作为持久存储）
_task_status = {}   # {task_id: {"status": "ocr|llm|done|error", "progress": "", "error": ""}}
_task_results = {}
_task_results_ts: dict[str, float] = {}   # 写入/命中时间戳,用于 TTL GC

# PDF 拆分流水线的独立状态字典(与解析流水线隔离)
# {task_id: {"status": "ocr|llm|splitting|done|error", "progress": "", "error": "", "result": dict|None}}
_split_task_status = {}

# 终态任务的内存 TTL(达到 done/error 起算),超时由 _inmem_ttl_gc 清理
# 数据落在 DB,清掉内存条目不影响轮询正确性,仅会让前端少量请求走 DB fallback
INMEM_TASK_TTL_SECONDS = 6 * 3600


def _stamp_result(task_id: str, result_data) -> None:
    """写入 _task_results 时同步打时间戳,供 _inmem_ttl_gc 用。所有写入位点都应走这里。"""
    _task_results[task_id] = result_data
    _task_results_ts[task_id] = time.time()


# healthz 中 DB 错误事件的限频时间戳(每 5 分钟最多记一次,避免事件表被探针写爆)
_last_db_error_event_ts: float = 0.0


# ==================== 上传工具 ====================

# 单文件上传大小上限,与 file_fetcher.MAX_DOWNLOAD_BYTES 保持一致(50MB)。
UPLOAD_MAX_BYTES = 50 * 1024 * 1024
_UPLOAD_CHUNK_SIZE = 64 * 1024  # 64KB 流式块

# 业务方批量上传的全局并发上限。multipart upload 接收阶段会持有连接 + 写盘,
# 10 个业务方同时打过来会瞬时 IO 洪水。这里串行化"接收"阶段,
# 真正的 OCR 处理仍在文件级队列异步消化。
# 超过此并发数时立即返回 429,业务方应当退避重试。
UPLOAD_CONCURRENCY = int(os.getenv("UPLOAD_CONCURRENCY", "1"))
_UPLOAD_SEMAPHORE: asyncio.Semaphore | None = None   # startup 时初始化(必须绑事件循环)


async def save_upload_stream(
    upload_file: UploadFile,
    dest_path: str,
    max_bytes: int = UPLOAD_MAX_BYTES,
) -> int:
    """把 UploadFile 流式写入 dest_path,边读边写边累加大小,超阈值立即 413。

    传统的 `content = await file.read(); f.write(content)` 把整个文件读进 Python 堆;
    50 文件 × 50MB 一次性 ≈ 2.5GB,小内存机器直接 OOM。改用 64KB chunk 流式,堆内存常驻仅几十 KB。

    超阈值时:已写入的临时文件会被删除,抛 HTTPException(413)。
    返回:实际写入字节数。
    """
    total = 0
    with open(dest_path, "wb") as out:
        while True:
            chunk = await upload_file.read(_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                out.close()
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过 {max_bytes // (1024 * 1024)} MB 上限",
                )
            out.write(chunk)
    return total

class ReviewPayload(BaseModel):
    """人工复核提交的数据"""
    task_id: str
    fields: dict
    doc_type: Optional[str] = None


class ClientProfileSourceFileItem(BaseModel):
    """客户档案生成候选文件。"""
    id: int = Field(..., description="archive_detect_files.id")
    filename: Optional[str] = Field(None, description="文件名")
    doc_category: Optional[str] = Field(None, description="文件分类")
    progress_name: Optional[str] = Field(None, description="进展名称")
    progress_oid: Optional[str] = Field(None, description="进展 OID")
    status: str = Field(..., description="文件状态")
    char_count: Optional[int] = Field(None, description="OCR 字符数")
    has_ocr_text: bool = Field(..., description="是否有 OCR 文本")
    selectable: bool = Field(..., description="是否可选择用于生成")


class ClientProfileSourceFilesResponse(BaseModel):
    items: list[ClientProfileSourceFileItem] = Field(..., description="候选文件列表")
    total: int = Field(..., description="候选文件数量")


class ClientProfileGeneratePayload(BaseModel):
    source_file_ids: list[int] = Field(..., description="用于生成客户档案的 archive_detect_files.id 数组")


class ClientProfileGenerateResponse(BaseModel):
    task_id: int = Field(..., description="生成任务 ID")
    client_id: int = Field(..., description="客户 ID")
    source_file_count: int = Field(..., description="本次使用文件数量")
    status: str = Field(..., description="任务状态")


class ClientProfileTaskResponse(BaseModel):
    task_id: int = Field(..., description="生成任务 ID")
    id: int = Field(..., description="生成任务 ID")
    client_id: int = Field(..., description="客户 ID")
    status: str = Field(..., description="任务状态 running/done/error")
    source_file_ids: list[int] = Field(default_factory=list, description="本次使用的文件 ID")
    source_files_snapshot: list[dict] = Field(default_factory=list, description="本次使用文件摘要")
    source_file_count: int = Field(..., description="使用文件数量")
    extracted_summary: dict = Field(default_factory=dict, description="AI 抽取汇总")
    created_count: dict = Field(default_factory=dict, description="写入数量统计")
    error: Optional[str] = Field(None, description="错误信息")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")


class ClientProfileTaskListResponse(BaseModel):
    items: list[ClientProfileTaskResponse] = Field(..., description="生成任务列表")


class ChildAgeLeadItem(BaseModel):
    """子女年龄线索列表项。"""
    client_id: int = Field(..., description="客户 ID")
    client_code: Optional[str] = Field(None, description="客户编码")
    client_name: str = Field(..., description="客户姓名")
    child_id: int = Field(..., description="子女家庭成员记录 ID")
    child_name: str = Field(..., description="子女姓名")
    relation: str = Field(..., description="关系字段,如 child/子女/儿子/女儿")
    birth_date: Optional[str] = Field(None, description="子女出生日期,YYYY-MM-DD")
    age_years: Optional[int] = Field(None, description="当前周岁")
    age_months: Optional[int] = Field(None, description="当前年龄总月数")
    age_text: str = Field(..., description="年龄展示文本,如 7岁3个月")


class ChildAgeLeadListResponse(BaseModel):
    """子女年龄线索列表返回。"""
    items: list[ChildAgeLeadItem] = Field(..., description="子女年龄线索数组")
    total: int = Field(..., description="符合筛选条件的总条数")


@app.on_event("startup")
async def startup():
    """启动时加载配置并初始化数据库连接池"""
    llm_service.load_config()
    # 重置鉴权配置缓存,使 config.json.auth 改动在重启后生效
    from middleware.auth_middleware import reset_auth_cache
    reset_auth_cache()
    await init_db()
    # 确保模板存储目录存在
    os.makedirs(os.path.join(OUTPUT_DIR, "templates"), exist_ok=True)
    # 清理过期的临时模板文件（未保存就关 dialog 留下的）
    await asyncio.to_thread(_cleanup_stale_template_temp, 60)
    # 清理过期的任务目录（>30天的 OCR 图片和拆分 PDF）
    await asyncio.to_thread(_cleanup_expired_output, 30)
    # 启动拆分任务 7 天 TTL 周期清理(后台 task,每 24h 跑一次)
    asyncio.create_task(_split_cleanup_loop())
    # 启动留底检测内存态 GC(无 DB 版本,6 小时 TTL,每 30 分钟扫一次)
    asyncio.create_task(archive_detect_service.gc_loop())
    # 启动 temp/fetched/ 临时文件兜底清理(Windows 句柄延迟释放导致的残留)
    asyncio.create_task(file_fetcher.periodic_cleanup_task())
    # 启动业务审核 watchdog + 恢复中断的 finalize 协程(方案二 2b: Worker 是独立进程)
    # OCR Worker 进程通过 systemd doc-review-worker@{1,2,3} 独立启动,主进程不再托管
    await archive_detect_service.start_background_tasks()
    # 初始化上传并发信号量(必须在 startup 内,绑当前 event loop)
    global _UPLOAD_SEMAPHORE
    _UPLOAD_SEMAPHORE = asyncio.Semaphore(UPLOAD_CONCURRENCY)
    print(f"配置已加载，数据库已连接，服务启动完成(upload 并发上限={UPLOAD_CONCURRENCY})")
    event_service.log_event(
        event_service.INFO,
        event_service.CATEGORY_SERVICE_START,
        "API 服务启动完成",
        context={
            "queue_max": archive_detect_service.QUEUE_MAX_SIZE,
            "upload_concurrency": UPLOAD_CONCURRENCY,
        },
    )


@app.on_event("shutdown")
async def shutdown():
    """优雅关停:停止 worker,关闭长连接 httpx client。"""
    event_service.log_event(
        event_service.INFO,
        event_service.CATEGORY_SERVICE_STOP,
        "API 服务关停",
    )
    try:
        await archive_detect_service.stop_background_tasks()
    except Exception as e:
        print(f"[shutdown] stop_background_tasks 失败(忽略): {e}")
    try:
        await file_fetcher.close_http_client()
    except Exception as e:
        print(f"[shutdown] close_http_client 失败(忽略): {e}")

def _cleanup_stale_template_temp(max_age_minutes: int = 60) -> None:
    """删除 temp/templates/ 和 output/templates/_parse/ 下早于阈值的临时文件/目录。

    parse 阶段产物：
      - temp/templates/{token}.docx  → 上传后未保存的 docx 副本
      - output/templates/_parse/{token}/  → 同 token 的 Word 原貌预览 PNG
    保存成功的会被 save_template 显式清掉；未保存就关 dialog / 刷新 / 断网的留垃圾，
    靠本函数在每次启动时回收。
    """
    import shutil
    import time

    cutoff = time.time() - max_age_minutes * 60
    cleaned = 0

    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp", "templates")
    if os.path.isdir(temp_dir):
        for name in os.listdir(temp_dir):
            full = os.path.join(temp_dir, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    if os.path.isdir(full):
                        shutil.rmtree(full, ignore_errors=True)
                    else:
                        os.remove(full)
                    cleaned += 1
            except OSError as e:
                print(f"[cleanup] 删除 {full} 失败: {e}")

    parse_dir = os.path.join(OUTPUT_DIR, "templates", "_parse")
    if os.path.isdir(parse_dir):
        for name in os.listdir(parse_dir):
            full = os.path.join(parse_dir, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    shutil.rmtree(full, ignore_errors=True)
                    cleaned += 1
            except OSError as e:
                print(f"[cleanup] 删除 {full} 失败: {e}")

    if cleaned:
        print(f"[cleanup] 清理了 {cleaned} 项过期临时模板文件")


def _cleanup_expired_output(max_age_days: int = 30) -> None:
    """删除 output/ 下超过指定天数的任务目录（OCR 图片、拆分 PDF 等）。

    数据库中的 ocr_text 和 extracted_fields 不受影响（已独立存储）。
    仅删除磁盘上的图片和 PDF 文件，释放存储空间。
    """
    import shutil
    import time
    import re

    cutoff = time.time() - max_age_days * 86400
    cleaned = 0
    # 匹配 YYMMDDHHmmss_ 开头的任务目录
    task_dir_re = re.compile(r"^\d{12}_")

    if not os.path.isdir(OUTPUT_DIR):
        return

    for name in os.listdir(OUTPUT_DIR):
        # 跳过 templates 目录和非任务目录
        if name == "templates" or not task_dir_re.match(name):
            continue
        full = os.path.join(OUTPUT_DIR, name)
        if not os.path.isdir(full):
            continue
        try:
            if os.path.getmtime(full) < cutoff:
                shutil.rmtree(full, ignore_errors=True)
                cleaned += 1
        except OSError as e:
            print(f"[cleanup] 删除 {full} 失败: {e}")

    if cleaned:
        print(f"[cleanup] 清理了 {cleaned} 个过期任务目录（>{max_age_days}天）")


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), client_id: Optional[int] = Form(None)):
    """
    上传 PDF/图片文件，立即返回 task_id，后台异步处理。
    前端通过 GET /api/result/{task_id} 轮询进度和结果。

    可选 client_id：批量队列模式下前端预先选了客户，解析完成后会自动归档到该客户档案，
    跳过人工复核步骤（A1 优化点）。未传 client_id 时维持原"复核 → 归档"路径。
    """
    # 生成 task_id: YYMMDDHHmmss_原文件名（去扩展名、去空格）
    timestamp = datetime.now().strftime("%y%m%d%H%M%S")
    stem = os.path.splitext(file.filename or "unknown")[0].replace(" ", "")
    task_id = f"{timestamp}_{stem}"

    # 如果同名已存在，追加序号避免冲突
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    if os.path.exists(task_dir):
        n = 2
        while os.path.exists(os.path.join(OUTPUT_DIR, f"{timestamp}_{stem}_{n}")):
            n += 1
        task_id = f"{timestamp}_{stem}_{n}"

    # 保存上传文件到临时位置（流式写入,避免整块进内存）
    ext = os.path.splitext(file.filename or "")[1] or ".pdf"
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{task_id}{ext}")
    await save_upload_stream(file, temp_path)

    # 初始化状态（内存）
    _task_status[task_id] = {"status": "ocr", "progress": "0页", "error": ""}

    # 写入数据库：创建 document 记录
    await crud.create_document(task_id=task_id, filename=file.filename or "", status="ocr")

    # 启动后台异步处理（A1：透传 client_id，解析完成后自动归档）
    asyncio.create_task(_process_file_background(task_id, temp_path, file.filename or "", client_id))

    return {"task_id": task_id, "status": "processing"}

async def _process_file_background(task_id: str, file_path: str, filename: str, client_id: Optional[int] = None):
    """后台异步处理文件：OCR → LLM → 保存结果到 output/{task_id}/ 和数据库"""
    try:
        # Step 1: OCR 识别（在线程池中执行，避免阻塞事件循环）
        _task_status[task_id] = {"status": "ocr", "progress": "识别中...", "error": ""}
        print(f"[{task_id}] 开始 OCR 识别: {filename}")
        max_ocr_pages = llm_service.CONFIG.get("max_ocr_pages", 0)
        ocr_results = await asyncio.to_thread(ocr_service.process_file, file_path, task_id, max_ocr_pages)

        ocr_texts = [page["text"] for page in ocr_results]

        # 收集图片和OCR详情
        images = []
        all_ocr_details = []
        for page in ocr_results:
            if page.get("image"):
                images.append(page["image"])
            all_ocr_details.extend(page.get("ocr_details", []))

        # Step 2: LLM 合并调用（类型检测+结构化提取，支持多证件）
        _task_status[task_id] = {"status": "llm", "progress": "分析中...", "error": ""}
        print(f"[{task_id}] 调用大模型分析...")
        llm_result = await asyncio.to_thread(llm_service.detect_and_extract, ocr_texts, task_id=task_id)

        # 处理多证件 items
        items = llm_result.get("items", [])
        for item in items:
            # Step 3: 匹配坐标框
            item["fields"] = llm_service.match_bboxes_to_fields(item.get("fields", {}), all_ocr_details)
            # Step 4: 计算统计
            item["stats"] = _calc_stats(item.get("fields", {}))

        # 保存结果（文件系统）
        result_data = {
            "task_id": task_id,
            "filename": filename,
            "items": items,
            "images": images,
            "ocr_texts": ocr_texts
        }

        # 写入数据库：更新 document 记录（OCR 文本和解析结果已存 DB，不再写磁盘文件）
        ocr_full_text = "\n".join(ocr_texts)
        doc_type = items[0].get("doc_type") if items else None
        # 计算平均置信度
        all_conf = []
        for item in items:
            for field_info in item.get("fields", {}).values():
                if isinstance(field_info, dict):
                    all_conf.append(field_info.get("confidence", 0.5))
        confidence_avg = round(sum(all_conf) / len(all_conf), 4) if all_conf else None

        await crud.update_document_result(
            task_id=task_id,
            items=items,
            ocr_text=ocr_full_text,
            doc_type=doc_type,
            confidence_avg=confidence_avg,
            file_path=f"output/{task_id}",
        )

        _stamp_result(task_id, result_data)
        _task_status[task_id] = {"status": "done", "progress": "", "error": "", "_finished_ts": time.time()}
        print(f"[{task_id}] 处理完成: {len(items)} 张证件")

        # A1 优化：批量队列模式带 client_id 上传时，跳过复核直接归档到指定客户
        if client_id is not None and items:
            try:
                written = await crud.archive_to_specific_client(task_id, client_id, items)
                if written > 0:
                    print(f"[{task_id}] 已自动归档到客户 ID={client_id}（{written} 项）")
                else:
                    print(f"[{task_id}] 自动归档跳过（客户不存在或无字段）")
            except Exception as e:
                print(f"[{task_id}] 自动归档失败（不影响解析）: {e}")

    except Exception as e:
        print(f"[{task_id}] 处理失败: {e}")
        _task_status[task_id] = {"status": "error", "progress": "", "error": str(e), "_finished_ts": time.time()}
        # 更新数据库状态为 error
        await crud.update_document_status(task_id, "error", str(e))
    finally:
        # 清理临时文件
        if os.path.exists(file_path):
            os.remove(file_path)


def _build_result_from_db(doc) -> dict:
    """从数据库记录构建前端需要的结果格式（与原 JSON 文件格式一致）"""
    import glob
    task_id = doc.task_id
    # 扫描 output/{task_id}/images/ 下的图片文件
    images_dir = os.path.join(OUTPUT_DIR, task_id, "images")
    images = []
    if os.path.isdir(images_dir):
        for f in sorted(os.listdir(images_dir)):
            if f.lower().endswith((".png", ".jpg", ".jpeg")):
                images.append(f"{task_id}/images/{f}")

    # 从 extracted_fields 还原 items
    items = doc.extracted_fields or []

    # 从 ocr_text 还原 ocr_texts（按页分割）
    ocr_texts = []
    if doc.ocr_text:
        # 按 "=== 第 N 页 ===" 分割
        import re
        parts = re.split(r"=== 第 \d+ 页 ===\n?", doc.ocr_text)
        ocr_texts = [p.strip() for p in parts if p.strip()]

    return {
        "task_id": task_id,
        "filename": doc.filename,
        "items": items,
        "images": images,
        "ocr_texts": ocr_texts
    }


@app.get("/api/result/{task_id}")
async def get_result(task_id: str):
    """获取解析任务的进度或结果（前端轮询接口）。

    路径参数:
        task_id - 上传时返回的任务 ID

    返回:
        - 处理中: {"task_id", "status": "ocr"|"llm", "progress": "...", "error": ""}
        - 完成:  {"task_id", "status": "done", "filename", "items":[...],
                 "images":[...], "ocr_texts":[...]}
        - 失败:  {"task_id", "status": "error", "error": "..."}

    数据源优先级:
        1. 内存 _task_status / _task_results 缓存
        2. 数据库 documents 表（进程重启后回落）

    错误:
        404 - 任务不存在
    """
    status = _task_status.get(task_id)
    if not status:
        # 内存中没有，尝试从数据库查
        doc = await crud.get_document(task_id)
        if doc and doc.status == "done":
            # 从数据库加载完整结果
            data = _build_result_from_db(doc)
            data["status"] = "done"
            _stamp_result(task_id, data)
            return data
        if doc:
            return {
                "task_id": task_id,
                "status": doc.status,
                "progress": "",
                "error": doc.error_msg or ""
            }
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    # 未完成：返回进度
    if status["status"] != "done":
        return {
            "task_id": task_id,
            "status": status["status"],
            "progress": status["progress"],
            "error": status.get("error", "")
        }

    # 已完成：返回完整结果
    if task_id in _task_results:
        result = _task_results[task_id]
        result["status"] = "done"
        return result

    # 从数据库加载
    doc = await crud.get_document(task_id)
    if doc and doc.status == "done":
        data = _build_result_from_db(doc)
        data["status"] = "done"
        _stamp_result(task_id, data)
        return data

    raise HTTPException(status_code=404, detail=f"任务 {task_id} 结果不存在")

@app.put("/api/result/{task_id}")
async def save_review(task_id: str, payload: dict):
    """保存人工复核修正结果。

    新增 `archive` 字段（可选）支持新归档路径：
      payload = {
        "items": [...],
        "archive": {
          "client_id": 1,
          "entity": "clients" | "family" | "assets",
          "target_id": null | int,        # family/assets 时给定则更新该行；null=新建
          "sub_meta": {"relation": "配偶"} 或 {"asset_type": "房产"}
        }
      }
    不带 archive 字段时退化到旧的 archive_to_client_info 行为（兼容）。
    """
    # 从内存缓存或数据库加载
    if task_id in _task_results:
        result_data = _task_results[task_id]
    else:
        doc = await crud.get_document(task_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
        result_data = _build_result_from_db(doc)

    # 更新 items 中的字段
    archive_result = None
    if "items" in payload:
        result_data["items"] = payload["items"]
        # 重新计算每个item的统计
        for item in result_data["items"]:
            item["stats"] = _calc_stats(item.get("fields", {}))
        result_data["reviewed"] = True

    _stamp_result(task_id, result_data)

    # 同步更新数据库
    if "items" in payload:
        await crud.update_document_review(task_id, payload["items"])

        archive_payload = payload.get("archive")
        if archive_payload and isinstance(archive_payload, dict) and archive_payload.get("client_id"):
            # 新路径：精准归档
            try:
                archive_result = await crud.archive_document(
                    task_id=task_id,
                    client_id=int(archive_payload["client_id"]),
                    entity=archive_payload.get("entity", "clients"),
                    target_id=archive_payload.get("target_id"),
                    sub_meta=archive_payload.get("sub_meta"),
                    items=payload["items"],
                )
                print(f"[{task_id}] 归档完成 entity={archive_result['entity']} mapped={archive_result['mapped_count']} unmapped={archive_result['unmapped_count']}")
            except Exception as e:
                print(f"[{task_id}] 归档失败: {e}")
                raise HTTPException(status_code=500, detail=f"归档失败: {e}")
        else:
            # 旧路径：保持兼容
            try:
                client_id = await crud.archive_to_client_info(task_id, payload["items"])
                if client_id:
                    print(f"[{task_id}] 已归档到客户 ID={client_id}（旧路径）")
            except Exception as e:
                print(f"[{task_id}] 归档失败（不影响保存）: {e}")

    response = {"message": "复核结果已保存", "task_id": task_id}
    if archive_result:
        response["archive"] = archive_result
    return response

@app.get("/api/history")
async def get_history():
    """获取所有解析任务历史记录摘要列表（从 documents 表查询）。

    返回:
        {"history": [{task_id, filename, doc_types, field_count,
                      reviewed, status, created_at}, ...]}
        默认最多 100 条，按创建时间倒序。
    """
    history = await crud.list_documents(limit=100, offset=0)
    return {"history": history}

@app.get("/api/search")
async def search(keyword: str = Query(..., min_length=1, description="搜索关键词")):
    """全文模糊搜索 documents 表。

    Query:
        keyword - 关键词（必填，长度 >= 1）

    搜索范围:
        filename / doc_type / ocr_text / extracted_fields(JSONB cast text)

    返回:
        {"keyword": "...", "results": [...], "total": N}
        最多 50 条，按相关度+时间排序。
    """
    results = await crud.search_documents(keyword.strip(), limit=50)
    return {"keyword": keyword.strip(), "results": results, "total": len(results)}


@app.get(
    "/api/sales/child-age-leads",
    tags=["销售线索"],
    summary="子女年龄线索列表",
    response_model=ChildAgeLeadListResponse,
)
async def sales_child_age_leads(
    keyword: Optional[str] = Query(None, description="客户姓名/客户编码/子女姓名模糊查询"),
    min_age: Optional[int] = Query(None, ge=0, description="最小年龄(周岁)"),
    max_age: Optional[int] = Query(None, ge=0, description="最大年龄(周岁)"),
    limit: int = Query(100, ge=1, le=500, description="返回条数,1-500"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """销售顾问查看客户子女年龄列表。"""
    return await sales_crud.list_child_age_leads(
        keyword=keyword,
        min_age=min_age,
        max_age=max_age,
        limit=limit,
        offset=offset,
    )


# ==================== 客户资料结构化生成 ====================

@app.get(
    "/api/client-profile/source-files/{client_id}",
    tags=["客户档案生成"],
    summary="客户档案生成 - 查询候选 OCR 文件",
    response_model=ClientProfileSourceFilesResponse,
)
async def client_profile_source_files(
    client_id: int = Path(..., description="客户 ID"),
):
    return await client_profile_service.list_source_files(client_id)


@app.post(
    "/api/client-profile/generate/{client_id}",
    tags=["客户档案生成"],
    summary="客户档案生成 - 创建生成任务",
    response_model=ClientProfileGenerateResponse,
)
async def client_profile_generate(
    payload: ClientProfileGeneratePayload,
    client_id: int = Path(..., description="客户 ID"),
):
    try:
        return await client_profile_service.submit_generate_profile(
            client_id,
            source_file_ids=payload.source_file_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/api/client-profile/generate/{task_id}",
    tags=["客户档案生成"],
    summary="客户档案生成 - 查询任务状态",
    response_model=ClientProfileTaskResponse,
)
async def client_profile_generate_status(
    task_id: int = Path(..., description="生成任务 ID"),
):
    data = await client_profile_service.get_generation_task(task_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"生成任务 {task_id} 不存在")
    return data


@app.get(
    "/api/client-profile/generate/list/{client_id}",
    tags=["客户档案生成"],
    summary="客户档案生成 - 查询客户生成记录",
    response_model=ClientProfileTaskListResponse,
)
async def client_profile_generate_list(
    client_id: int = Path(..., description="客户 ID"),
    limit: int = Query(20, ge=1, le=100, description="返回最近多少条生成记录"),
):
    return {"items": await client_profile_service.list_generation_tasks(client_id, limit=limit)}


@app.get("/api/clients")
async def get_clients(
    keyword: Optional[str] = Query(None, description="姓名/证件号/护照号/客户编号 模糊匹配"),
    visa_type: Optional[str] = Query(None, description="按业务类型筛选"),
    expiring_soon_days: Optional[int] = Query(None, description="护照在 N 天内到期的客户"),
    sort_by: str = Query("updated_at", description="updated_at | passport_expiry"),
):
    """客户列表（含文档 / 家属 / 资产 数量统计）。

    Query:
        keyword            可选，按 name/id_number/passport_no/client_code 任一字段模糊匹配
        visa_type          可选，按业务类型精确筛选
        expiring_soon_days 可选，仅返回护照在 N 天内到期的客户
        sort_by            排序字段：updated_at(默认) | passport_expiry

    返回:
        {"clients": [...], "total": N}
        最多 200 条，每条含 id/client_code/name/passport_*/doc_count/family_count/asset_count 等
    """
    clients = await crud.list_clients(
        keyword=keyword,
        visa_type=visa_type,
        expiring_soon_days=expiring_soon_days,
        sort_by=sort_by,
        limit=200,
        offset=0,
    )
    return {"clients": clients, "total": len(clients)}


class ClientMatchPayload(BaseModel):
    id_number: Optional[str] = None
    passport_no: Optional[str] = None
    name: Optional[str] = None
    birth_date: Optional[str] = None


@app.post("/api/clients/match")
async def match_clients_endpoint(payload: ClientMatchPayload):
    """客户智能匹配：OCR 完成后用证件号 / 护照号 / 姓名+生日 查找已有客户候选。

    Body (任一字段非空即可):
        id_number   身份证号
        passport_no 护照号
        name        姓名
        birth_date  出生日期 YYYY-MM-DD（与 name 配合判断）

    匹配评分:
        100 - 身份证号精确命中
         95 - 护照号精确命中
         80 - 姓名+出生日期同时命中
         50 - 仅姓名命中

    返回:
        {
          "candidates": [{client_id, name, score, reason}, ...]  按 score 倒序,
          "best_match_client_id": <int 或 null>  仅当最高分 >= 90 时给出,
          "total": N
        }
    """
    candidates = await crud.match_clients(
        id_number=payload.id_number,
        passport_no=payload.passport_no,
        name=payload.name,
        birth_date=payload.birth_date,
    )
    best = candidates[0]["client_id"] if candidates and candidates[0]["score"] >= 90 else None
    return {"candidates": candidates, "best_match_client_id": best, "total": len(candidates)}


class ClientCreatePayload(BaseModel):
    name: str
    client_code: Optional[str] = None
    name_en: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    nationality: Optional[str] = None
    id_number: Optional[str] = None
    passport_no: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    visa_type: Optional[str] = None
    notes: Optional[str] = None
    # 其余字段不在创建期常用，可通过 PUT 编辑


@app.post("/api/clients")
async def create_client_endpoint(payload: ClientCreatePayload):
    """新建客户档案（前端 ClientListPage 的"+ 新建客户"按钮调用）。

    Body:
        name 必填；其他字段可选；后端会丢弃 None 值
        其余完整字段（联系/护照/教育/工作/婚姻）请通过 PUT /api/clients/{id} 补充

    返回:
        新建的完整客户字典（含 id 与全部字段）

    错误:
        400 - name 缺失或参数不合法
    """
    try:
        data = payload.model_dump(exclude_none=True)
        client = await crud.create_client(data)
        return client
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/clients/{client_id}")
async def get_client(client_id: int):
    """客户详情（含基本信息 + family_members + assets + client_info KV + 名下 documents）。

    路径参数:
        client_id - clients 表主键

    返回:
        完整客户字典:
          - 主表 ~30 字段（身份/联系/护照/教育/工作/婚姻/业务）
          - family_members:[]  家庭成员列表
          - assets:[]          资产列表
          - infos:[]           client_info KV 记录
          - documents:[]       名下解析任务文档

    错误:
        404 - 客户不存在
    """
    detail = await crud.get_client_detail(client_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"客户 {client_id} 不存在")
    return detail


@app.put("/api/clients/{client_id}")
async def update_client_endpoint(client_id: int, payload: dict):
    """编辑客户主表（支持部分字段更新）。

    路径参数:
        client_id - clients 表主键

    Body:
        要更新的字段字典；空字符串 / None 会被忽略；
        日期/数字字段后端自动类型转换

    返回:
        更新后的完整客户字典

    错误:
        404 - 客户不存在
    """
    client = await crud.update_client(client_id, payload or {})
    if not client:
        raise HTTPException(status_code=404, detail=f"客户 {client_id} 不存在")
    return client


# ============== family_members RESTful ==============

@app.get("/api/clients/{client_id}/family")
async def list_family(client_id: int):
    """客户的家庭成员列表（配偶 / 子女 / 父母 / 紧急联系人）。

    路径参数:
        client_id - clients 表主键

    返回:
        {"items": [...]}  按 relation 升序、id 升序
    """
    return {"items": await family_crud.list_by_client(client_id)}


@app.post("/api/clients/{client_id}/family")
async def create_family(client_id: int, payload: dict):
    """新建家庭成员。

    路径参数:
        client_id - 关联客户

    Body:
        必填 relation（配偶/子/女/父/母/紧急联系人）和 name；
        其他字段按场景填（配偶教育、子女出生证、POA 表所需的护照/邮箱/公司/职位 等）

    返回:
        新建的家庭成员记录字典

    错误:
        400 - relation 或 name 缺失
    """
    try:
        return await family_crud.create(client_id, payload or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/family/{member_id}")
async def update_family(member_id: int, payload: dict):
    """更新家庭成员（支持部分字段）。

    路径参数:
        member_id - family_members 表主键

    Body:
        要更新的字段字典；空字符串 / None 自动忽略

    返回:
        更新后的完整家庭成员记录

    错误:
        404 - 该家庭成员不存在
    """
    row = await family_crud.update(member_id, payload or {})
    if not row:
        raise HTTPException(status_code=404, detail=f"家庭成员 {member_id} 不存在")
    return row


@app.delete("/api/family/{member_id}")
async def delete_family(member_id: int):
    """删除家庭成员。

    路径参数:
        member_id - family_members 表主键

    返回:
        {"deleted": True}

    错误:
        404 - 该家庭成员不存在
    """
    ok = await family_crud.delete(member_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"家庭成员 {member_id} 不存在")
    return {"deleted": True}


# ============== assets RESTful ==============

@app.get("/api/clients/{client_id}/assets")
async def list_assets(client_id: int):
    """客户的资产列表（房产/存款/银行流水/股票/车辆/其他）。

    路径参数:
        client_id - clients 表主键

    返回:
        {"items": [...]}  按 asset_type 升序、id 降序
    """
    return {"items": await assets_crud.list_by_client(client_id)}


@app.post("/api/clients/{client_id}/assets")
async def create_asset(client_id: int, payload: dict):
    """新建一笔资产。

    路径参数:
        client_id - 关联客户

    Body:
        必填 asset_type（房产/存款/银行流水/股票/车辆/其他）；
        其余字段按 asset_type 选填（房产用 location_address/area_sqm/... ;
        银行用 bank_name/account_no/period_*）

    返回:
        新建的资产记录字典

    错误:
        400 - asset_type 缺失等参数错误
    """
    try:
        return await assets_crud.create(client_id, payload or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/assets/{asset_id}")
async def update_asset(asset_id: int, payload: dict):
    """更新一笔资产（部分字段）。

    路径参数:
        asset_id - assets 表主键

    Body:
        要更新的字段字典；未传字段保持不变

    返回:
        更新后的完整资产记录

    错误:
        404 - 该资产不存在
    """
    row = await assets_crud.update(asset_id, payload or {})
    if not row:
        raise HTTPException(status_code=404, detail=f"资产 {asset_id} 不存在")
    return row


@app.delete("/api/assets/{asset_id}")
async def delete_asset(asset_id: int):
    """删除一笔资产。

    路径参数:
        asset_id - assets 表主键

    返回:
        {"deleted": True}

    错误:
        404 - 该资产不存在
    """
    ok = await assets_crud.delete(asset_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"资产 {asset_id} 不存在")
    return {"deleted": True}


# ============== field_router 元数据 ==============

@app.get("/api/field-router/doc-types")
async def get_doc_types():
    """获取字段路由器中已知的所有 doc_type 列表（前端 DocTypeSelector 下拉数据源）。

    返回:
        {"doc_types": [...]}  排序的字符串列表，例如 ["不动产权证", "出生医学证", "存款证明", ...]

    说明:
        来自 backend/db/field_router.py 中 DOC_TYPE_TO_ENTITY 的键集合。
        前端在归档审核面板里用作"文件类型"下拉的候选项。
    """
    from db import field_router
    return {"doc_types": field_router.list_doc_types()}


class ClientInfoUpsertPayload(BaseModel):
    """模板填写时反向同步主数据用的 payload。"""
    key_values: dict  # {info_key: info_value}


@app.post("/api/clients/{client_id}/info")
async def upsert_client_info_endpoint(client_id: int, payload: ClientInfoUpsertPayload):
    """反向同步主数据（B1）：模板填写界面把可变字段勾选"同步到档案"后调用。

    路径参数:
        client_id - clients 表主键

    Body:
        {"key_values": {info_key: info_value, ...}}
        info_key 通常是中文字段标签（如"地址"、"电话"），info_value 是字符串

    行为:
        - 复用 crud.upsert_client_info 的 upsert 语义
        - 锁定字段（id_number/passport_no 等）由前端不发送来保证，本接口对所有传入键平等处理
        - 同 (client_id, info_key) 的现有记录会被更新；不存在则插入

    返回:
        {"updated": N}  实际写入条数

    错误:
        404 - 当 key_values 非空但客户不存在时
    """
    if not payload.key_values:
        return {"updated": 0}
    written = await crud.upsert_client_info(client_id, payload.key_values)
    if written == 0:
        # 客户不存在 or 全部值为空
        client = await crud.get_client_detail(client_id)
        if not client:
            raise HTTPException(status_code=404, detail=f"客户 {client_id} 不存在")
    return {"updated": written}


@app.get("/api/clients/{client_id}/fills")
async def get_client_fills(client_id: int):
    """客户详情页"已生成文件"tab（B2）：返回该客户的模板填充历史。

    路径参数:
        client_id - clients 表主键

    返回:
        {
          "client_id": ...,
          "fills": [{id, template_id, template_name, output_url, output_kind,
                     placeholder_count, created_at}, ...],
          "total": N
        }
        按时间倒序，最多 100 条；output_url 为 /uploads/... 可直接下载
    """
    fills = await template_crud.list_fills_by_client(client_id, limit=100)
    return {"client_id": client_id, "fills": fills, "total": len(fills)}

@app.get("/api/export/{task_id}")
async def export_result(task_id: str):
    """导出解析任务的结构化结果为 JSON 文件下载。

    路径参数:
        task_id - 解析任务 ID

    返回:
        Content-Type: application/json 附件下载
        文件名: {task_id}-解析后.json（中文按 RFC 5987 编码）
        内容: 完整的解析结果（含 items / images / ocr_texts）

    错误:
        404 - 任务不存在
    """
    # 从内存缓存或数据库加载
    if task_id in _task_results:
        data = _task_results[task_id]
    else:
        doc = await crud.get_document(task_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
        data = _build_result_from_db(doc)

    # 文件名用 RFC 5987 编码处理中文
    from urllib.parse import quote
    filename = f"{task_id}-解析后.json"
    filename_encoded = quote(filename, safe='')

    from fastapi.responses import Response
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=\"result.json\"; filename*=UTF-8''{filename_encoded}"
        }
    )

@app.delete("/api/history/{task_id}")
async def delete_history(task_id: str):
    """删除一条解析历史记录（数据库记录 + 文件系统目录）。

    路径参数:
        task_id - 解析任务 ID

    行为:
        - DB: 删除 documents 表对应行 + 关联的 client_info（按 source_doc_id）
        - FS: 删除 output/{task_id}/ 整个目录（OCR 渲染图、解析 JSON 等）
        - 内存: 清理 _task_status / _task_results 缓存

    返回:
        {"message": "已删除", "task_id": "..."}

    错误:
        404 - DB 与 FS 都找不到该任务
    """
    import shutil

    # 删除数据库记录
    deleted = await crud.delete_document(task_id)

    # 删除文件系统
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    if os.path.exists(task_dir):
        shutil.rmtree(task_dir)
    elif not deleted:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    # 清理内存缓存
    _task_status.pop(task_id, None)
    _task_results.pop(task_id, None)
    _task_results_ts.pop(task_id, None)
    return {"message": "已删除", "task_id": task_id}

def _calc_stats(fields: dict) -> dict:
    """计算字段统计信息"""
    if not fields:
        return {"total": 0, "avg_confidence": 0, "needs_review": 0}

    total = len(fields)
    confidences = []
    needs_review = 0
    for field_info in fields.values():
        if isinstance(field_info, dict):
            conf = field_info.get("confidence", 0.5)
        else:
            conf = 0.5
        confidences.append(conf)
        if conf < 0.8:
            needs_review += 1

    avg_confidence = round(sum(confidences) / len(confidences) * 100, 1) if confidences else 0

    return {
        "total": total,
        "avg_confidence": avg_confidence,
        "needs_review": needs_review
    }

# ====================== Word 模板相关路由 ======================

TEMPLATE_TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp", "templates")
TEMPLATE_OUTPUT_DIR = os.path.join(OUTPUT_DIR, "templates")

@app.post("/api/templates/parse")
async def parse_template(file: UploadFile = File(...)):
    """
    上传 docx（不入库）—— v2:
      - mammoth 转 HTML
      - scan_anchors（结构化扫描）输出 anchor 候选列表
      - enrich_anchors_with_llm 给每个 anchor 加 description + field_hint
      - 渲染 Word 原貌 PNG

    返回：{html, anchors, pages, temp_token, filename}
    """
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 模板文件")

    os.makedirs(TEMPLATE_TEMP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%y%m%d%H%M%S")
    stem = os.path.splitext(file.filename or "template")[0].replace(" ", "")
    temp_token = f"{timestamp}_{stem}"
    temp_path = os.path.join(TEMPLATE_TEMP_DIR, f"{temp_token}.docx")

    await save_upload_stream(file, temp_path)

    # 1) mammoth 转 HTML
    html = await asyncio.to_thread(template_service.convert_docx_to_html, temp_path)

    # 2) 结构化扫描 → anchor 列表
    raw_anchors = await asyncio.to_thread(
        template_service.scan_anchors, temp_path
    )

    # 3) LLM enrich：给每个 anchor 加 description + field_hint
    enriched = await asyncio.to_thread(
        template_service.enrich_anchors_with_llm, raw_anchors, None
    )

    # 给 anchor 分配 strN id
    anchors_with_id: list[dict] = []
    for i, a in enumerate(enriched):
        item = dict(a)
        item["id"] = f"str{i+1}"
        anchors_with_id.append(item)

    # 缓存 enrich 结果到磁盘，供后续 quick-save 复用
    try:
        enrich_path = os.path.join(TEMPLATE_TEMP_DIR, f"{temp_token}.enrich.json")
        with open(enrich_path, "w", encoding="utf-8") as f:
            json.dump(anchors_with_id, f, ensure_ascii=False)
    except OSError as e:
        print(f"[templates/parse] 缓存 enrich 失败（不影响 parse）: {e}")

    # 4) Word 原貌渲染
    pages: list[str] = []
    parse_preview_dir = os.path.join(TEMPLATE_OUTPUT_DIR, "_parse", temp_token)
    try:
        abs_paths = await asyncio.to_thread(
            template_service.render_docx_pages, temp_path, parse_preview_dir
        )
        rel = os.path.relpath(parse_preview_dir, OUTPUT_DIR).replace("\\", "/")
        pages = [f"/uploads/{rel}/page_{i + 1}.png" for i in range(len(abs_paths))]
    except Exception as e:
        print(f"[templates/parse] Word 原貌渲染失败，前端将降级到 HTML 视图: {e}")

    return {
        "html": html,
        "anchors": anchors_with_id,
        "pages": pages,
        "temp_token": temp_token,
        "filename": file.filename,
    }

class TemplateSavePayload(BaseModel):
    name: str
    filename: Optional[str] = None
    anchors: list
    temp_token: str

def _normalize_anchors(raw: list) -> list[dict]:
    """规范化前端传入的 anchor 列表。

    每项最终形如：
      {id, anchor: {kind, t/r/c or container/p/run_index/...}, description, field_hint, default_fmt}
    """
    out: list[dict] = []
    for i, ph in enumerate(raw):
        if not isinstance(ph, dict):
            continue
        anchor_dict = ph.get("anchor")
        if not isinstance(anchor_dict, dict) or not anchor_dict.get("kind"):
            continue
        item = {
            "id": ph.get("id") or f"str{i+1}",
            "anchor": anchor_dict,
            "description": str(ph.get("description") or "").strip(),
            "field_hint": ph.get("field_hint") or None,
            "default_fmt": ph.get("default_fmt") or None,
        }
        out.append(item)
    return out

async def _persist_template_v2(
    name: str,
    filename: str,
    anchors: list[dict],
    temp_token: str,
) -> dict:
    """v2 落库：基于 anchor 的存储，不再注入 marker。

    步骤：移动 docx → create_template（anchors 入库）→ 清理 _parse + enrich 缓存 →
    更新 file_path → 预渲染 pages。
    """
    import shutil

    if not name.strip():
        raise HTTPException(status_code=400, detail="模板名称不能为空")
    if not anchors:
        raise HTTPException(status_code=400, detail="占位符列表不能为空")

    temp_path = os.path.join(TEMPLATE_TEMP_DIR, f"{temp_token}.docx")
    if not os.path.exists(temp_path):
        raise HTTPException(status_code=404, detail="临时文件已过期，请重新上传")

    # 1) 移动 docx 到 final 路径
    final_dir = os.path.join(TEMPLATE_OUTPUT_DIR, "pending")
    os.makedirs(final_dir, exist_ok=True)
    pending_path = os.path.join(final_dir, f"{temp_token}.docx")
    try:
        shutil.move(temp_path, pending_path)
    except OSError as e:
        shutil.copy(temp_path, pending_path)
        try:
            os.remove(temp_path)
        except OSError:
            pass
        print(f"[templates] 移动文件带告警: {e}")

    # 2) 入库（anchors 直接存 JSONB，不再有 marker 注入）
    tpl = await template_crud.create_template(
        name=name.strip(),
        filename=filename or "",
        file_path="",
        placeholders=anchors,
        created_by="default",
    )

    # 3) 移到 output/templates/{id}/template.docx
    final_real = os.path.join(TEMPLATE_OUTPUT_DIR, str(tpl.id))
    os.makedirs(final_real, exist_ok=True)
    target_path = os.path.join(final_real, "template.docx")
    try:
        shutil.move(pending_path, target_path)
    except OSError as e:
        shutil.copy(pending_path, target_path)
        try:
            os.remove(pending_path)
        except OSError:
            pass
        print(f"[templates] 移动到 final 路径带告警: {e}")

    # 4) 清理临时预览 + enrich 缓存
    parse_dir = os.path.join(TEMPLATE_OUTPUT_DIR, "_parse", temp_token)
    if os.path.isdir(parse_dir):
        try:
            shutil.rmtree(parse_dir)
        except OSError as e:
            print(f"[templates] 清理临时预览目录失败（不影响保存）: {e}")
    enrich_path = os.path.join(TEMPLATE_TEMP_DIR, f"{temp_token}.enrich.json")
    if os.path.exists(enrich_path):
        try:
            os.remove(enrich_path)
        except OSError:
            pass

    rel_path = os.path.relpath(
        target_path, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )
    await template_crud.update_template_file_path(tpl.id, rel_path.replace("\\", "/"))

    # 5) 预渲染 pages 缓存
    preview_dir = os.path.join(final_real, "preview")
    try:
        await asyncio.to_thread(
            template_service.render_docx_pages, target_path, preview_dir
        )
    except Exception as e:
        print(f"[templates] 预渲染 pages 失败（不影响保存）: {e}")

    return {"id": tpl.id, "name": tpl.name, "placeholder_count": len(anchors)}

@app.post("/api/templates")
async def save_template(payload: TemplateSavePayload):
    """手动路径：用户在 dialog 里逐项采纳/编辑 anchor 后保存模板。

    Body:
        {
          "name": "模板名（用户命名）",
          "filename": "原 docx 文件名",
          "anchors": [{id, anchor:{...}, description, field_hint, ...}],
          "temp_token": "parse 阶段返回的临时 token"
        }

    行为:
        - 把 temp/templates/{token}.docx 移到 output/templates/{id}/template.docx
        - 异步渲染 Word 原貌 PNG 缓存到 output/templates/{id}/preview/
        - 清理 _parse/ 临时预览目录与 enrich.json

    返回:
        {"id": int, "name": str, "message": "模板已保存"}

    错误:
        400 - anchors 列表为空
        404 - temp_token 对应的临时文件已过期
    """
    anchors = _normalize_anchors(payload.anchors)
    if not anchors:
        raise HTTPException(status_code=400, detail="占位符列表不能为空")

    result = await _persist_template_v2(
        name=payload.name,
        filename=payload.filename or "",
        anchors=anchors,
        temp_token=payload.temp_token,
    )
    return {"id": result["id"], "name": result["name"], "message": "模板已保存"}

class QuickSavePayload(BaseModel):
    name: str
    filename: Optional[str] = None
    temp_token: str

@app.post("/api/templates/quick-save")
async def quick_save_template(payload: QuickSavePayload):
    """快速路径：跳过 dialog 三步向导，把 parse 阶段产出的建议直接自动入库。

    v2：直接读 parse 阶段缓存的 enrich.json（含 anchor + description + field_hint），
    不再调 LLM，不调 scan（结果已在 enrich 缓存里）。
    """
    enrich_path = os.path.join(TEMPLATE_TEMP_DIR, f"{payload.temp_token}.enrich.json")
    if not os.path.exists(enrich_path):
        raise HTTPException(status_code=404, detail="临时文件已过期，请重新上传")

    try:
        with open(enrich_path, "r", encoding="utf-8") as f:
            anchors = json.load(f) or []
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"读取临时缓存失败: {e}")

    if not anchors:
        raise HTTPException(
            status_code=400,
            detail="未检测到任何占位符，请改用「手动标注」流程",
        )

    result = await _persist_template_v2(
        name=payload.name,
        filename=payload.filename or "",
        anchors=anchors,
        temp_token=payload.temp_token,
    )
    return {
        "id": result["id"],
        "name": result["name"],
        "placeholder_count": result["placeholder_count"],
        "message": "模板已自动识别并保存",
    }

@app.get("/api/templates")
async def list_templates():
    """Word 模板列表（按更新时间倒序）。

    返回:
        {"templates": [{id, name, filename, placeholder_count, legacy,
                        created_at, updated_at}, ...], "total": N}
        legacy=True 表示是 v1 老结构，前端可标记为兼容模式
    """
    items = await template_crud.list_templates(limit=200, offset=0)
    return {"templates": items, "total": len(items)}

@app.get("/api/templates/{template_id}")
async def get_template(template_id: int):
    """模板详情（含完整 placeholders 列表）。

    路径参数:
        template_id - templates 表主键

    返回:
        {
          "id": ..., "name": ..., "filename": ...,
          "file_path": ..., "placeholders": [...],
          "created_at": ..., "updated_at": ...
        }

    错误:
        404 - 模板不存在
    """
    detail = await template_crud.get_template_dict(template_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")
    return detail

@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: int):
    """删除模板（同步清理文件系统）。

    路径参数:
        template_id - templates 表主键

    行为:
        - DB: 删除 templates 行 + 级联删除 template_fills 关联记录
        - FS: 删除 output/templates/{id}/ 整个目录（template.docx + preview/ + fills/）

    返回:
        {"message": "已删除", "id": template_id}

    错误:
        404 - DB 与 FS 都找不到该模板
    """
    import shutil

    deleted = await template_crud.delete_template(template_id)
    tpl_dir = os.path.join(TEMPLATE_OUTPUT_DIR, str(template_id))
    if os.path.exists(tpl_dir):
        shutil.rmtree(tpl_dir)
    elif not deleted:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")
    return {"message": "已删除", "id": template_id}

@app.get("/api/templates/{template_id}/preview-html")
async def get_template_preview_html(template_id: int):
    """
    返回模板的原始 HTML + placeholders，供填写页做前端替换预览。
    {html, placeholders}
    """
    tpl = await template_crud.get_template_dict(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")

    file_path = tpl.get("file_path") or ""
    if not os.path.isabs(file_path):
        backend_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.normpath(os.path.join(backend_dir, "..", file_path))
    try:
        html = await asyncio.to_thread(template_service.convert_docx_to_html, file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="模板文件不存在")

    return {
        "html": html,
        "placeholders": tpl.get("placeholders") or [],
        "name": tpl.get("name"),
        "filename": tpl.get("filename"),
    }

@app.get("/api/templates/{template_id}/preview-pages")
async def get_template_preview_pages(template_id: int):
    """
    返回模板的 Word 原貌 PNG 页面 URL 列表（/uploads/...）。
    首次调用渲染并缓存到 output/templates/{id}/preview/，后续直接返回。
    """
    tpl = await template_crud.get_template_dict(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")

    file_path = tpl.get("file_path") or ""
    if not os.path.isabs(file_path):
        backend_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.normpath(os.path.join(backend_dir, "..", file_path))
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="模板文件不存在")

    preview_dir = os.path.join(TEMPLATE_OUTPUT_DIR, str(template_id), "preview")

    # 缓存命中：已存在 page_*.png 直接返回 URL
    if os.path.isdir(preview_dir):
        existing = sorted(
            p for p in os.listdir(preview_dir)
            if p.startswith("page_") and p.endswith(".png")
        )
        if existing:
            rel = os.path.relpath(preview_dir, OUTPUT_DIR).replace("\\", "/")
            return {"pages": [f"/uploads/{rel}/{name}" for name in existing]}

    # 缓存未命中：渲染
    try:
        abs_paths = await asyncio.to_thread(
            template_service.render_docx_pages, file_path, preview_dir
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Word 原貌渲染失败: {e}")

    rel = os.path.relpath(preview_dir, OUTPUT_DIR).replace("\\", "/")
    return {"pages": [f"/uploads/{rel}/page_{i + 1}.png" for i in range(len(abs_paths))]}

class MapClientPayload(BaseModel):
    client_id: int

@app.post("/api/templates/{template_id}/map-client")
async def map_client_to_template(template_id: int, payload: MapClientPayload):
    """选客户后做 anchor → 客户字段匹配（field_hint 规则优先 + LLM 兜底，带缓存）。

    路径参数:
        template_id - templates 表主键

    Body:
        {"client_id": 1}

    匹配策略:
        1. 优先查 (template_id, client_id) 历史 fill 缓存（最近一次填充值）
        2. 若 anchor 有 field_hint，按字段字典从客户档案查值（"主表 → 子表 → KV"三级）
        3. 兜底用 LLM 给无 field_hint 的 anchor 推断

    返回:
        {
          "matched": {"str1": "value1", ...},   命中的 anchor id → 值
          "unmatched": ["str3", ...],           未匹配的 anchor id 列表
          "from_cache": true | false             是否命中历史缓存
        }

    错误:
        404 - 模板不存在
    """
    tpl = await template_crud.get_template_dict(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")

    result = await template_service.match_anchors_to_client(
        anchors=tpl.get("placeholders") or [],
        client_id=payload.client_id,
        template_id=template_id,
    )
    return result

class GeneratePayload(BaseModel):
    client_id: Optional[int] = None
    anchor_values: dict  # v2：{strN: value}

@app.post("/api/templates/{template_id}/generate")
async def generate_template_pdf(template_id: int, payload: GeneratePayload):
    """生成填充后的模板文件（PDF 优先，失败降级 DOCX）。

    路径参数:
        template_id - templates 表主键

    Body:
        {
          "client_id": <可选 int>,                 关联客户（仅记录历史用）
          "anchor_values": {"str1": "值", ...}     anchor id → 替换值
        }

    行为:
        - 用 anchor_values 替换模板 anchor 内容，生成 docx
        - 调 docx2pdf 转 PDF（依赖 Windows + Word）
        - PDF 转换失败时返回 docx 并设置 X-Fallback-Docx=1 头
        - 同步写一条 template_fills 记录作为历史 + 下次匹配缓存

    返回:
        FileResponse:
            - Content-Type: application/pdf 或 docx
            - Content-Disposition: attachment + 中文文件名
            - X-Fallback-Docx: "0"=PDF 成功，"1"=降级 docx

    错误:
        404 - 模板或源 docx 文件不存在
        500 - 渲染失败 / 输出文件丢失
    """
    from fastapi.responses import FileResponse

    tpl = await template_crud.get_template_dict(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")

    try:
        pdf_path, fallback_docx = await template_service.render_to_pdf_v2(
            template_id=template_id,
            anchor_values=payload.anchor_values or {},
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成失败: {e}")

    output_file = pdf_path or fallback_docx
    if not output_file or not os.path.exists(output_file):
        raise HTTPException(status_code=500, detail="输出文件不存在")

    # 记录填充历史作为缓存
    try:
        await template_crud.create_template_fill(
            template_id=template_id,
            client_id=payload.client_id,
            placeholder_values=payload.anchor_values or {},
            output_pdf=output_file,
        )
    except Exception as e:
        print(f"[templates] 写入填充记录失败（不影响返回）: {e}")

    is_pdf = pdf_path is not None
    ext = ".pdf" if is_pdf else ".docx"
    media_type = "application/pdf" if is_pdf else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    download_name = f"{tpl['name']}{ext}"
    from urllib.parse import quote
    download_encoded = quote(download_name, safe='')

    headers = {
        "Content-Disposition": f"attachment; filename=\"output{ext}\"; filename*=UTF-8''{download_encoded}",
        "X-Fallback-Docx": "1" if not is_pdf else "0",
    }
    return FileResponse(output_file, media_type=media_type, headers=headers, filename=download_name)

# ====================== PDF 自动按证件拆分(处理超长PDF文件) ======================

# 原 PDF 落盘到 output/{task_id}/_original.pdf 而非 temp/,作为审计归档保留 7 天。
# 7 天后由 _cleanup_expired_split_tasks 周期任务删除整个目录,DB 记录保留并置 files_cleaned=true。
SPLIT_ORIGINAL_FILENAME = "_original.pdf"
SPLIT_TASK_TTL_DAYS = 7
SPLIT_CLEANUP_INTERVAL_HOURS = 24


def _hydrate_split_status_from_db(task_id: str, row) -> dict:
    """把 DB 行转为 _split_task_status 兼容格式。供下载/状态接口在内存丢失时回落使用。"""
    if row is None:
        return None
    if row.status == "done" and row.ranges:
        result = {
            "task_id": row.task_id,
            "total_pages": row.total_pages or 0,
            "ranges": row.ranges,
        }
    else:
        result = None
    return {
        "status": row.status,
        "progress": "",
        "error": row.error or "",
        "result": result,
        "files_cleaned": bool(row.files_cleaned),
    }


async def _process_split_background(task_id: str, pdf_path: str, t_started: float):
    """后台异步管线:OCR -> LLM 判断页边界 -> 规整 -> 切分 PDF。

    全过程同步写两处:
      - 内存 _split_task_status:前端 1.5s 轮询,需要细粒度 progress 文本
      - DB split_tasks:进程重启后仍可查
    """
    try:
        # Step 1: 全页 OCR(降 DPI 200 + 双线程并发,见 split_ocr_service)
        # 不复用 ocr_service.process_file:它会受 config.json 的 max_ocr_pages 限制(为单证件解析流水线设计),
        # 拆分场景每页都可能是不同证件,必须全 OCR
        _split_task_status[task_id] = {"status": "ocr", "progress": "识别中...", "error": "", "result": None}
        await split_crud.update_status(task_id, "ocr")
        print(f"[split:{task_id}] 开始 OCR(全页, 200dpi, 双线程)")
        ocr_results = await asyncio.to_thread(
            split_ocr_service.split_extract_all_pages,
            pdf_path, task_id, 200, 2,    # dpi=200, max_workers=2
        )
        total_pages = len(ocr_results)
        _split_task_status[task_id]["progress"] = f"已识别 {total_pages} 页"

        per_page_texts = [page.get("text", "") for page in ocr_results]

        # Step 2: LLM 判断每页归属
        _split_task_status[task_id] = {"status": "llm", "progress": "分析页边界...", "error": "", "result": None}
        await split_crud.update_status(task_id, "llm", total_pages=total_pages)
        print(f"[split:{task_id}] 调用 LLM 判断页边界")
        raw_ranges = await asyncio.to_thread(llm_service.detect_page_ranges, per_page_texts, task_id=task_id)

        # Step 3: 规整 + 拆分
        _split_task_status[task_id] = {"status": "splitting", "progress": "拆分中...", "error": "", "result": None}
        await split_crud.update_status(task_id, "splitting")
        doc_types = llm_service.CONFIG.get("document_types", [])
        normalized = split_service.normalize_ranges(raw_ranges, total_pages, doc_types)
        out_dir = os.path.join(OUTPUT_DIR, task_id)
        files = split_service.split_pdf_by_ranges(pdf_path, normalized, out_dir, task_id)

        # Step 4: 组装可序列化 result(前端用 filename/page_*/doc_type/fields,不暴露绝对路径)
        ranges_payload = []
        for idx, item in enumerate(files):
            ranges_payload.append({
                "idx": idx,
                "doc_type": item["doc_type"],
                "page_start": item["page_start"],
                "page_end": item["page_end"],
                "filename": item["filename"],
                "fields": item.get("fields") or {},
            })
        result = {
            "task_id": task_id,
            "total_pages": total_pages,
            "ranges": ranges_payload,
        }
        duration = round(time.time() - t_started, 2)
        await split_crud.update_done(task_id, total_pages, ranges_payload, duration)
        _split_task_status[task_id] = {"status": "done", "progress": "", "error": "", "result": result, "_finished_ts": time.time()}
        print(f"[split:{task_id}] 完成: {len(files)} 份子 PDF, 耗时 {duration}s")
    except Exception as e:
        print(f"[split:{task_id}] 失败: {e}")
        _split_task_status[task_id] = {"status": "error", "progress": "", "error": str(e), "result": None, "_finished_ts": time.time()}
        try:
            await split_crud.update_status(task_id, "error", error=str(e))
        except Exception as db_err:
            print(f"[split:{task_id}] 写 DB 失败状态也失败(忽略): {db_err}")
    # 不再删原 PDF:已在 output/{task_id}/_original.pdf 作归档,等待 7 天 TTL 清理


@app.post("/api/split")
async def split_upload(file: UploadFile = File(...)):
    """上传一份多证件 PDF,异步拆分。立即返回 task_id,前端轮询进度。

    原 PDF 落盘到 output/{task_id}/_original.pdf 作审计归档,与子 PDF/页 PNG 一起保留 7 天。
    DB 同步写 split_tasks 一条记录,确保进程重启后历史仍可查。
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="拆分功能仅支持 .pdf 文件")

    timestamp = datetime.now().strftime("%y%m%d%H%M%S")
    raw_name = file.filename or "split.pdf"
    stem = os.path.splitext(raw_name)[0].replace(" ", "")
    task_id = f"{timestamp}_{stem}"

    # 避免目录冲突(用户连续上传同名文件)
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    if os.path.exists(task_dir):
        n = 2
        while os.path.exists(os.path.join(OUTPUT_DIR, f"{timestamp}_{stem}_{n}")):
            n += 1
        task_id = f"{timestamp}_{stem}_{n}"
        task_dir = os.path.join(OUTPUT_DIR, task_id)

    os.makedirs(task_dir, exist_ok=True)
    original_path = os.path.join(task_dir, SPLIT_ORIGINAL_FILENAME)
    await save_upload_stream(file, original_path)

    await split_crud.create(task_id=task_id, filename=raw_name)

    _split_task_status[task_id] = {"status": "ocr", "progress": "0页", "error": "", "result": None}
    t_started = time.time()
    asyncio.create_task(_process_split_background(task_id, original_path, t_started))
    return {"task_id": task_id, "status": "processing"}


@app.get("/api/split/history")
async def split_history(limit: int = 200):
    """拆分任务历史列表(含已清理但记录尚存的)。"""
    items = await split_crud.list_history(limit=limit, offset=0)
    return {"history": items, "total": len(items)}


@app.delete("/api/split/history/{task_id}")
async def delete_split_history(task_id: str):
    """彻底删除一条拆分历史(DB 记录 + output/{task_id}/ 目录)。"""
    import shutil

    deleted = await split_crud.delete(task_id)
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    if os.path.isdir(task_dir):
        shutil.rmtree(task_dir, ignore_errors=True)
    elif not deleted:
        raise HTTPException(status_code=404, detail=f"拆分任务 {task_id} 不存在")
    _split_task_status.pop(task_id, None)
    return {"message": "已删除", "task_id": task_id}


@app.get("/api/split/{task_id}")
async def split_status(task_id: str):
    """轮询拆分任务进度/结果。内存无记录时回落到 DB 查(进程重启场景)。"""
    s = _split_task_status.get(task_id)
    if s:
        return s
    row = await split_crud.get(task_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"拆分任务 {task_id} 不存在")
    return _hydrate_split_status_from_db(task_id, row)


@app.get("/api/split/{task_id}/download/{idx}")
async def split_download_one(task_id: str, idx: int):
    """下载第 idx 份拆分出的 PDF(0-based)。"""
    from fastapi.responses import FileResponse
    from urllib.parse import quote

    s = _split_task_status.get(task_id)
    if not s:
        row = await split_crud.get(task_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"拆分任务 {task_id} 不存在")
        s = _hydrate_split_status_from_db(task_id, row)
    if s["status"] != "done" or not s.get("result"):
        raise HTTPException(status_code=400, detail="拆分尚未完成")
    if s.get("files_cleaned"):
        raise HTTPException(status_code=410, detail="拆分文件已超过 7 天保留期被清理")

    ranges = s["result"].get("ranges") or []
    if idx < 0 or idx >= len(ranges):
        raise HTTPException(status_code=404, detail=f"索引 {idx} 越界")

    filename = ranges[idx]["filename"]
    file_path = os.path.join(OUTPUT_DIR, task_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件已被删除")

    encoded = quote(filename, safe='')
    return FileResponse(
        file_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"split.pdf\"; filename*=UTF-8''{encoded}",
        },
        filename=filename,
    )


@app.get("/api/split/{task_id}/download-all")
async def split_download_all(task_id: str):
    """打包下载该任务的全部拆分子 PDF(zip)。"""
    import io
    import zipfile
    from fastapi.responses import Response
    from urllib.parse import quote

    s = _split_task_status.get(task_id)
    if not s:
        row = await split_crud.get(task_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"拆分任务 {task_id} 不存在")
        s = _hydrate_split_status_from_db(task_id, row)
    if s["status"] != "done" or not s.get("result"):
        raise HTTPException(status_code=400, detail="拆分尚未完成")
    if s.get("files_cleaned"):
        raise HTTPException(status_code=410, detail="拆分文件已超过 7 天保留期被清理")

    ranges = s["result"].get("ranges") or []
    if not ranges:
        raise HTTPException(status_code=404, detail="无可下载的文件")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in ranges:
            filename = item["filename"]
            file_path = os.path.join(OUTPUT_DIR, task_id, filename)
            if os.path.exists(file_path):
                zf.write(file_path, arcname=filename)
    buf.seek(0)

    zip_name = f"{task_id}.zip"
    encoded = quote(zip_name, safe='')
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"split.zip\"; filename*=UTF-8''{encoded}",
        },
    )


async def _split_cleanup_once() -> int:
    """对超出 SPLIT_TASK_TTL_DAYS 天的拆分任务,删除 output/{task_id}/ 整个目录。

    DB 记录保留并置 files_cleaned=true,前端历史页仍能看到任务但下载/预览置灰。
    返回本轮清理的任务数。
    """
    import shutil

    expired = await split_crud.list_expired_task_ids(SPLIT_TASK_TTL_DAYS)
    if not expired:
        return 0
    cleaned_ids: list[str] = []
    for tid in expired:
        task_dir = os.path.join(OUTPUT_DIR, tid)
        if os.path.isdir(task_dir):
            try:
                shutil.rmtree(task_dir, ignore_errors=False)
            except OSError as e:
                print(f"[split_cleanup] 删除 {task_dir} 失败,跳过: {e}")
                continue
        cleaned_ids.append(tid)
    if cleaned_ids:
        await split_crud.mark_files_cleaned(cleaned_ids)
        print(f"[split_cleanup] 清理 {len(cleaned_ids)} 个 >{SPLIT_TASK_TTL_DAYS} 天的拆分任务: {cleaned_ids}")
    return len(cleaned_ids)


def _inmem_ttl_gc() -> tuple[int, int, int]:
    """清理 _task_status / _task_results / _split_task_status 中超过 TTL 的终态条目。

    数据已在 DB,内存清掉只影响轮询 fast-path。无 _finished_ts(或 _task_results_ts)的旧条目
    lazy 补一个,下一轮 GC 才会被清,保证升级期间不爆毁现有任务。
    返回 (清理的 _task_status 数, _task_results 数, _split_task_status 数)。
    """
    now = time.time()
    cutoff = now - INMEM_TASK_TTL_SECONDS
    deleted_status = 0
    deleted_results = 0
    deleted_split = 0

    # _task_status: 仅清终态(done/error)
    stale = []
    for tid, info in _task_status.items():
        if not isinstance(info, dict):
            continue
        if info.get("status") not in ("done", "error"):
            continue
        ts = info.get("_finished_ts")
        if ts is None:
            info["_finished_ts"] = now
            continue
        if ts < cutoff:
            stale.append(tid)
    for tid in stale:
        _task_status.pop(tid, None)
        deleted_status += 1

    # _split_task_status: 同上
    stale = []
    for tid, info in _split_task_status.items():
        if not isinstance(info, dict):
            continue
        if info.get("status") not in ("done", "error"):
            continue
        ts = info.get("_finished_ts")
        if ts is None:
            info["_finished_ts"] = now
            continue
        if ts < cutoff:
            stale.append(tid)
    for tid in stale:
        _split_task_status.pop(tid, None)
        deleted_split += 1

    # _task_results: 用独立 ts 表(可能含从 DB 重 hydrate 的条目,生命周期独立于 _task_status)
    stale = []
    for tid in list(_task_results.keys()):
        ts = _task_results_ts.get(tid)
        if ts is None:
            _task_results_ts[tid] = now
            continue
        if ts < cutoff:
            stale.append(tid)
    for tid in stale:
        _task_results.pop(tid, None)
        _task_results_ts.pop(tid, None)
        deleted_results += 1

    return deleted_status, deleted_results, deleted_split


async def _split_cleanup_loop():
    """周期任务:每 SPLIT_CLEANUP_INTERVAL_HOURS 小时跑一次 _split_cleanup_once。

    启动后立即跑一次(catch up 任何上次 server 关停期间过期的);之后按周期跑。
    内存表 TTL GC 频率更高(每小时),数据已在 DB,清掉只是少量 fallback。
    """
    inmem_gc_every = 3600   # 每小时 GC 一次内存表
    last_split_cleanup = 0.0
    while True:
        now = time.time()
        try:
            if now - last_split_cleanup >= SPLIT_CLEANUP_INTERVAL_HOURS * 3600:
                await _split_cleanup_once()
                # 原仅启动时跑的三项清理,并入日级周期:长期不重启也会执行(startup 调用保留,双保险)
                await asyncio.to_thread(_cleanup_expired_output, 30)        # output/ 解析任务目录 >30 天
                await asyncio.to_thread(_cleanup_stale_template_temp, 60)   # 模板 temp >60 分钟
                await asyncio.to_thread(file_fetcher._cleanup_stale_files)  # temp/fetched >1h 残留(含 worker 进程遗留)
                last_split_cleanup = now
        except Exception as e:
            print(f"[split_cleanup] 异常(忽略,下个周期重试): {e}")
        try:
            ds, dr, dsp = _inmem_ttl_gc()
            if ds or dr or dsp:
                print(f"[inmem_gc] 清理 task_status={ds} task_results={dr} split_task_status={dsp}")
        except Exception as e:
            print(f"[inmem_gc] 异常(忽略): {e}")
        # 事件流 GC:30 天保留
        try:
            from db import event_crud as _event_crud
            deleted_events = await _event_crud.delete_events_older_than(days=30)
            if deleted_events:
                print(f"[event_gc] 清理 {deleted_events} 条 >30 天的事件")
        except Exception as e:
            print(f"[event_gc] 异常(忽略): {e}")
        # 外部请求日志 GC:30 天
        try:
            from db import request_log_crud as _rlc
            deleted_req = await _rlc.delete_request_logs_older_than(days=30)
            if deleted_req:
                print(f"[request_log_gc] 清理 {deleted_req} 条 >30 天的外部请求日志")
                event_service.log_event(
                    severity="info",
                    category="gc.cleanup",
                    message=f"清理 {deleted_req} 条 >30 天的外部请求日志",
                    context={"table": "api_request_logs", "deleted": deleted_req, "days": 30},
                )
        except Exception as e:
            print(f"[request_log_gc] 异常(忽略): {e}")
        # 调用外部接口 GC:30 天
        try:
            from db import external_api_log_crud as _ealc
            deleted_ext = await _ealc.delete_external_api_logs_older_than(days=30)
            if deleted_ext:
                print(f"[external_api_log_gc] 清理 {deleted_ext} 条 >30 天的调用外部接口")
                event_service.log_event(
                    severity="info",
                    category="gc.cleanup",
                    message=f"清理 {deleted_ext} 条 >30 天的调用外部接口",
                    context={"table": "external_api_logs", "deleted": deleted_ext, "days": 30},
                )
        except Exception as e:
            print(f"[external_api_log_gc] 异常(忽略): {e}")
        # AI/LLM 调用记录 GC:30 天
        try:
            from db import ai_api_call_crud as _ai_crud
            deleted_ai = await _ai_crud.delete_ai_api_calls_older_than(days=30)
            if deleted_ai:
                print(f"[ai_api_call_gc] 清理 {deleted_ai} 条 >30 天的 AI 调用记录")
                event_service.log_event(
                    severity="info",
                    category="gc.cleanup",
                    message=f"清理 {deleted_ai} 条 >30 天的 AI 调用记录",
                    context={"table": "ai_api_calls", "deleted": deleted_ai, "days": 30},
                )
        except Exception as e:
            print(f"[ai_api_call_gc] 异常(忽略): {e}")
        # 客户文件原件 GC:到期(file_keep_until)删磁盘原件,DB/OCR 文本保留
        try:
            from db import customer_file_crud as _cfc
            expired = await _cfc.list_expired_local_files()
            cleaned_files = 0
            for ef in expired:
                abs_path = os.path.join(OUTPUT_DIR, ef["local_path"] or "")
                try:
                    if ef["local_path"] and os.path.exists(abs_path):
                        os.remove(abs_path)
                except OSError:
                    pass  # Windows 句柄占用等,下个周期重试
                else:
                    await _cfc.clear_file_local(ef["id"])
                    cleaned_files += 1
            if cleaned_files:
                print(f"[customer_file_gc] 清理 {cleaned_files} 份到期原件(DB/OCR 保留)")
                event_service.log_event(
                    severity="info",
                    category="gc.cleanup",
                    message=f"清理 {cleaned_files} 份到期客户文件原件",
                    context={"table": "customer_files", "deleted": cleaned_files},
                )
        except Exception as e:
            print(f"[customer_file_gc] 异常(忽略): {e}")
        # 留底检测原件预览缓存 GC:20 天保留(按 mtime)
        try:
            removed_preview = file_fetcher.sweep_preview_cache(
                ARCHIVE_PREVIEW_CACHE_DIR, ARCHIVE_PREVIEW_CACHE_MAX_AGE_SEC)
            if removed_preview:
                print(f"[archive_preview_gc] 清理 {removed_preview} 份 >20 天的原件预览缓存")
                event_service.log_event(
                    severity="info",
                    category="gc.cleanup",
                    message=f"清理 {removed_preview} 份 >20 天的留底原件预览缓存",
                    context={"dir": "temp/archive_preview_cache", "deleted": removed_preview, "days": 20},
                )
        except Exception as e:
            print(f"[archive_preview_gc] 异常(忽略): {e}")
        await asyncio.sleep(inmem_gc_every)


# ====================== 文件解析（URL → 摘要） ======================

class FileSummaryPayload(BaseModel):
    url: str
    progress_name: str


@app.post("/api/file-summary")
async def file_summary(payload: FileSummaryPayload):
    """同步：URL → 下载 → 抽取文本 → LLM 摘要+相关性判断 → 入库 → 返回完整结果。

    - progress_name 必填：用户描述当前进展（如"美国EB5-资金来源证明"），AI 会判断文件是否属于该进展
    - 仅支持 http/https URL
    - 下载上限 50 MB
    - 支持 PDF/PNG/JPG/JPEG/BMP/TIFF/WEBP/DOC/DOCX
    """
    url = (payload.url or "").strip()
    progress_name = (payload.progress_name or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    if not progress_name:
        raise HTTPException(status_code=400, detail="进展名称不能为空")

    t_started = time.time()
    local_path = None
    err_msg = None
    record_payload = {"url": url, "progress_name": progress_name, "status": "error"}

    try:
        # 1) 下载
        try:
            local_path, filename, mime_type = await file_fetcher.fetch_url_to_temp(url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except file_fetcher.FileTooLargeError as e:
            raise HTTPException(status_code=413, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"下载失败: {e}")

        record_payload["filename"] = filename
        record_payload["mime_type"] = mime_type

        if not file_fetcher.is_supported_extension(filename):
            raise HTTPException(
                status_code=400,
                detail=file_fetcher.get_unsupported_hint(filename)
            )

        # 2) 抽取文本
        try:
            extracted = await text_extractor.extract_text(local_path, mime_type)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"文本抽取失败: {e}")

        text = extracted["text"]
        if not text or not text.strip():
            raise HTTPException(status_code=502, detail="文件内容为空或无法识别（OCR/抽取后无文字）")

        record_payload["source"] = extracted["source"]
        record_payload["page_count"] = extracted["page_count"]
        record_payload["char_count"] = extracted["char_count"]
        record_payload["extracted_text"] = text

        # 3) LLM 摘要 + 相关性判断
        try:
            summary_result = await asyncio.to_thread(
                llm_service.summarize_text, text, progress_name, task_id=f"summary:{progress_name}"
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"AI 分析失败: {e}")

        record_payload["title"] = summary_result.get("title")
        record_payload["summary"] = summary_result.get("summary")
        record_payload["key_points"] = summary_result.get("key_points")
        record_payload["doc_category"] = summary_result.get("doc_category")
        record_payload["relevance"] = summary_result.get("relevance")
        record_payload["relevance_score"] = summary_result.get("relevance_score")
        record_payload["relevance_reason"] = summary_result.get("relevance_reason")
        record_payload["status"] = "done"
        record_payload["elapsed_sec"] = round(time.time() - t_started, 2)

        # 4) 入库 + 返回
        record = await summary_crud.create(record_payload)
        return record

    except HTTPException as he:
        # 失败也写一条 error 记录（便于排查）
        err_msg = he.detail if isinstance(he.detail, str) else str(he.detail)
        record_payload["error_msg"] = err_msg
        record_payload["status"] = "error"
        record_payload["elapsed_sec"] = round(time.time() - t_started, 2)
        try:
            await summary_crud.create(record_payload)
        except Exception as save_err:
            print(f"[file_summary] 写入错误记录失败（忽略）: {save_err}")
        raise

    finally:
        # 删临时下载文件（不论成败）
        if local_path:
            file_fetcher.cleanup_temp_file(local_path)


@app.get("/api/summaries")
async def list_summaries(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """摘要历史列表（不返 extracted_text，按时间倒序）。"""
    items = await summary_crud.list_summaries(limit=limit, offset=offset)
    return {"items": items, "total": len(items)}


@app.get("/api/summaries/{summary_id}")
async def get_summary(summary_id: int):
    """摘要详情（含 extracted_text）。"""
    s = await summary_crud.get_by_id(summary_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"摘要 {summary_id} 不存在")
    return s


@app.delete("/api/summaries/{summary_id}")
async def delete_summary(summary_id: int):
    """删除一条文件解析摘要记录。

    路径参数:
        summary_id - summaries 表主键

    返回:
        {"deleted": True}

    错误:
        404 - 该记录不存在
    """
    ok = await summary_crud.delete(summary_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"摘要 {summary_id} 不存在")
    return {"deleted": True}


# ==================== 文件留底检测 ====================

# ---- 业务接口(阶段三):增量复用 + 业务字段透传 ----

class BusinessClientPayload(BaseModel):
    client_code: str = Field(..., description="客户编码,业务方稳定客户 ID,系统按该字段 upsert clients 表")
    name: str = Field(..., description="客户姓名,用于业务上下文和客户档案展示")


class BusinessProgressPayload(BaseModel):
    progress_oid: str = Field(..., description="进展 OID,业务方稳定进展标识;(client_id, progress_oid) 组合唯一")
    handler: Optional[str] = Field(None, description="办理人,归属于当前进展包,同客户不同进展可不同")
    project_name: Optional[str] = Field(None, description="项目名称,如:新加坡家办")
    project_code: Optional[str] = Field(None, description="项目编码,如:P001")
    project_detail_name: Optional[str] = Field(None, description="项目详情名称,如:架构设计")
    project_detail_code: Optional[str] = Field(None, description="项目详情编码,如:PD001")
    progress_name: Optional[str] = Field(None, description="进展名称,如:递交后进展中")


class BusinessItemPayload(BaseModel):
    file_id: str = Field(..., description="业务方文件稳定 ID,增量复用 key;同一 progress 下相同 file_id 会复用历史结果")
    filename: Optional[str] = Field(None, description="文件名,可选;若为空系统尝试从 URL/上传文件名解析")
    url: str = Field(..., description="文件访问地址,支持 http/https;可为 OSS 临时签名地址")


class BusinessBatchPayload(BaseModel):
    """业务接口 JSON 入口提交体。"""
    criteria: str = Field(..., description="审核标准/判定提示词;业务审核 tab 会根据客户/项目/阶段自动预填,也可手动调整")
    stage: str = Field("post_submit", description="审核阶段:pre_submit=递交前,post_submit=递交后;默认 post_submit")
    client: BusinessClientPayload = Field(..., description="客户信息")
    progress: BusinessProgressPayload = Field(..., description="进展包信息")
    items: list[BusinessItemPayload] = Field(..., description="待检测文件列表,至少 1 个,最多 MAX_FILES_PER_BATCH 个")


# ---- 文件留底检测:Response Models(用于 Swagger UI Schema 中文说明) ----

class ArchiveDetectBusinessSubmitResponse(BaseModel):
    """业务审核接口提交后立即返回的批次创建结果(含增量复用统计)。"""
    batch_id: str = Field(..., description="批次ID,后续轮询使用")
    progress_id: int = Field(..., description="进展包数据库ID(关联 client_id + progress_oid 唯一)")
    total_files: int = Field(..., description="本次提交的文件总数")
    reused_count: int = Field(..., description="复用历史结果的文件数(同 file_id 命中,不走 OCR/LLM)")
    new_count: int = Field(..., description="需重新检测的文件数(走完整 OCR/LLM 流水线)")
    queue_depth: int = Field(0, description="提交时全局文件队列深度(含本批),可用于估算等待时间")


class ArchiveDetectClientInfo(BaseModel):
    """批次关联的客户简要信息。"""
    id: int = Field(..., description="客户表主键 ID")
    client_code: str = Field(..., description="客户编码(业务方稳定 ID,upsert key)")
    name: str = Field(..., description="客户姓名")


class ArchiveDetectProgressInfo(BaseModel):
    """批次关联的进展包信息(业务字段透传)。"""
    id: int = Field(..., description="进展包数据库 ID")
    client_id: int = Field(..., description="所属客户 ID")
    handler: Optional[str] = Field(None, description="办理人(进展包属性,同客户不同进展可不同)")
    project_name: Optional[str] = Field(None, description="项目名称(如:新加坡家办)")
    project_code: Optional[str] = Field(None, description="项目编码(如:P001)")
    project_detail_name: Optional[str] = Field(None, description="项目详情名称(如:架构设计)")
    project_detail_code: Optional[str] = Field(None, description="项目详情编码(如:PD001)")
    progress_oid: str = Field(..., description="进展 OID(业务方稳定标识,(client_id, progress_oid) 唯一)")
    progress_name: Optional[str] = Field(None, description="进展名称(如:递交后进展中)")


class ArchiveDetectFileItem(BaseModel):
    """单个文件检测结果。

    业务模式有 progress_id/file_id/version/is_reused 等字段;匿名模式这些为 null。
    """
    id: Optional[int] = Field(None, description="文件记录数据库 ID;运行中内存态可能为空,DB 回落/完成后有值")
    idx: int = Field(..., description="在 batch 内的顺序号(0-based)")
    progress_id: Optional[int] = Field(None, description="所属进展包 ID(仅业务模式)")
    file_id: Optional[str] = Field(None, description="业务方传入的文件稳定 ID(增量复用 key,仅业务模式)")
    version: Optional[int] = Field(None, description="该 file_id 的检测版本号(严格复用模式恒为 1)")
    source_url: Optional[str] = Field(None, description="URL 模式下的原始 OSS 地址")
    filename: Optional[str] = Field(None, description="文件名")
    mime_type: Optional[str] = Field(None, description="MIME 类型,如 application/pdf")
    page_count: Optional[int] = Field(None, description="文档页数(PDF/docx)")
    char_count: Optional[int] = Field(None, description="OCR 抽取后的字符数")
    is_archival: Optional[bool] = Field(None, description="(旧字段)= (verdict == 'match'),向后兼容")
    confidence: Optional[int] = Field(None, description="(旧字段)= match_score,向后兼容,0-100")
    verdict: Optional[str] = Field(None, description="三态判定:match(符合)/partial(部分符合)/mismatch(不符合)")
    match_score: Optional[int] = Field(None, description="匹配度 0-100(LLM 量化结果)")
    reason: Optional[str] = Field(None, description="LLM 判断依据,30-120 字(已脱敏)")
    key_points: list[str] = Field(default_factory=list, description="LLM 提取的 3-6 条关键要点(已脱敏)")
    doc_category: Optional[str] = Field(None, description="文档分类,如 'A-护照'/'G-批复函'(公司分类术语)")
    status: str = Field(..., description="状态机:pending/fetching/ocr/llm/done/error")
    error_msg: Optional[str] = Field(None, description="error 状态时的失败原因")
    elapsed_sec: Optional[float] = Field(None, description="单文件处理耗时秒数(复用项为 0)")
    is_reused: bool = Field(False, description="本次是否复用了历史结果(true=未走 OCR/LLM)")


class ArchiveDetectBatchResponse(BaseModel):
    """批次详情(GET /{batch_id} 通用返回)。

    业务模式有 client/progress/reused_count/new_count 字段;匿名模式这些为 null。
    """
    batch_id: str = Field(..., description="批次 ID")
    user_prompt: Optional[str] = Field(None, description="提交时的判定标准(criteria 同义)")
    criteria: Optional[str] = Field(None, description="判定标准(业务模式专用字段名,与 user_prompt 等价)")
    source_kind: str = Field(..., description="来源:upload(匿名上传)/url(匿名URL)/batch(业务模式)")
    total_files: int = Field(..., description="本批次文件总数")
    done_files: int = Field(..., description="已完成数(成功+失败都算)")
    status: str = Field(..., description="批次状态:running(进行中)/done(完成)/error(失败)")
    error: Optional[str] = Field(None, description="batch 级错误(极少触发)")
    overall_verdict: Optional[str] = Field(None, description="批次总体判断:match/partial/mismatch(全部 done 后生成)")
    overall_score: Optional[int] = Field(None, description="批次总体匹配度 0-100(所有 done 文件的平均 match_score)")
    overall_reason: Optional[str] = Field(None, description="批次总体说明 80-200 字(LLM 生成,失败兜底规则文本)")
    client: Optional[ArchiveDetectClientInfo] = Field(None, description="客户信息(仅业务模式)")
    progress: Optional[ArchiveDetectProgressInfo] = Field(None, description="进展包信息(仅业务模式)")
    reused_count: Optional[int] = Field(None, description="本批次复用历史结果的文件数(仅业务模式)")
    new_count: Optional[int] = Field(None, description="本批次新检测的文件数(仅业务模式)")
    files: list[ArchiveDetectFileItem] = Field(default_factory=list, description="文件级检测结果数组,按 idx 排序")
    created_at: str = Field(..., description="批次创建时间(YYYY-MM-DD HH:MM:SS)")
    updated_at: Optional[str] = Field(None, description="批次最后更新时间;运行中内存态可能为空")


class ArchiveDetectHistoryItem(BaseModel):
    """历史列表项(不含 files 详情)。"""
    batch_id: str = Field(..., description="批次 ID")
    user_prompt: Optional[str] = Field(None, description="提交时的判定标准")
    source_kind: str = Field(..., description="来源:upload/url/batch")
    total_files: int = Field(..., description="文件总数")
    done_files: int = Field(..., description="已完成数")
    status: str = Field(..., description="批次状态")
    error: Optional[str] = Field(None, description="batch 级错误")
    overall_verdict: Optional[str] = Field(None, description="批次总体判断")
    overall_score: Optional[int] = Field(None, description="批次总体匹配度 0-100")
    overall_reason: Optional[str] = Field(None, description="批次总体说明")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")


class ArchiveDetectHistoryResponse(BaseModel):
    """历史批次列表返回。"""
    items: list[ArchiveDetectHistoryItem] = Field(..., description="历史批次数组,按创建时间倒序")
    total: int = Field(..., description="返回条数(实际数量,非数据库总数)")


class ArchiveDetectDeleteResponse(BaseModel):
    """删除批次返回。"""
    deleted: bool = Field(..., description="是否删除成功(true=已删除)")


class ArchiveDetectRecheckPayload(BaseModel):
    """重新审核请求体。"""
    criteria: str = Field(default="", description="重新审核使用的最新判定提示词；regenerate_criteria=true 时可忽略")
    stage: Optional[str] = Field(None, description="审核阶段:pre_submit=递交前,post_submit=递交后;快速检测可为空")
    regenerate_criteria: bool = Field(False, description="是否根据批次当前 client/progress/stage 重新生成新规则 criteria；为 true 时忽略 criteria 字段")


class ArchiveDetectRecheckResponse(BaseModel):
    """重新审核提交后返回。"""
    batch_id: str = Field(..., description="新建的重新审核批次 ID")
    source_batch_id: str = Field(..., description="原批次 ID")
    total_files: int = Field(..., description="参与重新审核的文件总数")
    ai_only_count: int = Field(..., description="已有 OCR 文本、只重新跑 AI 的文件数")
    ocr_count: int = Field(..., description="缺少 OCR 文本、需要重新下载/OCR 的文件数")
    mode: str = Field(..., description="原批次模式:business=业务审核,quick=快速检测")


class ArchiveDetectRerunResponse(BaseModel):
    """原地重跑提交后返回。"""
    batch_id: str = Field(..., description="重跑的批次 ID(原地,不新建)")
    total_files: int = Field(..., description="批次文件总数")
    ai_only_count: int = Field(..., description="已有 OCR 文本、只重新跑 AI 的文件数")
    ocr_count: int = Field(..., description="缺少 OCR 文本、需要重新下载/OCR 的文件数")
    skipped_count: int = Field(..., description="已有 AI 结果、本次跳过的文件数")
    mode: str = Field(..., description="批次模式:business=业务审核,quick=快速检测,no-op=无需重跑")


class ArchiveDetectAdminBatchItem(BaseModel):
    """后台管理批次列表项。"""
    batch_id: str = Field(..., description="批次 ID")
    source_kind: str = Field(..., description="来源类型:upload/url/batch/recheck")
    status: str = Field(..., description="批次状态:running/done/error")
    user_prompt: Optional[str] = Field(None, description="判定提示词/审核标准(重审预填用)")
    total_files: int = Field(..., description="文件总数")
    done_files: int = Field(..., description="已完成文件数")
    overall_verdict: Optional[str] = Field(None, description="批次总体判断")
    overall_score: Optional[int] = Field(None, description="批次总体匹配度")
    overall_reason: Optional[str] = Field(None, description="批次总体说明")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    client: Optional[ArchiveDetectClientInfo] = Field(None, description="客户信息(业务批次才有)")
    progress: Optional[ArchiveDetectProgressInfo] = Field(None, description="进展包信息(业务批次才有)")


class ArchiveDetectAdminBatchListResponse(BaseModel):
    """后台管理批次列表返回。"""
    items: list[ArchiveDetectAdminBatchItem] = Field(..., description="批次列表")
    total: int = Field(..., description="符合筛选条件的总条数")


class ArchiveDetectDailyReportBucket(BaseModel):
    """每日报告统计桶(客户维度或全量合计)。"""
    client_id: Optional[int] = Field(None, description="客户 ID(全量合计/无客户桶为 null)")
    client_code: str = Field("", description="客户编码")
    name: str = Field("", description="客户姓名")
    batches: int = Field(..., description="批次数")
    files: int = Field(..., description="文件总数")
    match: int = Field(..., description="总体判断=符合 的批次数")
    partial: int = Field(..., description="总体判断=部分符合 的批次数")
    mismatch: int = Field(..., description="总体判断=不符合 的批次数")
    other: int = Field(..., description="已完成但无总体判断 的批次数")
    error: int = Field(..., description="识别失败 的批次数")
    in_progress: int = Field(..., description="进行中 的批次数")
    avg_score: Optional[float] = Field(None, description="已完成批次总体匹配度均值(无则 null)")
    clients: int = Field(0, description="涉及客户数(仅 totals 合计桶有意义)")


class ArchiveDetectDailyReportResponse(BaseModel):
    """每日留底检测报告返回。"""
    date: str = Field(..., description="统计日期 YYYY-MM-DD(按批次创建时间)")
    totals: ArchiveDetectDailyReportBucket = Field(..., description="当天全量合计(多一个 clients 字段)")
    clients: list[ArchiveDetectDailyReportBucket] = Field(..., description="按客户分组的统计,批次数降序")


class ArchiveDetectAdminProgressItem(BaseModel):
    """后台管理进展包列表项。"""
    id: int = Field(..., description="进展包数据库 ID")
    client_id: int = Field(..., description="所属客户 ID")
    handler: Optional[str] = Field(None, description="办理人")
    project_name: Optional[str] = Field(None, description="项目名称")
    project_code: Optional[str] = Field(None, description="项目编码")
    project_detail_name: Optional[str] = Field(None, description="项目详情名称")
    project_detail_code: Optional[str] = Field(None, description="项目详情编码")
    progress_oid: str = Field(..., description="进展 OID")
    progress_name: Optional[str] = Field(None, description="进展名称")
    client: ArchiveDetectClientInfo = Field(..., description="客户信息")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")


class ArchiveDetectAdminProgressListResponse(BaseModel):
    """后台管理进展包列表返回。"""
    items: list[ArchiveDetectAdminProgressItem] = Field(..., description="进展包列表")
    total: int = Field(..., description="符合筛选条件的总条数")


class ArchiveDetectAdminFileDetail(ArchiveDetectFileItem):
    """后台管理单文件详情(含 OCR 文本)。"""
    batch_id: str = Field(..., description="所属批次 ID")
    ocr_text: Optional[str] = Field(None, description="OCR 识别文字(已脱敏),仅详情接口返回")
    client: Optional[ArchiveDetectClientInfo] = Field(None, description="客户信息")
    progress: Optional[ArchiveDetectProgressInfo] = Field(None, description="进展包信息")


class FileInfoListItem(ArchiveDetectFileItem):
    """文件信息列表项:含文件本身 + 批次/客户/进展上下文。"""
    batch_id: str = Field(..., description="所属批次 ID")
    created_at: str = Field(..., description="文件创建时间")
    updated_at: Optional[str] = Field(None, description="文件最后更新时间")
    client: Optional[ArchiveDetectClientInfo] = Field(None, description="客户信息(仅业务模式)")
    progress: Optional[ArchiveDetectProgressInfo] = Field(None, description="进展包信息(仅业务模式)")


class FileInfoListResponse(BaseModel):
    """文件信息列表返回。"""
    items: list[FileInfoListItem] = Field(..., description="文件列表")
    total: int = Field(..., description="符合筛选条件的总条数")


# 注意:history 必须声明在 /{batch_id} 之前,避免路径参数吞掉 "history"
@app.get(
    "/api/archive-detect/history",
    tags=["文件留底检测"],
    summary="历史批次列表",
    response_model=ArchiveDetectHistoryResponse,
)
async def archive_detect_history(
    limit: int = Query(200, ge=1, le=500, description="返回最近多少条历史批次,范围 1-500,默认 200")
):
    """历史 batch 列表（不含 files 详情）。"""
    items = await archive_detect_service.list_history(limit=limit)
    return {"items": items, "total": len(items)}


# ==================== 后台管理/识别进度监控(只读) ====================

class LoginPayload(BaseModel):
    username: str = Field(..., description="账号")
    password: str = Field(..., description="密码")


@app.post(
    "/api/auth/login",
    tags=["鉴权"],
    summary="登录 - 账号密码校验",
    dependencies=[],   # 白名单:免 Bearer
)
async def login(payload: LoginPayload):
    """员工账号密码登录。

    校验 config.json.auth 的 admin_user/admin_password,通过后返回 biz_api_key
    作为会话 token,前端存 localStorage,后续请求带 Authorization: Bearer <token>。
    """
    from middleware.auth_middleware import get_admin_credentials, get_biz_api_key, reset_auth_cache
    reset_auth_cache()  # 每次登录重读配置,使 config.json 改动即时生效
    admin_user, admin_password = get_admin_credentials()
    biz_key = get_biz_api_key()

    user = (payload.username or "").strip()
    pwd = (payload.password or "").strip()
    if not admin_user or not admin_password:
        return {"ok": False, "detail": "服务端未配置管理员账号"}
    if user == admin_user and pwd == admin_password:
        return {"ok": True, "token": biz_key, "detail": "登录成功"}
    return {"ok": False, "detail": "账号或密码错误"}


@app.get(
    "/api/healthz",
    tags=["运维"],
    summary="健康检查 - DB + 队列",
    dependencies=[],   # 白名单:免 Bearer
)
async def healthz():
    """探针:DB 可达。供 nginx/k8s/外部监控调用。

    任一组件异常返回 503,nginx 健康检查会摘流。
    OCR Worker 是独立 systemd 进程(doc-review-worker@{1,2,3}),
    各自有独立 health,不在本主进程探针范围内。
    """
    from sqlalchemy import text
    from db.engine import async_engine
    problems = []

    # 1) DB ping
    db_error = None
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_error = f"{e.__class__.__name__}: {e}"
        problems.append(f"db: {db_error}")

    # 2) Worker 状态(独立进程,通过 DB 间接观测;只在 stats 里报告,不作为 healthz fail 条件)
    stats = archive_detect_service.queue_stats()

    # DB 异常时记一条事件,但限频(避免 healthz 高频探针把事件表写爆)
    # _last_db_error_event_ts 在模块级初始化为 0.0;5 分钟内同一类异常只记一次
    if db_error is not None:
        global _last_db_error_event_ts
        now_ts = time.time()
        if now_ts - _last_db_error_event_ts > 300:
            _last_db_error_event_ts = now_ts
            event_service.log_event(
                event_service.ERROR,
                event_service.CATEGORY_DB_ERROR,
                f"healthz DB ping 失败:{db_error[:200]}",
                context={"db_error": db_error[:300]},
            )

    if problems:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "problems": problems, "queue": stats},
        )
    return {"status": "ok", "queue": stats}


@app.get(
    "/api/archive-detect/admin/queue-stats",
    tags=["文件留底检测"],
    summary="后台管理 - 文件级队列实时统计",
)
async def archive_detect_admin_queue_stats():
    """实时返回业务审核文件级队列的运行指标。

    用于:监控当前积压、worker 数、LLM 信号量余量、可用内存。
    返回值字段:
      - queue_depth: DB 中待处理(pending+leased+fetching+ocr+llm)的文件数
      - queue_max:  队列上限(超过此值 submit 会 429)
      - workers:    Worker 是独立 systemd 进程,这里返回 "see-systemd-status";真实数看 doc-review-worker@{1,2,3}
      - in_flight_batches: 还在等 worker 处理完的批次数(主进程 finalize 协程数)
      - llm_semaphore_avail: LLM 信号量剩余槽位
      - free_memory_mb: 系统可用内存 MB(若安装了 psutil)
    """
    stats = await archive_detect_service.queue_stats_async()
    # 可用内存:有 psutil 就上报,没有就略过
    try:
        import psutil
        stats["free_memory_mb"] = int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        stats["free_memory_mb"] = None
    return stats


# ==================== 业务事件流 ====================

@app.get(
    "/api/admin/events",
    tags=["运维"],
    summary="事件流查询",
)
async def admin_list_events(
    severity: Optional[str] = Query(None, description="逗号分隔,如 warn,error,critical;不填查全部"),
    category: Optional[str] = Query(None, description="逗号分隔的 category"),
    batch_id: Optional[str] = Query(None, description="按 batch_id 查相关事件"),
    since: Optional[str] = Query(None, description="起始时间 YYYY-MM-DD HH:MM:SS,含此时刻"),
    until: Optional[str] = Query(None, description="结束时间 YYYY-MM-DD HH:MM:SS,不含此时刻"),
    limit: int = Query(50, ge=1, le=200, description="单页条数 1-200"),
    offset: int = Query(0, ge=0),
):
    """业务事件流查询。
    默认返回最近 24h、所有级别;前端通常筛 severity=warn,error,critical 隐藏 info 噪声。
    """
    from db import event_crud as _event_crud

    def _parse_csv(s: Optional[str]) -> Optional[list[str]]:
        if not s:
            return None
        out = [x.strip() for x in s.split(",") if x.strip()]
        return out or None

    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise HTTPException(status_code=400, detail=f"非法时间格式: {s}")

    items, total = await _event_crud.list_events(
        severities=_parse_csv(severity),
        categories=_parse_csv(category),
        batch_id=batch_id,
        since=_parse_dt(since),
        until=_parse_dt(until),
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total}


@app.get(
    "/api/admin/events/categories",
    tags=["运维"],
    summary="事件流 - 已出现过的 category 列表",
)
async def admin_event_categories():
    """前端筛选下拉用。包括预定义常量 + 数据库里实际出现过的 category。"""
    from db import event_crud as _event_crud
    # 预定义常量(确保前端能看到所有可能值,即使表里还没出现过)
    predefined = [
        event_service.CATEGORY_SERVICE_START,
        event_service.CATEGORY_SERVICE_STOP,
        event_service.CATEGORY_BATCH_SUBMIT,
        event_service.CATEGORY_BATCH_QUEUE_FULL,
        event_service.CATEGORY_BATCH_DONE,
        event_service.CATEGORY_FILE_FAILED,
        event_service.CATEGORY_FILE_OCR_SLOW,
        event_service.CATEGORY_FILE_OCR_SAMPLED,
        event_service.CATEGORY_LLM_TIMEOUT,
        event_service.CATEGORY_DB_ERROR,
        event_service.CATEGORY_WORKER_CRASH,
        event_service.CATEGORY_MEMORY_LOW,
    ]
    db_seen = await _event_crud.distinct_categories()
    merged = sorted(set(predefined) | set(db_seen))
    return {"categories": merged}


@app.get(
    "/api/admin/request-logs",
    tags=["运维"],
    summary="外部请求日志查询",
)
async def admin_list_request_logs(
    source: Optional[str] = Query(None, description="business/admin/poll/other"),
    method: Optional[str] = Query(None, description="GET/POST"),
    path: Optional[str] = Query(None, description="路径片段模糊匹配"),
    since: Optional[str] = Query(None, description="起始时间 YYYY-MM-DD HH:MM:SS"),
    until: Optional[str] = Query(None, description="结束时间"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from db import request_log_crud as _rlc

    def _parse_dt(s):
        if not s:
            return None
        from datetime import datetime as _dt
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return _dt.strptime(s, fmt)
            except ValueError:
                continue
        raise HTTPException(status_code=400, detail=f"非法时间格式: {s}")

    items, total = await _rlc.list_request_logs(
        source=source,
        method=method,
        path_contains=path,
        since=_parse_dt(since),
        until=_parse_dt(until),
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total}


@app.get(
    "/api/admin/external-api-logs",
    tags=["运维"],
    summary="调用外部接口查询",
)
async def admin_list_external_api_logs(
    service: Optional[str] = Query(None, description="refresh_url | llm"),
    status: Optional[str] = Query(None, description="ok | error"),
    batch_id: Optional[str] = Query(None, description="关联批次 ID 模糊匹配"),
    since: Optional[str] = Query(None, description="起始时间 YYYY-MM-DD HH:MM:SS"),
    until: Optional[str] = Query(None, description="结束时间"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from db import external_api_log_crud as _ealc

    def _parse_dt(s):
        if not s:
            return None
        from datetime import datetime as _dt
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return _dt.strptime(s, fmt)
            except ValueError:
                continue
        raise HTTPException(status_code=400, detail=f"非法时间格式: {s}")

    items, total = await _ealc.list_external_api_logs(
        service=service, status=status, batch_id=batch_id,
        since=_parse_dt(since), until=_parse_dt(until),
        limit=limit, offset=offset,
    )
    return {"items": items, "total": total}


@app.get(
    "/api/admin/ai-api-calls",
    tags=["运维"],
    summary="AI/LLM API 调用记录查询",
)
async def admin_list_ai_api_calls(
    operation: Optional[str] = Query(None, description="LLM wrapper/操作名,如 detect_archival"),
    model: Optional[str] = Query(None, description="模型 ID 模糊匹配"),
    status: Optional[str] = Query(None, description="ok | error"),
    batch_id: Optional[str] = Query(None, description="关联批次 ID 模糊匹配"),
    file_id: Optional[str] = Query(None, description="关联文件编码模糊匹配"),
    client_code: Optional[str] = Query(None, description="客户编码模糊匹配"),
    task_id: Optional[str] = Query(None, description="任务/摘要 ID 模糊匹配"),
    since: Optional[str] = Query(None, description="起始时间 YYYY-MM-DD HH:MM:SS"),
    until: Optional[str] = Query(None, description="结束时间"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from db import ai_api_call_crud as _crud

    def _parse_dt(s):
        if not s:
            return None
        from datetime import datetime as _dt
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return _dt.strptime(s, fmt)
            except ValueError:
                continue
        raise HTTPException(status_code=400, detail=f"非法时间格式: {s}")

    items, total = await _crud.list_ai_api_calls(
        operation=operation, model=model, status=status,
        batch_id=batch_id, file_id=file_id, client_code=client_code, task_id=task_id,
        since=_parse_dt(since), until=_parse_dt(until),
        limit=limit, offset=offset,
    )
    return {"items": items, "total": total}


@app.get(
    "/api/admin/ai-api-calls/{row_id}",
    tags=["运维"],
    summary="AI/LLM API 调用记录详情(prompt/response 全文)",
)
async def admin_get_ai_api_call(row_id: int):
    from db import ai_api_call_crud as _crud
    detail = await _crud.get_ai_api_call_detail(row_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return detail


# ==================== 信息提取:证件提取规则 ====================
# 规则已迁至代码常量 backend/extract_rules.py(改规则=改该文件+重启 worker)。
# 原 draft/activate/disable 生命周期端点已移除;提取结果(doc_extract_results)的
# 人工复核编辑闭环不受影响,见下方「客户画像」及「复核」相关端点。


# ==================== 客户画像:业务方接口文件清单导入 ====================
# 从 getAfterCustomerAllFiles 拉客户文件清单 -> 预览勾选 -> 全量 OCR 入客户文件库
# -> 筛 4 类证件 -> 规则提取 -> 客户档案。方案见 docs/09-客户画像-Excel导入方案.md。


class ProfileRemotePreviewReq(BaseModel):
    customer_code: str = Field("", description="客户编号;留空查最近 100 条(接口固定)")
    operation_user: str = Field("Jason邹启", description="业务方接口操作人身份")


class ProfileRemoteImportReq(BaseModel):
    customer_code: str = Field("", description="客户编号;留空查最近 100 条(接口固定)")
    operation_user: str = Field("Jason邹启", description="业务方接口操作人身份")
    customer_names: list[str] = Field(..., description="要导入的客户姓名(预览勾选项)")


@app.post(
    "/api/profile/import-remote/preview",
    tags=["客户画像"],
    summary="从业务方接口拉客户文件清单预览(按客户合并,不写库)",
)
async def profile_import_remote_preview(req: ProfileRemotePreviewReq):
    import profile_import_service
    try:
        customers = await profile_import_service.fetch_after_customer_files(
            customer_code=req.customer_code, operation_user=req.operation_user)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"客户文件清单查询失败: {e}")
    out = []
    for g in profile_import_service.group_api_customers(customers):
        m = profile_import_service.parse_api_manifest(g["customer"])
        out.append({
            "customer_name": g["customer_name"],
            "customer_code": g["customer_code"],
            "crm_oid": g["crm_oid"],
            "entry_count": g["entry_count"],
            "file_count": len(m["files"]),
            "projects": m["projects"],
            "skipped_junk": m["skipped_junk"],
            "duplicates": m["duplicates"],
        })
    return {"customers": out}


@app.post(
    "/api/profile/import-remote",
    tags=["客户画像"],
    summary="按选中客户创建导入任务(后台串行跑 OCR/分类/提取)",
)
async def profile_import_remote(req: ProfileRemoteImportReq):
    import profile_import_service
    from db import customer_file_crud, profile_crud

    names = [n.strip() for n in req.customer_names if n and n.strip()]
    if not names:
        raise HTTPException(status_code=400, detail="未选择客户")
    try:
        customers = await profile_import_service.fetch_after_customer_files(
            customer_code=req.customer_code, operation_user=req.operation_user)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"客户文件清单查询失败: {e}")

    wanted = set(names)
    tasks = []
    skipped_running = []  # 该家庭已有 running 任务,按户跳过(防重复提交并发双建卡)
    for g in profile_import_service.group_api_customers(customers):
        name = g["customer_name"]
        if name not in wanted:
            continue
        m = profile_import_service.parse_api_manifest(g["customer"])
        if not m["files"]:
            continue
        # 主客户:老表软关联(不写字段);画像 v2 写独立 profile_* 表
        client = await crud.find_or_create_client(name)
        household = await profile_crud.get_or_create_household(
            name, legacy_client_id=client.id,
            customer_code=g["customer_code"] or None, crm_oid=g["crm_oid"] or None)
        if await customer_file_crud.has_running_task(household["id"]):
            skipped_running.append(name)
            continue
        task = await customer_file_crud.create_import_task(
            filename=f"接口导入-{name}", client_name=name,
            client_id=client.id, total_files=len(m["files"]),
            household_id=household["id"])
        counts = await customer_file_crud.upsert_task_files(
            task["id"], client.id, m["files"])
        # 项目案件壳同步建好(一个售后项目=一个案件),早于后台任务消除里程碑路由竞态
        await profile_crud.upsert_project_cases(household["id"], m["projects"])
        tasks.append({
            "task_id": task["id"], "client_name": name,
            "household_id": household["id"], "total_files": len(m["files"]),
            "new_files": counts["new"], "relinked_files": counts["relinked"],
            "project_count": len(m["projects"]),
        })

    if not tasks and not skipped_running:
        raise HTTPException(status_code=400, detail="选中客户在接口返回中无有效文件")
    if not tasks:
        return {"tasks": [], "skipped_running": skipped_running, "status": "skipped"}
    asyncio.create_task(profile_import_service.run_imports_sequential(
        [t["task_id"] for t in tasks]))
    return {"tasks": tasks, "skipped_running": skipped_running, "status": "running"}


@app.get(
    "/api/profile/tasks",
    tags=["客户画像"],
    summary="导入任务列表查询",
)
async def profile_task_list(
    status: Optional[str] = Query(None, description="状态筛选:running/done/error"),
    client_name: Optional[str] = Query(None, description="客户姓名模糊查询"),
    customer_code: Optional[str] = Query(None, description="客户编码模糊查询(按家庭 customer_code)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from db import customer_file_crud
    items, total = await customer_file_crud.list_import_tasks(
        status=status, client_name=client_name, customer_code=customer_code,
        limit=limit, offset=offset)
    return {"items": items, "total": total}


@app.get(
    "/api/profile/tasks/{task_id}",
    tags=["客户画像"],
    summary="导入任务详情(进度/计数)",
)
async def profile_task_get(task_id: int):
    from db import customer_file_crud
    row = await customer_file_crud.get_import_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return row


@app.post(
    "/api/profile/tasks/resume-stale",
    tags=["客户画像"],
    summary="恢复中断的导入任务:进程重启卡 running 的任务断点续跑(已完成文件不重跑)",
)
async def profile_tasks_resume_stale():
    """DB status='running' 但不在主进程活跃登记里的任务 = stale(进程重启杀了协程)。

    mark_task_active 同步执行:既排除真在跑的任务,又关掉双击/并发请求的竞态;
    后续 run_import(assume_active=True) 跑完在 finally 里 unmark。
    """
    from db import customer_file_crud
    import profile_import_service

    running = await customer_file_crud.list_running_import_tasks()
    ids = [t["id"] for t in running if profile_import_service.mark_task_active(t["id"])]
    if not ids:
        return {"resumed": [], "skipped_running": len(running)}
    asyncio.create_task(profile_import_service.run_imports_sequential(
        ids, resume=True, assume_active=True))
    event_service.log_event(
        severity="info",
        category="profile.import.resumed",
        message=f"恢复 {len(ids)} 个中断的画像导入任务",
        context={"task_ids": ids},
    )
    return {"resumed": ids, "skipped_running": len(running) - len(ids)}


def _remove_customer_file_disks(local_paths: list[str]) -> int:
    """连带删除磁盘原件(容错同 customer_file GC:Windows 句柄占用不阻塞响应)。"""
    removed = 0
    for p in local_paths:
        abs_path = os.path.join(OUTPUT_DIR, p)
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
                removed += 1
        except OSError:
            pass
    return removed


@app.delete(
    "/api/profile/tasks/{task_id}",
    tags=["客户画像"],
    summary="删除导入任务(任务有家庭时=删除画像:只删画像数据,文件/OCR/提取结果保留;无家庭时级联删任务级数据)",
)
async def profile_task_delete(task_id: int):
    from db import customer_file_crud
    deleted, local_paths, stats = await customer_file_crud.delete_import_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    removed = _remove_customer_file_disks(local_paths)
    event_service.log_event(
        severity="info",
        category="profile.import.deleted",
        message=f"删除画像数据(保留文件/OCR) 任务 #{task_id}" if stats.get("household_deleted")
                else f"删除画像导入任务 #{task_id}",
        context={"task_id": task_id, "local_files_removed": removed, **stats},
    )
    return {"deleted": True, "local_files_removed": removed, **stats}


@app.delete(
    "/api/profile/households/{household_id}",
    tags=["客户画像"],
    summary="删除画像(只删家庭画像数据:人员/字段/资产/案件;文件/OCR/提取结果/磁盘原件全保留)",
)
async def profile_household_delete(household_id: int):
    from db import customer_file_crud
    deleted, local_paths, stats = await customer_file_crud.delete_household_profile(household_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="家庭不存在")
    removed = _remove_customer_file_disks(local_paths)
    event_service.log_event(
        severity="info",
        category="profile.household.deleted",
        message=f"删除画像数据(保留文件/OCR) 家庭 #{household_id}",
        context={"household_id": household_id, "local_files_removed": removed, **stats},
    )
    return {"deleted": True, "local_files_removed": removed, **stats}


@app.post(
    "/api/profile/households/{household_id}/regenerate",
    tags=["客户画像"],
    summary="重新生成画像:复用当前任务原地重跑,已有 OCR 直接复用,缺 OCR 重识别,缺文件按文件编码重下载,按当前规则重分类/提取/归因",
)
async def profile_household_regenerate(household_id: int, task_id: Optional[int] = Query(None)):
    """家庭名下全部文件重跑一遍导入流水线(复用已有任务,不新建),画像域数据不清空。

    宿主任务:优先取前端传的 task_id(当前打开画像所在的任务,须属于本家庭),
    缺省取家庭最近一个任务;家庭一个任务都没有时兜底新建(正常不会发生)。
    语义落点全靠现有 run_import/upsert_task_files 行为:
    done+有 ocr_text 的行复用 OCR 只重分类/提取;error/pending 行自动重置重试;
    无本地原件按 file_code 刷新地址重下载。人工已确认/修正字段不会被覆盖。
    """
    from db import customer_file_crud, profile_crud
    import profile_import_service

    household = await profile_crud.get_household(household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="家庭不存在")
    if await customer_file_crud.has_running_task(household_id):
        raise HTTPException(status_code=409, detail="该家庭有正在运行的任务,请完成后再重新生成")

    files = await customer_file_crud.list_household_files(household_id)
    if not files:
        raise HTTPException(status_code=400, detail="家庭名下没有文件,无法重新生成")

    task = None
    if task_id is not None:
        task = await customer_file_crud.get_import_task(task_id)
        if task is None or task.get("household_id") != household_id:
            raise HTTPException(status_code=400, detail="任务不存在或不属于该家庭")
    else:
        task = await customer_file_crud.get_latest_task_for_household(household_id)
    if task is None:
        task = await customer_file_crud.create_import_task(
            filename=f"重新生成-{household['name']}", client_name=household["name"],
            client_id=household.get("legacy_client_id"), total_files=len(files),
            household_id=household_id)
    else:
        await customer_file_crud.reset_import_task(task["id"], total_files=len(files))
    counts = await customer_file_crud.upsert_task_files(
        task["id"], household.get("legacy_client_id"), files)
    asyncio.create_task(profile_import_service.run_import(task["id"]))
    event_service.log_event(
        severity="info",
        category="profile.import.regenerate",
        message=f"重新生成画像: 家庭 #{household_id} ({household['name']})",
        context={"household_id": household_id, "task_id": task["id"],
                 "total_files": len(files), **counts},
    )
    return {"task_id": task["id"], "household_id": household_id,
            "total_files": len(files), "new_files": counts["new"],
            "relinked_files": counts["relinked"], "status": "running"}


@app.post(
    "/api/profile/households/{household_id}/merge-duplicates",
    tags=["客户画像"],
    summary="扫描合并家庭内同名人员卡(name_folded 折叠分组;dry_run 只出计划;id_number 冲突组跳过)",
)
async def profile_household_merge_duplicates(
    household_id: int,
    dry_run: bool = Query(False, description="只返回分组计划不执行合并"),
):
    from db import customer_file_crud, profile_crud
    import profile_import_service

    household = await profile_crud.get_household(household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="家庭不存在")
    if await customer_file_crud.has_running_task(household_id):
        raise HTTPException(status_code=409, detail="该家庭有正在运行的任务,请完成后再合并")
    if dry_run:
        return {"household_id": household_id,
                "groups": await profile_crud.find_duplicate_person_groups(household_id)}
    result = await profile_import_service.run_merge_duplicate_persons(
        household_id, trigger="manual")
    return {"household_id": household_id, **result}


@app.post(
    "/api/profile/admin/merge-duplicates-all",
    tags=["客户画像"],
    summary="全量扫描合并同名人员卡(存量清理;跳过有 running 任务的家庭;dry_run 只出计划)",
)
async def profile_admin_merge_duplicates_all(
    dry_run: bool = Query(False, description="只返回分组计划不执行合并"),
):
    from db import customer_file_crud, profile_crud
    import profile_import_service

    results, skipped_running = [], []
    for household_id in await profile_crud.list_household_ids():
        if await customer_file_crud.has_running_task(household_id):
            skipped_running.append(household_id)
            continue
        if dry_run:
            groups = await profile_crud.find_duplicate_person_groups(household_id)
            if groups:
                results.append({"household_id": household_id, "groups": groups})
        else:
            r = await profile_import_service.run_merge_duplicate_persons(
                household_id, trigger="admin")
            if r["groups"]:
                results.append({"household_id": household_id, **r})
    return {"dry_run": dry_run, "households": results,
            "skipped_running": skipped_running}


class PersonMergePayload(BaseModel):
    keep_id: int = Field(..., description="保留的人员 profile_persons.id")
    drop_id: int = Field(..., description="合并掉的人员 profile_persons.id")
    keep_name: Optional[str] = Field(None, description="合并后保留的姓名;缺省=keep 人原名,可选 drop 人名")


@app.post(
    "/api/profile/persons/merge",
    tags=["客户画像"],
    summary="手动合并单对人员(字段仲裁:人工>AI、同 AI 晚者胜;文件/资产/归因一并重挂)",
)
async def profile_persons_merge(payload: PersonMergePayload):
    from db import profile_crud

    keep = await profile_crud.get_person(payload.keep_id)
    drop = await profile_crud.get_person(payload.drop_id)
    if not keep or not drop:
        raise HTTPException(status_code=404, detail="人员不存在")
    if keep["household_id"] != drop["household_id"]:
        raise HTTPException(status_code=400, detail="两人不属于同一家庭,禁止合并")
    try:
        result = await profile_crud.merge_persons(
            keep["household_id"], payload.keep_id, payload.drop_id,
            keep_name=payload.keep_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    event_service.log_event(
        event_service.INFO, event_service.CATEGORY_PROFILE_PERSON_MERGED,
        f"同名人员手动合并: person#{payload.drop_id} → person#{payload.keep_id}",
        context={"household_id": keep["household_id"], "trigger": "manual-single", **result})
    return result


@app.get(
    "/api/profile/tasks/{task_id}/files",
    tags=["客户画像"],
    summary="任务文件清单(状态/OCR 来源/分类结果;不含 OCR 全文)",
)
async def profile_task_files(
    task_id: int,
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    from db import customer_file_crud
    task = await customer_file_crud.get_import_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    items, total = await customer_file_crud.list_task_files(task_id, limit=limit, offset=offset)
    return {"items": items, "total": total}


@app.get(
    "/api/profile/files/{file_id}",
    tags=["客户画像"],
    summary="客户文件详情(含 OCR 全文;fresh=原文,reused=脱敏文本)",
)
async def profile_file_get(file_id: int):
    from db import customer_file_crud
    row = await customer_file_crud.get_customer_file(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return row


@app.get(
    "/api/profile/files/{file_id}/raw",
    tags=["客户画像"],
    summary="查看/下载原件(本地留存 30 天;已清理则按需重新下载)",
)
async def profile_file_raw(file_id: int):
    from db import customer_file_crud
    import profile_import_service
    from fastapi.responses import FileResponse
    row = await customer_file_crud.get_customer_file(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        abs_path, mime = await profile_import_service.ensure_local_file(row)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"原件获取失败: {e}")
    return FileResponse(abs_path, media_type=mime, filename=row.get("filename") or None)


# Office 原件在线预览:soffice 按需转 PDF,按 file_id 缓存
_OFFICE_PREVIEW_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}


@app.get(
    "/api/profile/files/{file_id}/preview-pdf",
    tags=["客户画像"],
    summary="Office 原件转 PDF 预览(soffice 按需转换+缓存;无 LibreOffice 返回 501)",
)
async def profile_file_preview_pdf(file_id: int):
    from db import customer_file_crud
    import profile_import_service
    import text_extractor
    from fastapi.responses import FileResponse
    row = await customer_file_crud.get_customer_file(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = os.path.splitext(row.get("filename") or "")[1].lower()
    if ext not in _OFFICE_PREVIEW_EXTS:
        raise HTTPException(status_code=400, detail=f"非 Office 文件,不支持转换预览: {ext or '无扩展名'}")
    try:
        abs_path, _mime = await profile_import_service.ensure_local_file(row)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"原件获取失败: {e}")

    preview_dir = os.path.join(OUTPUT_DIR, "customer_files", "previews")
    os.makedirs(preview_dir, exist_ok=True)
    cache_path = os.path.join(preview_dir, f"{file_id}.pdf")
    if (os.path.exists(cache_path)
            and os.path.getmtime(cache_path) >= os.path.getmtime(abs_path)):
        return FileResponse(cache_path, media_type="application/pdf")
    try:
        produced = await asyncio.to_thread(text_extractor.office_to_pdf, abs_path, preview_dir)
        if os.path.normpath(produced) != os.path.normpath(cache_path):
            os.replace(produced, cache_path)
    except RuntimeError as e:
        msg = str(e)
        if "未安装 LibreOffice" in msg:
            raise HTTPException(status_code=501, detail=msg)
        raise HTTPException(status_code=502, detail=msg)
    return FileResponse(cache_path, media_type="application/pdf")


# ==================== 复核(质量评级驱动的轻重缓急队列) ====================


class ReviewActionPayload(BaseModel):
    reviewed_by: Optional[str] = Field(None, description="复核人")


class ReviewCorrectPayload(BaseModel):
    person_id: Optional[int] = Field(None, description="归属人 profile_persons.id;与 new_person_name 二选一")
    new_person_name: Optional[str] = Field(None, description="新建归属人姓名(no_person 复核时)")
    person_relation: Optional[str] = Field(None, description="顺带修正关系:配偶/子/女/父/母/户主")
    fields: dict = Field(default_factory=dict, description="修正字段 {field: value},value 为空表示清除")
    reviewed_by: Optional[str] = Field(None, description="复核人")


class PersonFieldPayload(BaseModel):
    fields: dict = Field(default_factory=dict, description="修正字段 {field: value},value 为空表示清除")
    relation: Optional[str] = Field(None, description="与户主关系:配偶/子/女/父/母/待确认(户主行不允许改)")
    reviewed_by: Optional[str] = Field(None, description="操作人")


@app.get(
    "/api/review/files",
    tags=["复核"],
    summary="复核队列(待复核文件按质量分升序)",
)
async def review_file_list(
    import_task_id: Optional[int] = Query(None, description="导入任务 ID"),
    client_name: Optional[str] = Query(None, description="客户姓名模糊查询"),
    reason: Optional[str] = Query(None, description="原因:no_text/garbled/ocr_short/low_confidence/extract_error/no_person/masked_id"),
    include_reviewed: bool = Query(False, description="是否包含已复核"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from db import customer_file_crud
    items, total = await customer_file_crud.list_review_files(
        import_task_id=import_task_id, client_name=client_name, reason=reason,
        include_reviewed=include_reviewed, limit=limit, offset=offset)
    return {"items": items, "total": total}


@app.get(
    "/api/review/files/{file_id}",
    tags=["复核"],
    summary="复核详情(文件+OCR+最近提取结果+可选归属人)",
)
async def review_file_get(file_id: int):
    from db import customer_file_crud, doc_extract_crud, profile_crud
    row = await customer_file_crud.get_customer_file(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    result = await doc_extract_crud.get_latest_result_for_file(file_id)
    persons = []
    task = await customer_file_crud.get_import_task(row["import_task_id"])
    if task and task.get("household_id"):
        ps = await profile_crud.list_persons(task["household_id"])
        persons = [{"id": p["id"], "name": p["name"],
                    "relation_to_main": p["relation_to_main"], "is_main": p["is_main"]}
                   for p in ps]
    return {"file": row, "result": result, "household_persons": persons,
            "household_id": task.get("household_id") if task else None}


@app.post(
    "/api/review/files/{file_id}/confirm",
    tags=["复核"],
    summary="复核确认无误",
)
async def review_file_confirm(file_id: int, payload: ReviewActionPayload):
    from db import customer_file_crud, doc_extract_crud
    row = await customer_file_crud.get_customer_file(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    result = await doc_extract_crud.get_latest_result_for_file(file_id)
    if result:
        await doc_extract_crud.update_result_review(
            result["id"], status="confirmed", reviewed_by=payload.reviewed_by)
    await customer_file_crud.set_file_reviewed(file_id)
    return {"ok": True}


@app.post(
    "/api/review/files/{file_id}/correct",
    tags=["复核"],
    summary="复核修正(修正字段永远覆盖,status='corrected';no_person 可选归属/新建人)",
)
async def review_file_correct(file_id: int, payload: ReviewCorrectPayload):
    from db import customer_file_crud, doc_extract_crud, profile_crud
    row = await customer_file_crud.get_customer_file(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    task = await customer_file_crud.get_import_task(row["import_task_id"])
    household_id = task.get("household_id") if task else None

    # 定归属人:显式 person_id > 新建人 > 最近提取结果已归因的人
    person_id = payload.person_id
    person_deduped = False
    if person_id is None and payload.new_person_name:
        if household_id is None:
            raise HTTPException(status_code=400, detail="任务无家庭上下文,无法新建归属人")
        person = await profile_crud.create_person(household_id, payload.new_person_name)
        person_id = person["id"]
        person_deduped = bool(person.get("deduped"))
    result = await doc_extract_crud.get_latest_result_for_file(file_id)
    if person_id is None and result and result.get("write_stats"):
        person_id = (result["write_stats"] or {}).get("person_id")
    if person_id is None and payload.fields:
        raise HTTPException(status_code=400, detail="缺少归属人(person_id 或 new_person_name)")

    if payload.person_relation and person_id:
        await profile_crud.set_person_relation(person_id, payload.person_relation)
    if person_id is not None:
        # 归属人落 customer_files.person_id(权威载体):人员「查看文件」/完备度矩阵据此可见
        await customer_file_crud.assign_file_person(file_id, person_id)
    for field, value in (payload.fields or {}).items():
        try:
            await profile_crud.correct_person_field(
                person_id, field, value,
                corrected_by=payload.reviewed_by,
                source_result_id=result["id"] if result else None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"字段 {field}: {e}")
    if result:
        await doc_extract_crud.update_result_review(
            result["id"], status="corrected", corrected=payload.fields,
            reviewed_by=payload.reviewed_by)
    await customer_file_crud.set_file_reviewed(file_id)
    return {"ok": True, "person_id": person_id, "deduped": person_deduped}


@app.post(
    "/api/review/files/{file_id}/dismiss",
    tags=["复核"],
    summary="复核忽略(确认无需处理)",
)
async def review_file_dismiss(file_id: int, payload: ReviewActionPayload):
    from db import customer_file_crud, doc_extract_crud
    row = await customer_file_crud.get_customer_file(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    result = await doc_extract_crud.get_latest_result_for_file(file_id)
    if result:
        await doc_extract_crud.update_result_review(
            result["id"], status="dismissed", reviewed_by=payload.reviewed_by)
    await customer_file_crud.set_file_reviewed(file_id)
    return {"ok": True}


@app.post(
    "/api/profile/persons/{person_id}/field",
    tags=["客户画像"],
    summary="人工修正人员字段(永远覆盖,status=corrected;供多源解决/人员编辑)",
)
async def person_field_correct(person_id: int, payload: PersonFieldPayload):
    from db import profile_crud
    if payload.relation is not None:
        await profile_crud.set_person_relation(person_id, payload.relation)
    for field, value in payload.fields.items():
        try:
            await profile_crud.correct_person_field(
                person_id, field, value, corrected_by=payload.reviewed_by)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"字段 {field}: {e}")
    return {"ok": True, "person_id": person_id}


@app.get(
    "/api/profile/persons/{person_id}/files",
    tags=["客户画像"],
    summary="人员的关联文件(手动归属 ∪ 提取归因)",
)
async def person_files(person_id: int):
    from db import customer_file_crud
    return await customer_file_crud.list_person_files(person_id)


class FileAssignPayload(BaseModel):
    person_id: Optional[int] = Field(None, description="归属人 profile_persons.id;null 且不带 new_person_name = 清除归属")
    new_person_name: Optional[str] = Field(None, description="新建归属人姓名(家庭内查无此人时)")


@app.get(
    "/api/profile/files",
    tags=["客户画像"],
    summary="文件归属页全局文件列表(分页+客户/文件名称/类型/归属状态筛选)",
)
async def profile_file_list(
    client_name: Optional[str] = Query(None, description="客户姓名模糊查询"),
    file_name: Optional[str] = Query(None, description="文件名称/文件编码模糊查询"),
    doc_type: Optional[str] = Query(None, description="证件类型:id_card/hukou/degree_cert/birth_cert/other"),
    assigned: Optional[str] = Query(None, description="归属状态:none=未归属/any=已归属,缺省全部"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from db import customer_file_crud
    items, total = await customer_file_crud.list_files_for_assignment(
        client_name=client_name, file_name=file_name, doc_type=doc_type, assigned=assigned,
        limit=limit, offset=offset)
    return {"items": items, "total": total}


@app.get(
    "/api/profile/households/{household_id}/persons",
    tags=["客户画像"],
    summary="家庭成员轻量列表(文件归属下拉用)",
)
async def household_person_list(household_id: int):
    from db import profile_crud
    ps = await profile_crud.list_persons(household_id)
    return [{"id": p["id"], "name": p["name"],
             "relation_to_main": p["relation_to_main"], "is_main": p["is_main"]}
            for p in ps]


@app.get(
    "/api/profile/households/{household_id}/asset-files",
    tags=["客户画像"],
    summary="家庭资产相关文件列表(全部资产 source_file_id 去重)",
)
async def household_asset_files(household_id: int):
    from db import customer_file_crud, profile_crud
    if await profile_crud.get_household(household_id) is None:
        raise HTTPException(status_code=404, detail="家庭不存在")
    return await customer_file_crud.list_asset_files(household_id)


@app.post(
    "/api/profile/files/{file_id}/assign",
    tags=["客户画像"],
    summary="指定/清除文件归属人(写 customer_files.person_id;可顺带新建人)",
)
async def profile_file_assign(file_id: int, payload: FileAssignPayload):
    from db import customer_file_crud, profile_crud
    row = await customer_file_crud.get_customer_file(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    task = await customer_file_crud.get_import_task(row["import_task_id"])
    household_id = task.get("household_id") if task else None

    person_id = payload.person_id
    person_deduped = False
    if person_id is None and payload.new_person_name:
        if household_id is None:
            raise HTTPException(status_code=400, detail="任务无家庭上下文,无法新建归属人")
        person = await profile_crud.create_person(household_id, payload.new_person_name.strip())
        person_id = person["id"]
        person_deduped = bool(person.get("deduped"))
    elif person_id is not None:
        persons = await profile_crud.list_persons(household_id) if household_id else []
        if not any(p["id"] == person_id for p in persons):
            raise HTTPException(status_code=400, detail="归属人不属于该文件所在家庭")
    await customer_file_crud.assign_file_person(file_id, person_id)
    return {"ok": True, "person_id": person_id, "deduped": person_deduped}


class AssetMergeGroup(BaseModel):
    keep_id: int = Field(..., description="保留的资产 id")
    merge_ids: list[int] = Field(..., description="被合并的资产 id 列表(至少 1 个)")
    merged_attrs: dict = Field(default_factory=dict, description="合并后的 attrs")
    merged_name: Optional[str] = Field(None, description="合并后的资产名")
    reason: Optional[str] = Field(None, description="AI 判定理由(仅存档)")


class AssetMergePayload(BaseModel):
    groups: list[AssetMergeGroup] = Field(..., description="合并组列表")


@app.post(
    "/api/profile/households/{household_id}/dedupe-assets/preview",
    tags=["客户画像"],
    summary="AI 分析家庭资产是否有重复,返回合并建议(不写库)",
)
async def dedupe_assets_preview(household_id: int):
    import asyncio
    from db import profile_crud
    import llm_service

    assets = await profile_crud.list_assets(household_id)
    # 只让 AI 分析 status='ai' 的资产;人工修正过的直接列出但不参与合并
    ai_assets = [a for a in assets if a.get("status") == "ai"]
    if len(ai_assets) < 2:
        return {"groups": [], "assets": assets, "message": "AI 生成资产少于 2 条,无需分析"}

    result = await asyncio.to_thread(
        llm_service.merge_household_assets, ai_assets,
        client_code=str(household_id))
    return {
        "groups": result.get("groups") or [],
        "assets": assets,
        "fallback": result.get("_fallback", False),
    }


@app.post(
    "/api/profile/households/{household_id}/dedupe-assets/commit",
    tags=["客户画像"],
    summary="按前端确认后的合并方案执行合并(事务写入,合并前会校验)",
)
async def dedupe_assets_commit(household_id: int, payload: AssetMergePayload):
    from db import profile_crud
    try:
        merged, deleted = await profile_crud.merge_assets(
            household_id, [g.model_dump() for g in payload.groups])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "merged_groups": merged, "deleted_rows": deleted}


@app.post(
    "/api/profile/households/{household_id}/infer-relations",
    tags=["客户画像"],
    summary="手动触发家庭关系交叉推导(刷历史已导入家庭;幂等,不建人)",
)
async def profile_infer_relations(household_id: int):
    import profile_import_service
    from db import profile_crud
    if await profile_crud.get_household(household_id) is None:
        raise HTTPException(status_code=404, detail="家庭不存在")
    return await profile_import_service.run_infer_relations(
        household_id, trigger="manual")


@app.get(
    "/api/profile/expiry-reminders",
    tags=["客户画像"],
    summary="全库证件到期提醒(护照/准证/身份证,按剩余天数升序;续签商机入口)",
)
async def profile_expiry_reminders(
        days: int = Query(180, ge=1, le=3650, description="到期预警阈值(天),剩余 ≤days 视为即将到期"),
        include_ok: bool = Query(False, description="true 时连同正常证件一起返回"),
        keyword: Optional[str] = Query(None, description="家庭名/成员名模糊筛选"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0)):
    from db import profile_crud
    return await profile_crud.list_expiry_reminders(
        days=days, include_ok=include_ok, keyword=keyword, limit=limit, offset=offset)


@app.get(
    "/api/profile/tasks/{task_id}/profile",
    tags=["客户画像"],
    summary="客户画像(客户卡 + 家庭成员 + 本任务提取明细)",
)
async def profile_task_profile(task_id: int):
    from db import customer_file_crud, doc_extract_crud, profile_crud
    task = await customer_file_crud.get_import_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    client = None
    members = []
    if task.get("client_id"):
        client = await crud.get_client_detail(task["client_id"])
        members = await family_crud.list_by_client(task["client_id"])
    # 画像 v2:家庭 + 成员字段档案(新表,主数据源)
    household = None
    persons = []
    assets = []
    cases = []
    if task.get("household_id"):
        household = await profile_crud.get_household(task["household_id"])
        persons = await profile_crud.list_persons(task["household_id"])
        profile_crud.attach_passport_expiry(persons)
        # 冲突与可信度共用一次采样(同一组 DB 查询)
        provenance = await profile_crud._collect_field_provenance(task["household_id"])
        await profile_crud.attach_field_conflicts(persons, task["household_id"], provenance=provenance)
        await profile_crud.attach_field_credibility(persons, task["household_id"], provenance=provenance)
        assets = await profile_crud.list_assets(task["household_id"])
        cases = await profile_crud.list_cases(task["household_id"])
    extractions, extraction_total = await doc_extract_crud.list_results(
        import_task_id=task_id, limit=500)
    return {
        "task": task,
        "client": client,
        "family_members": members,
        "household": household,
        "persons": persons,
        "assets": assets,
        "cases": cases,
        "extractions": extractions,
        "extraction_total": extraction_total,
        "type_counts": {
            "id_card": task["id_card_count"],
            "hukou": task["hukou_count"],
            "degree_cert": task["degree_cert_count"],
            "birth_cert": task["birth_cert_count"],
        },
    }


@app.get(
    "/api/profile/tasks/{task_id}/matrix",
    tags=["客户画像"],
    summary="完备度矩阵(人 × 材料类型:ok/warn/missing/na)",
)
async def profile_task_matrix(task_id: int):
    from db import customer_file_crud, profile_crud
    task = await customer_file_crud.get_import_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return await profile_crud.build_completeness_matrix(task)


@app.get(
    "/api/doc-extract/results",
    tags=["信息提取"],
    summary="提取结果列表查询",
)
async def doc_extract_result_list(
    import_task_id: Optional[int] = Query(None, description="导入任务 ID"),
    file_id: Optional[str] = Query(None, description="业务文件编码模糊查询"),
    doc_type: Optional[str] = Query(None, description="证件类型:id_card/hukou/degree_cert/birth_cert"),
    status: Optional[str] = Query(None, description="状态:done/error/skipped"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    from db import doc_extract_crud
    items, total = await doc_extract_crud.list_results(
        import_task_id=import_task_id, file_id=file_id,
        doc_type=doc_type, status=status, limit=limit, offset=offset)
    return {"items": items, "total": total}


@app.get(
    "/api/doc-extract/results/{row_id}",
    tags=["信息提取"],
    summary="提取结果详情(extracted/mapped 全文)",
)
async def doc_extract_result_get(row_id: int):
    from db import doc_extract_crud
    row = await doc_extract_crud.get_result(row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return row


@app.get(
    "/api/archive-detect/admin/batches",
    tags=["文件留底检测"],
    summary="后台管理 - 批次列表",
    response_model=ArchiveDetectAdminBatchListResponse,
)
async def archive_detect_admin_batches(
    status: Optional[str] = Query(None, description="批次状态筛选:running/done/error"),
    source_kind: Optional[str] = Query(None, description="来源筛选:upload/url/batch/recheck"),
    batch_id: Optional[str] = Query(None, description="批次 ID 模糊查询"),
    overall_verdict: Optional[str] = Query(None, description="总体判断筛选,多值用逗号分隔:match,partial,mismatch"),
    has_error_file: Optional[bool] = Query(None, description="是否只看含识别失败文件的批次"),
    client_code: Optional[str] = Query(None, description="客户编码模糊查询"),
    client_name: Optional[str] = Query(None, description="客户姓名模糊查询"),
    progress_oid: Optional[str] = Query(None, description="进展 OID 模糊查询"),
    progress_name: Optional[str] = Query(None, description="进展名称模糊查询"),
    handler: Optional[str] = Query(None, description="办理人模糊查询"),
    date_from: Optional[str] = Query(None, description="创建时间开始日期,格式 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="创建时间结束日期,格式 YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500, description="返回条数,1-500"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """后台管理批次列表:用于查看数据库中的批次和运行进度。"""
    for label, value in (("date_from", date_from), ("date_to", date_to)):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{label} 日期格式必须是 YYYY-MM-DD")
    verdict_list = None
    if overall_verdict:
        verdict_list = [v.strip() for v in overall_verdict.split(",") if v.strip()]
        allowed = {"match", "partial", "mismatch"}
        bad = [v for v in verdict_list if v not in allowed]
        if bad:
            raise HTTPException(status_code=400, detail=f"非法 overall_verdict: {bad}")
    return await archive_detect_crud.admin_list_batches(
        status=status,
        source_kind=source_kind,
        batch_id=batch_id,
        overall_verdict=verdict_list,
        has_error_file=has_error_file,
        client_code=client_code,
        client_name=client_name,
        progress_oid=progress_oid,
        progress_name=progress_name,
        handler=handler,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/archive-detect/admin/daily-report",
    tags=["文件留底检测"],
    summary="后台管理 - 每日留底检测报告",
    response_model=ArchiveDetectDailyReportResponse,
)
async def archive_detect_admin_daily_report(
    date: Optional[str] = Query(None, description="统计日期 YYYY-MM-DD(按批次创建时间),缺省为今天"),
):
    """每日留底检测报告:当天全部批次按客户分组的总体判定分布统计。"""
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date 日期格式必须是 YYYY-MM-DD")
    return await archive_detect_crud.admin_daily_report(date_str)


class RejudgeOverallPayload(BaseModel):
    """批量重判总体请求体。"""
    verdicts: Optional[list[str]] = Field(
        default=["partial", "mismatch"],
        description="要重判的 overall_verdict 集合;默认只刷 partial/mismatch。传 [] 或省略含义见默认值",
    )


class RerunFilesBatchPayload(RejudgeOverallPayload):
    """批量重新检测(重跑单文件)请求体。支持指定 batch_ids,提供时只处理其中 status='done' 的批次。"""
    batch_ids: Optional[list[str]] = Field(
        default=None,
        description="要重新检测的批次 ID 列表；提供时只处理其中 status='done' 的批次,忽略 verdicts",
    )
    regenerate_criteria: bool = Field(
        default=False,
        description="是否逐批根据各自 client/progress/stage 重新生成新规则 criteria",
    )


@app.post(
    "/api/archive-detect/admin/rejudge-overall",
    tags=["文件留底检测"],
    summary="后台管理 - 按新规则批量重判总体",
)
async def archive_detect_rejudge_overall(payload: RejudgeOverallPayload = RejudgeOverallPayload()):
    """按新规则(judge_batch_overall)批量重判批次总体。

    只重跑总体判定,不碰单文件结果、不 OCR、不下载。默认只刷 partial/mismatch 的 done 批次。
    异步执行,前端轮询 GET /admin/rejudge-overall/progress。
    """
    verdicts = payload.verdicts
    if verdicts:
        allowed = {"match", "partial", "mismatch"}
        bad = [v for v in verdicts if v not in allowed]
        if bad:
            raise HTTPException(status_code=400, detail=f"非法 verdict: {bad}")
    try:
        return await archive_detect_service.start_rejudge_overall(verdicts)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get(
    "/api/archive-detect/admin/rejudge-overall/progress",
    tags=["文件留底检测"],
    summary="后台管理 - 批量重判进度",
)
async def archive_detect_rejudge_progress():
    """查询批量重判总体的进度。"""
    return archive_detect_service.get_rejudge_progress()


@app.post(
    "/api/archive-detect/admin/rerun-files-batch",
    tags=["文件留底检测"],
    summary="后台管理 - 批量重审(重跑单文件)",
)
async def archive_detect_rerun_files_batch(payload: RerunFilesBatchPayload = RerunFilesBatchPayload()):
    """批量重新检测:对目标批次逐个原地重跑(force_all),复用 ocr_text 重判单文件 verdict/score,
    不重新 OCR、不下载。各批沿用自身原 criteria。默认只重审 partial/mismatch 的 done 批次。

    支持传入 batch_ids 指定具体要重新检测的 done 批次,此时忽略 verdicts。
    注意:成本高(每文件 1 次 LLM),由 worker 队列串行消化。进度只反映"已触发重跑的批次数"。
    异步执行,前端轮询 GET /admin/rerun-files-batch/progress。
    """
    verdicts = payload.verdicts
    if verdicts:
        allowed = {"match", "partial", "mismatch"}
        bad = [v for v in verdicts if v not in allowed]
        if bad:
            raise HTTPException(status_code=400, detail=f"非法 verdict: {bad}")
    try:
        return await archive_detect_service.start_rerun_files_batch(
            verdicts=verdicts, batch_ids=payload.batch_ids, regenerate_criteria=payload.regenerate_criteria
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get(
    "/api/archive-detect/admin/rerun-files-batch/progress",
    tags=["文件留底检测"],
    summary="后台管理 - 批量重审进度",
)
async def archive_detect_rerun_files_progress():
    """查询批量重审的进度(done=已触发重跑的批次数)。"""
    return archive_detect_service.get_rerun_batch_progress()


@app.get(
    "/api/archive-detect/admin/progress",
    tags=["文件留底检测"],
    summary="后台管理 - 进展包列表",
    response_model=ArchiveDetectAdminProgressListResponse,
)
async def archive_detect_admin_progress(
    client_code: Optional[str] = Query(None, description="客户编码模糊查询"),
    client_name: Optional[str] = Query(None, description="客户姓名模糊查询"),
    handler: Optional[str] = Query(None, description="办理人模糊查询"),
    project_name: Optional[str] = Query(None, description="项目名称模糊查询"),
    progress_oid: Optional[str] = Query(None, description="进展 OID 模糊查询"),
    limit: int = Query(100, ge=1, le=500, description="返回条数,1-500"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """后台管理进展包列表。"""
    return await archive_detect_crud.admin_list_progress(
        client_code=client_code,
        client_name=client_name,
        handler=handler,
        project_name=project_name,
        progress_oid=progress_oid,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/admin/file-infos",
    tags=["文件信息"],
    summary="文件信息列表查询",
    response_model=FileInfoListResponse,
)
async def list_file_infos(
    batch_id: Optional[str] = Query(None, description="批次 ID 模糊查询"),
    file_id: Optional[str] = Query(None, description="文件编码模糊查询"),
    filename: Optional[str] = Query(None, description="文件名称模糊查询"),
    status: Optional[str] = Query(None, description="文件状态:pending/fetching/ocr/llm/done/error"),
    verdict: Optional[str] = Query(None, description="判定结果:match/partial/mismatch/no_text"),
    client_code: Optional[str] = Query(None, description="客户编码模糊查询"),
    client_name: Optional[str] = Query(None, description="客户姓名模糊查询"),
    progress_name: Optional[str] = Query(None, description="进展名称模糊查询"),
    handler: Optional[str] = Query(None, description="办理人模糊查询"),
    limit: int = Query(50, ge=1, le=500, description="返回条数,1-500,默认50"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """以文件为维度的查询列表,支持按批次号、文件编码、文件名称等模糊筛选。"""
    return await archive_detect_crud.admin_list_files(
        batch_id=batch_id,
        file_id=file_id,
        filename=filename,
        status=status,
        verdict=verdict,
        client_code=client_code,
        client_name=client_name,
        progress_name=progress_name,
        handler=handler,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/archive-detect/admin/file/{record_id}",
    tags=["文件留底检测"],
    summary="后台管理 - 文件详情(含 OCR 文本)",
    response_model=ArchiveDetectAdminFileDetail,
)
async def archive_detect_admin_file_detail(
    record_id: int = Path(..., description="文件记录 ID(archive_detect_files.id)"),
):
    """后台管理单文件详情:显式返回 ocr_text 大字段。"""
    data = await archive_detect_crud.admin_get_file_detail(record_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"文件记录 {record_id} 不存在")
    return data


# ---- 原件预览缓存:首次点击下载落 temp/archive_preview_cache/,20 天 GC(_split_cleanup_loop)----
# 不放 output/ 的原因:output/ 经 /uploads 静态挂载无鉴权,record_id 自增可遍历,原件会裸奔。
ARCHIVE_PREVIEW_CACHE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "temp", "archive_preview_cache"))
ARCHIVE_PREVIEW_CACHE_MAX_AGE_SEC = 20 * 24 * 3600   # 原件预览缓存保留 20 天


def _archive_preview_cache_path(row: dict) -> str:
    """缓存键 = record_id(每行内容唯一;file_id 跨 version 内容可能不同,不能当键)。"""
    ext = os.path.splitext(row.get("filename") or "")[1].lower()
    return os.path.join(ARCHIVE_PREVIEW_CACHE_DIR, f"{row['id']}{ext or '.bin'}")


async def _ensure_archive_preview_cache(row: dict) -> str:
    """原件缓存:命中直接返回路径;未命中按 source_url 下载(过期自动刷新)后移入缓存。"""
    cache_path = _archive_preview_cache_path(row)
    if os.path.exists(cache_path):
        return cache_path
    try:
        local_path, _fname, _mime, _refresh = await file_fetcher.fetch_url_to_temp_with_refresh(
            row["source_url"], file_id=row.get("file_id"),
        )
    except file_fetcher.FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"原件下载失败: {e}")
    os.makedirs(ARCHIVE_PREVIEW_CACHE_DIR, exist_ok=True)
    try:
        os.replace(local_path, cache_path)
    except OSError as e:
        file_fetcher.cleanup_temp_file(local_path)
        raise HTTPException(status_code=500, detail=f"原件缓存写入失败: {e}")
    return cache_path


@app.get(
    "/api/archive-detect/admin/file/{record_id}/raw",
    tags=["文件留底检测"],
    summary="后台管理 - 原件预览/下载(本地缓存 20 天;首次按 source_url 重下,过期按 file_id 自动刷新)",
)
async def archive_detect_admin_file_raw(
    record_id: int = Path(..., description="文件记录 ID(archive_detect_files.id)"),
):
    """后台查看原件:首次下载落本地缓存(temp/archive_preview_cache/,20 天 GC),之后直接开缓存。"""
    from fastapi.responses import FileResponse

    row = await archive_detect_crud.admin_get_file_source(record_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"文件记录 {record_id} 不存在")
    if not row.get("source_url"):
        raise HTTPException(status_code=404, detail="该文件无原件地址(历史批次未留存)")
    cache_path = await _ensure_archive_preview_cache(row)
    mime = (row.get("mime_type")
            or mimetypes.guess_type(row.get("filename") or "")[0]
            or "application/octet-stream")
    return FileResponse(cache_path, media_type=mime, filename=row.get("filename") or None)


@app.get(
    "/api/archive-detect/admin/file/{record_id}/preview-pdf",
    tags=["文件留底检测"],
    summary="后台管理 - Office 原件转 PDF 预览(soffice 转换并缓存 20 天;无 LibreOffice 返回 501)",
)
async def archive_detect_admin_file_preview_pdf(
    record_id: int = Path(..., description="文件记录 ID(archive_detect_files.id)"),
):
    """Office 原件预览:原件缓存 + soffice 转 PDF 缓存({record_id}.preview.pdf),20 天 GC。"""
    from fastapi.responses import FileResponse

    row = await archive_detect_crud.admin_get_file_source(record_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"文件记录 {record_id} 不存在")
    ext = os.path.splitext(row.get("filename") or "")[1].lower()
    if ext not in _OFFICE_PREVIEW_EXTS:
        raise HTTPException(status_code=400, detail=f"非 Office 文件,不支持转换预览: {ext or '无扩展名'}")
    if not row.get("source_url"):
        raise HTTPException(status_code=404, detail="该文件无原件地址(历史批次未留存)")

    pdf_cache = os.path.join(ARCHIVE_PREVIEW_CACHE_DIR, f"{record_id}.preview.pdf")
    if not os.path.exists(pdf_cache):
        raw_path = await _ensure_archive_preview_cache(row)
        try:
            produced = await asyncio.to_thread(
                text_extractor.office_to_pdf, raw_path, ARCHIVE_PREVIEW_CACHE_DIR)
            # office_to_pdf 输出名 = {record_id}.pdf,改名为 {record_id}.preview.pdf
            os.replace(produced, pdf_cache)
        except Exception as e:
            msg = str(e)
            if "未安装 LibreOffice" in msg:
                raise HTTPException(status_code=501, detail=msg)
            raise HTTPException(status_code=502, detail=msg)
    return FileResponse(pdf_cache, media_type="application/pdf")


# ==================== 业务接口(阶段三):增量复用 + 业务字段透传 ====================
# 注意:business/batch 和 business/batch/upload 必须声明在 /{batch_id} 之前。

@app.post(
    "/api/archive-detect/business/batch",
    tags=["文件留底检测"],
    summary="业务审核 - 提交进展包(OSS URL)",
    response_model=ArchiveDetectBusinessSubmitResponse,
)
async def archive_detect_business_batch(payload: BusinessBatchPayload):
    """业务方批量提交进展包(JSON + OSS URL 模式)。

    请求体:{criteria, client:{client_code, name}, progress:{progress_oid, handler, ...}, items:[{file_id, filename, url}]}
    返回:{batch_id, progress_id, total_files, reused_count, new_count}
    业务方用 batch_id 轮询 GET /api/archive-detect/business/batch/{batch_id} 拿完整结果。
    """
    # URL 校验
    for i, it in enumerate(payload.items):
        url = (it.url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail=f"非法 URL(仅支持 http/https): items[{i}].url={url}")

    if payload.stage not in ("pre_submit", "post_submit"):
        raise HTTPException(status_code=400, detail=f"非法 stage: {payload.stage} (仅支持 pre_submit / post_submit)")

    try:
        result = await archive_detect_service.submit_business_batch(
            criteria=payload.criteria,
            stage=payload.stage,
            client_payload=payload.client.model_dump(),
            progress_payload=payload.progress.model_dump(),
            items=[it.model_dump() for it in payload.items],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.get(
    "/api/archive-detect/business/batch",
    tags=["文件留底检测"],
    summary="业务审核 - 查询批次结果(Query 参数)",
    response_model=ArchiveDetectBatchResponse,
)
async def archive_detect_business_batch_get_by_query(
    batch_id: str = Query(..., description="批次 ID,由业务审核提交接口返回"),
):
    """业务接口轮询(Query 版):返回完整结果含 client/progress/files/overall_*。"""
    data = await archive_detect_service.get_business_batch(batch_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return data


@app.get(
    "/api/archive-detect/business/batch/{batch_id}",
    tags=["文件留底检测"],
    summary="业务审核 - 查询批次结果",
    response_model=ArchiveDetectBatchResponse,
)
async def archive_detect_business_batch_get(
    batch_id: str = Path(..., description="批次 ID,由业务审核提交接口返回")
):
    """业务接口轮询:返回完整结果含 client/progress/files/overall_*。"""
    data = await archive_detect_service.get_business_batch(batch_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return data


@app.post(
    "/api/archive-detect/recheck/{batch_id}",
    tags=["文件留底检测"],
    summary="重新审核 - 复用 OCR 文本重新跑 AI",
    response_model=ArchiveDetectRecheckResponse,
)
async def archive_detect_recheck(
    payload: ArchiveDetectRecheckPayload,
    batch_id: str = Path(..., description="要重新审核的原批次 ID"),
):
    """重新审核当前批次:有 OCR 文本则跳过 OCR 只跑 AI;否则尝试重新下载/OCR。

    regenerate_criteria=True 时,根据原批次 client/progress/stage 重新生成新规则 criteria,忽略 criteria 字段。
    """
    try:
        return await archive_detect_service.submit_recheck_batch(
            source_batch_id=batch_id,
            criteria=payload.criteria,
            stage=payload.stage,
            regenerate_criteria=payload.regenerate_criteria,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/api/archive-detect/rerun/{batch_id}",
    tags=["文件留底检测"],
    summary="原地重跑 - 复用已有结果,只补跑缺失的",
    response_model=ArchiveDetectRerunResponse,
)
async def archive_detect_rerun(
    payload: ArchiveDetectRecheckPayload,
    batch_id: str = Path(..., description="要重跑的批次 ID"),
    force_all: bool = Query(False, description="是否无视已有 AI 结果,全部用新 criteria 重跑"),
):
    """原地重跑批次:
    - 有 OCR 文本的跳过 OCR
    - 有 AI 结果的默认跳过(force_all=False)
    - 缺失的部分补跑
    - 用新 criteria 更新批次提示词
    - regenerate_criteria=True 时,忽略传入的 criteria,根据批次当前 client/progress/stage 重新生成新规则 criteria
    """
    try:
        return await archive_detect_service.rerun_batch_inplace(
            batch_id=batch_id,
            criteria=payload.criteria,
            stage=payload.stage,
            force_all=force_all,
            regenerate_criteria=payload.regenerate_criteria,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/api/archive-detect/batch",
    tags=["文件留底检测"],
    summary="查询批次状态(Query 参数)",
    response_model=ArchiveDetectBatchResponse,
)
async def archive_detect_get_by_query(
    batch_id: str = Query(..., description="批次 ID,由提交接口返回"),
):
    """轮询 batch + 每文件状态(Query 版)。优先内存态,缺失时从 DB 读。"""
    data = await archive_detect_service.get_batch(batch_id)
    if not data:
        raise HTTPException(status_code=404,
                            detail=f"批次 {batch_id} 不存在（服务可能已重启，请重新提交）")
    return data


@app.get(
    "/api/archive-detect/{batch_id}",
    tags=["文件留底检测"],
    summary="查询批次状态(通用)",
    response_model=ArchiveDetectBatchResponse,
)
async def archive_detect_get(
    batch_id: str = Path(..., description="批次 ID,由提交接口返回")
):
    """轮询 batch + 每文件状态。优先内存态,缺失时从 DB 读（重启后可恢复）。

    内存态含细粒度中间态(fetching/ocr/llm);DB 只含终态(done/error)。
    ocr_text 在 DB 层已 defer,本接口不返回大文本。
    """
    data = await archive_detect_service.get_batch(batch_id)
    if not data:
        raise HTTPException(status_code=404,
                            detail=f"批次 {batch_id} 不存在（服务可能已重启，请重新提交）")
    return data


@app.delete(
    "/api/archive-detect/{batch_id}",
    tags=["文件留底检测"],
    summary="删除历史批次",
    response_model=ArchiveDetectDeleteResponse,
)
async def archive_detect_delete(
    batch_id: str = Path(..., description="要删除的批次 ID")
):
    """删除一条历史记录（内存 + DB 级联清理 files）。"""
    ok = await archive_detect_service.delete_batch(batch_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return {"deleted": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
