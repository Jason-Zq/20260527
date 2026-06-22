"""
FastAPI 后端入口
智能文档审核工作台 API 服务
"""

import os
import json
import asyncio
import time
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
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

import file_fetcher
import text_extractor
import archive_detect_service
from db import archive_detect_crud

app = FastAPI(title="智能文档审核工作台", version="1.0.0")

# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 输出目录（统一用 output/）
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 静态文件：提供图片访问（URL 路径保持 /uploads/ 不变，前端无需改动）
app.mount("/uploads", StaticFiles(directory=OUTPUT_DIR), name="uploads")

# 内存中的任务状态缓存（仅用于轮询进度，不再作为持久存储）
_task_status = {}   # {task_id: {"status": "ocr|llm|done|error", "progress": "", "error": ""}}
_task_results = {}

# PDF 拆分流水线的独立状态字典(与解析流水线隔离)
# {task_id: {"status": "ocr|llm|splitting|done|error", "progress": "", "error": "", "result": dict|None}}
_split_task_status = {}

class ReviewPayload(BaseModel):
    """人工复核提交的数据"""
    task_id: str
    fields: dict
    doc_type: Optional[str] = None

@app.on_event("startup")
async def startup():
    """启动时加载配置并初始化数据库连接池"""
    llm_service.load_config()
    await init_db()
    # 确保模板存储目录存在
    os.makedirs(os.path.join(OUTPUT_DIR, "templates"), exist_ok=True)
    # 清理过期的临时模板文件（未保存就关 dialog 留下的）
    await asyncio.to_thread(_cleanup_stale_template_temp, 60)
    # 清理过期的任务目录（>30天的 OCR 图片和拆分 PDF）
    await asyncio.to_thread(_cleanup_expired_output, 30)
    # 启动拆分任务 7 天 TTL 周期清理(后台 task,每 24h 跑一次)
    asyncio.create_task(_split_cleanup_loop())
    print("配置已加载，数据库已连接，服务启动完成")

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

    # 保存上传文件到临时位置
    ext = os.path.splitext(file.filename or "")[1] or ".pdf"
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{task_id}{ext}")
    with open(temp_path, "wb") as f:
        content = await file.read()
        f.write(content)

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
        llm_result = await asyncio.to_thread(llm_service.detect_and_extract, ocr_texts)

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

        _task_results[task_id] = result_data
        _task_status[task_id] = {"status": "done", "progress": "", "error": ""}
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
        _task_status[task_id] = {"status": "error", "progress": "", "error": str(e)}
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
            _task_results[task_id] = data
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
        _task_results[task_id] = data
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

    _task_results[task_id] = result_data

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

    with open(temp_path, "wb") as f:
        content = await file.read()
        f.write(content)

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
        raw_ranges = await asyncio.to_thread(llm_service.detect_page_ranges, per_page_texts)

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
        _split_task_status[task_id] = {"status": "done", "progress": "", "error": "", "result": result}
        print(f"[split:{task_id}] 完成: {len(files)} 份子 PDF, 耗时 {duration}s")
    except Exception as e:
        print(f"[split:{task_id}] 失败: {e}")
        _split_task_status[task_id] = {"status": "error", "progress": "", "error": str(e), "result": None}
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
    with open(original_path, "wb") as f:
        content = await file.read()
        f.write(content)

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


async def _split_cleanup_loop():
    """周期任务:每 SPLIT_CLEANUP_INTERVAL_HOURS 小时跑一次 _split_cleanup_once。

    启动后立即跑一次(catch up 任何上次 server 关停期间过期的);之后按周期跑。
    """
    while True:
        try:
            await _split_cleanup_once()
        except Exception as e:
            print(f"[split_cleanup] 异常(忽略,下个周期重试): {e}")
        await asyncio.sleep(SPLIT_CLEANUP_INTERVAL_HOURS * 3600)


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
    - 支持 PDF/PNG/JPG/JPEG/BMP/TIFF/WEBP/DOCX
    - .doc 不支持，需转换为 .docx
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
                detail=f"不支持的文件类型: {filename}（支持 {', '.join(file_fetcher.get_supported_extensions())}）"
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
                llm_service.summarize_text, text, progress_name
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

