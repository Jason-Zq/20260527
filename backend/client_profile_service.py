"""客户资料结构化生成服务。"""
import asyncio

import llm_service
from db import client_profile_crud


async def submit_generate_profile(client_id: int, source_file_ids: list[int] | None = None) -> dict:
    """创建生成任务,后台异步抽取并直接写入客户档案表。"""
    files = await client_profile_crud.list_source_files_for_client(client_id, source_file_ids)
    if not files:
        raise ValueError("该客户没有可用于生成档案的 OCR 文件")
    task = await client_profile_crud.create_generation_task(client_id, files)
    asyncio.create_task(_generate_background(task["task_id"], client_id, files))
    return {
        "task_id": task["task_id"],
        "client_id": client_id,
        "source_file_count": len(files),
        "status": "running",
    }


async def _generate_background(task_id: int, client_id: int, files: list[dict]) -> None:
    facts_list = []
    try:
        from sqlalchemy import select
        from db.engine import async_session_maker
        from db.models import ArchiveDetectFile

        for f in files:
            # 候选列表不包含 ocr_text，需要从 DB 重新查询
            file_id = f.get("id")
            async with async_session_maker() as session:
                stmt = select(ArchiveDetectFile.ocr_text).where(ArchiveDetectFile.id == file_id)
                ocr_text = (await session.execute(stmt)).scalar_one_or_none() or ""

            facts = await asyncio.to_thread(
                llm_service.extract_client_profile_facts,
                ocr_text,
                f.get("filename") or "",
                f.get("doc_category") or "",
            )
            facts["_source_file_id"] = f.get("id")
            facts["_source_filename"] = f.get("filename")
            facts_list.append(facts)

        counts = await client_profile_crud.apply_profile_facts(client_id, facts_list)
        summary = {
            "source_files": [
                {"id": f.get("id"), "filename": f.get("filename"), "doc_category": f.get("doc_category")}
                for f in files
            ],
            "facts_count": len(facts_list),
            "facts": facts_list,
        }
        await client_profile_crud.update_generation_task(
            task_id,
            status="done",
            extracted_summary=summary,
            created_count=counts,
        )
    except Exception as e:
        await client_profile_crud.update_generation_task(task_id, status="error", error=str(e))


async def get_generation_task(task_id: int) -> dict | None:
    return await client_profile_crud.get_generation_task(task_id)


async def list_generation_tasks(client_id: int, limit: int = 20) -> list[dict]:
    return await client_profile_crud.list_generation_tasks(client_id, limit=limit)


async def list_source_files(client_id: int) -> dict:
    files = await client_profile_crud.list_source_files_for_client(client_id)
    items = []
    for f in files:
        items.append({
            "id": f.get("id"),
            "filename": f.get("filename"),
            "doc_category": f.get("doc_category"),
            "progress_name": f.get("progress_name"),
            "progress_oid": f.get("progress_oid"),
            "status": "done",
            "char_count": f.get("char_count"),
            "has_ocr_text": bool(f.get("has_ocr_text")),
            "selectable": bool(f.get("selectable")),
        })
    return {"items": items, "total": len(items)}
