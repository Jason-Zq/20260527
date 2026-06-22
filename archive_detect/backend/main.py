"""
文件留底检测 - FastAPI 入口（精简版，无 DB）。

只暴露 5 条路由：
  POST   /api/archive-detect/upload      上传文件（multipart）
  POST   /api/archive-detect/urls        粘贴 URL 列表（JSON）
  GET    /api/archive-detect/{batch_id}  轮询批次结果
  GET    /api/health                     健康检查

启动:
  cd archive_detect/backend
  python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import asyncio
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import llm_service
import file_fetcher
import archive_detect_service

app = FastAPI(title="文件留底检测", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    """启动：加载配置 + 起后台 GC。"""
    llm_service.load_config()
    asyncio.create_task(archive_detect_service.gc_loop())
    print("配置已加载，文件留底检测服务启动完成")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "archive-detect"}


# ==================== 路由 ====================

class ArchiveDetectUrlsPayload(BaseModel):
    user_prompt: str
    urls: list[str]


@app.post("/api/archive-detect/upload")
async def archive_detect_upload(
    files: list[UploadFile] = File(...),
    user_prompt: str = Form(...),
):
    """上传文件 + 用户判定提示词。返回 {batch_id, total_files}。"""
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
        # 校验失败 → 清理已落盘的临时文件
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
    """URL 列表模式。"""
    user_prompt = (payload.user_prompt or "").strip()
    urls = [u.strip() for u in (payload.urls or []) if u and u.strip()]
    if not user_prompt:
        raise HTTPException(status_code=400, detail="判定标准（提示词）不能为空")
    if not urls:
        raise HTTPException(status_code=400, detail="请至少输入一个文件地址")
    if len(urls) > archive_detect_service.MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多 {archive_detect_service.MAX_FILES_PER_BATCH} 个文件地址，收到 {len(urls)} 个",
        )
    for u in urls:
        if not u.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail=f"非法地址（仅支持 http/https）：{u}")

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


@app.get("/api/archive-detect/{batch_id}")
async def archive_detect_get(batch_id: str):
    """轮询 batch 状态。无 DB，重启后所有 batch 失效。"""
    data = archive_detect_service.get_batch(batch_id)
    if not data:
        raise HTTPException(status_code=404,
                            detail=f"批次 {batch_id} 不存在（服务可能已重启，请重新提交）")
    return data


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