class ArchiveDetectUrlsPayload(BaseModel):
    """URL 列表模式提交体。"""
    user_prompt: str
    urls: list[str]


@app.post("/api/archive-detect/upload")
async def archive_detect_upload(
    files: list[UploadFile] = File(...),
    user_prompt: str = Form(...),
):
    """上传文件模式：multipart 提交多文件 + 判定提示词。

    返回 {batch_id, total_files}。前端用 batch_id 轮询 GET /api/archive-detect/{batch_id}。
    """
    user_prompt = (user_prompt or "").strip()
    if not user_prompt:
        raise HTTPException(status_code=400, detail="判定标准（提示词）不能为空")
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")
    if len(files) > archive_detect_service.MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多 {archive_detect_service.MAX_FILES_PER_BATCH} 个文件，收到 {len(files)} 个",
        )

    # 把每个 UploadFile 落到 temp/archive_detect/，构造 items
    items: list[dict] = []
    upload_dir = archive_detect_service._upload_temp_dir()
    for i, f in enumerate(files):
        if not f.filename:
            raise HTTPException(status_code=400, detail=f"第 {i+1} 个文件没有文件名")
        if not file_fetcher.is_supported_extension(f.filename):
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型：{f.filename}（支持 {', '.join(file_fetcher.get_supported_extensions())}）",
            )
        # 文件名含中文 / 空格也安全
        safe_name = os.path.basename(f.filename)
        token = datetime.now().strftime("%y%m%d%H%M%S") + f"_{i}_"
        local_path = os.path.join(upload_dir, token + safe_name)
        content = await f.read()
        with open(local_path, "wb") as out:
            out.write(content)
        items.append({
            "local_path": local_path,
            "filename": safe_name,
            "mime_type": f.content_type or None,
        })

    try:
        batch_id = await archive_detect_service.submit_batch(
            user_prompt=user_prompt,
            source_kind="upload",
            items=items,
        )
    except ValueError as e:
        # 提交失败（如校验错误）→ 清理已落盘的临时文件
        for it in items:
            try:
                if it.get("local_path") and os.path.exists(it["local_path"]):
                    os.remove(it["local_path"])
            except OSError:
                pass
        raise HTTPException(status_code=400, detail=str(e))

    return {"batch_id": batch_id, "total_files": len(items)}


@app.post("/api/archive-detect/urls")
async def archive_detect_urls(payload: ArchiveDetectUrlsPayload):
    """URL 列表模式：JSON 提交一组 URL + 判定提示词。"""
    user_prompt = (payload.user_prompt or "").strip()
    urls = [u.strip() for u in (payload.urls or []) if u and u.strip()]
    if not user_prompt:
        raise HTTPException(status_code=400, detail="判定标准（提示词）不能为空")
    if not urls:
        raise HTTPException(status_code=400, detail="请至少输入一个 URL")
    if len(urls) > archive_detect_service.MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多 {archive_detect_service.MAX_FILES_PER_BATCH} 个 URL，收到 {len(urls)} 个",
        )
    for u in urls:
        if not u.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail=f"非法 URL（仅支持 http/https）: {u}")

    items = [{"source_url": u} for u in urls]
    try:
        batch_id = await archive_detect_service.submit_batch(
            user_prompt=user_prompt,
            source_kind="url",
            items=items,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"batch_id": batch_id, "total_files": len(items)}


# 注意：history 必须在 /{batch_id} 之前注册，避免路径参数吞掉 "history"
@app.get("/api/archive-detect/history")
async def archive_detect_history(limit: int = Query(200, ge=1, le=500)):
    """历史 batch 列表（不含 files 详情）。"""
    items = await archive_detect_service.list_history(limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/api/archive-detect/{batch_id}")
async def archive_detect_get(batch_id: str):
    """轮询 batch + 每文件状态。优先内存态，缺失时从 DB 读。"""
    data = await archive_detect_service.get_batch(batch_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return data


@app.delete("/api/archive-detect/{batch_id}")
async def archive_detect_delete(batch_id: str):
    """删除一条历史记录（DB 级联清理 files）。"""
    ok = await archive_detect_service.delete_batch(batch_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return {"deleted": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
