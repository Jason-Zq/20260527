"""文件留底检测提示词库 CRUD（archive_detect_prompts）。

业务键 = (project_name, project_code, project_detail_name, project_detail_code, progress_name)
五元组（空值归一化为 ''，与表唯一索引 ux_archive_detect_prompts_key 对齐）。
prompt1 = 批次总判模板（缺省由上层注入 llm_service.DEFAULT_JUDGE_OVERALL_TEMPLATE）；
prompt2 = 项目专属留底标准（AI 生成或手填，空 = 缺失，finalize 时会自动生成补齐）。

本模块不 import llm_service（避免 db 层反向依赖），默认模板由调用方传入。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from db.engine import async_session_maker
from db.models import ArchiveDetectPrompt


def normalize_prompt_key(
    project_name: Optional[str] = None,
    project_code: Optional[str] = None,
    project_detail_name: Optional[str] = None,
    project_detail_code: Optional[str] = None,
    progress_name: Optional[str] = None,
) -> tuple:
    """五元组归一化:None→''、strip。返回 (project_name, project_code, project_detail_name,
    project_detail_code, progress_name) 定序元组,与表列一一对应。"""
    return tuple((v or "").strip() for v in (
        project_name, project_code, project_detail_name, project_detail_code, progress_name,
    ))


def _prompt_to_dict(p: ArchiveDetectPrompt) -> dict:
    return {
        "id": p.id,
        "project_name": p.project_name,
        "project_code": p.project_code,
        "project_detail_name": p.project_detail_name,
        "project_detail_code": p.project_detail_code,
        "progress_name": p.progress_name,
        "prompt1": p.prompt1,
        "prompt2": p.prompt2,
        "apply_to_overall1": bool(p.apply_to_overall1),
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else "",
        "updated_at": p.updated_at.strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else "",
    }


def _key_conditions(key: tuple):
    cols = (ArchiveDetectPrompt.project_name, ArchiveDetectPrompt.project_code,
            ArchiveDetectPrompt.project_detail_name, ArchiveDetectPrompt.project_detail_code,
            ArchiveDetectPrompt.progress_name)
    return [col == val for col, val in zip(cols, key)]


async def list_prompts(
    *,
    project_name: Optional[str] = None,
    project_detail_name: Optional[str] = None,
    progress_name: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """提示词库列表:项目名/项目详情/进展名模糊筛选 + 分页,按 id 倒序(新建在前)。"""
    async with async_session_maker() as session:
        stmt = select(ArchiveDetectPrompt)
        count_stmt = select(func.count()).select_from(ArchiveDetectPrompt)
        conditions = []
        if project_name:
            conditions.append(ArchiveDetectPrompt.project_name.ilike(f"%{project_name}%"))
        if project_detail_name:
            conditions.append(ArchiveDetectPrompt.project_detail_name.ilike(f"%{project_detail_name}%"))
        if progress_name:
            conditions.append(ArchiveDetectPrompt.progress_name.ilike(f"%{progress_name}%"))
        for cond in conditions:
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        stmt = stmt.order_by(ArchiveDetectPrompt.id.desc()).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).scalars().all()
        total = (await session.execute(count_stmt)).scalar() or 0
        return {"items": [_prompt_to_dict(p) for p in rows], "total": total}


async def get_prompt_by_id(row_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        p = await session.get(ArchiveDetectPrompt, row_id)
        return _prompt_to_dict(p) if p else None


async def get_prompt_by_key(key: tuple) -> Optional[dict]:
    """按归一化五元组查行(finalize 判定2 用)。"""
    async with async_session_maker() as session:
        stmt = select(ArchiveDetectPrompt).where(*_key_conditions(key))
        p = (await session.execute(stmt)).scalars().first()
        return _prompt_to_dict(p) if p else None


async def create_prompt(
    key: tuple,
    *,
    prompt1: Optional[str] = None,
    prompt2: Optional[str] = None,
) -> dict:
    """新建提示词库行。五元组冲突抛 IntegrityError(路由层映射 409)。"""
    async with async_session_maker() as session:
        p = ArchiveDetectPrompt(
            project_name=key[0], project_code=key[1], project_detail_name=key[2],
            project_detail_code=key[3], progress_name=key[4],
            prompt1=prompt1 or None, prompt2=prompt2 or None,
            created_at=datetime.now(), updated_at=datetime.now(),
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return _prompt_to_dict(p)


async def update_prompt(
    row_id: int,
    key: tuple,
    *,
    prompt1: Optional[str] = None,
    prompt2: Optional[str] = None,
) -> Optional[dict]:
    """全量更新(五元组 + prompt1/prompt2);行不存在返回 None,五元组撞他人抛 IntegrityError。"""
    async with async_session_maker() as session:
        p = await session.get(ArchiveDetectPrompt, row_id)
        if not p:
            return None
        p.project_name, p.project_code, p.project_detail_name = key[0], key[1], key[2]
        p.project_detail_code, p.progress_name = key[3], key[4]
        p.prompt1 = prompt1 or None
        p.prompt2 = prompt2 or None
        p.updated_at = datetime.now()
        await session.commit()
        await session.refresh(p)
        return _prompt_to_dict(p)


async def delete_prompt(row_id: int) -> bool:
    async with async_session_maker() as session:
        p = await session.get(ArchiveDetectPrompt, row_id)
        if not p:
            return False
        await session.delete(p)
        await session.commit()
        return True


async def get_or_create_prompt(key: tuple, *, default_prompt1: Optional[str] = None) -> tuple:
    """按五元组查,没有则建行(prompt1=默认模板,prompt2 留空待生成)。返回 (dict, created)。

    唯一索引兜底并发:IntegrityError 时重查返回胜出者(参照 profile_crud._create_person_in_session)。
    """
    async with async_session_maker() as session:
        stmt = select(ArchiveDetectPrompt).where(*_key_conditions(key))
        p = (await session.execute(stmt)).scalars().first()
        if p:
            return _prompt_to_dict(p), False
        p = ArchiveDetectPrompt(
            project_name=key[0], project_code=key[1], project_detail_name=key[2],
            project_detail_code=key[3], progress_name=key[4],
            prompt1=default_prompt1 or None, prompt2=None,
            created_at=datetime.now(), updated_at=datetime.now(),
        )
        try:
            async with session.begin_nested():
                session.add(p)
                await session.flush()
        except IntegrityError:
            session.expunge(p)  # 失败 INSERT 的 pending 对象必须移除,否则后续 autoflush 重放毒化事务
            winner = (await session.execute(stmt)).scalars().first()
            if winner is not None:
                return _prompt_to_dict(winner), False
            raise
        await session.commit()
        await session.refresh(p)
        return _prompt_to_dict(p), True


async def set_prompt2(row_id: int, prompt2: str) -> None:
    """写入/覆盖 prompt2(AI 生成结果落库)。"""
    async with async_session_maker() as session:
        p = await session.get(ArchiveDetectPrompt, row_id)
        if not p:
            return
        p.prompt2 = prompt2 or None
        p.updated_at = datetime.now()
        await session.commit()


async def set_apply_to_overall1(row_id: int, apply: bool) -> bool:
    """设置「应用到总体1」开关。行不存在返回 False。"""
    async with async_session_maker() as session:
        p = await session.get(ArchiveDetectPrompt, row_id)
        if not p:
            return False
        p.apply_to_overall1 = bool(apply)
        p.updated_at = datetime.now()
        await session.commit()
        return True
