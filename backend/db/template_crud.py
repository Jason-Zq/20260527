"""
Word 模板相关 CRUD 封装。
风格对齐 backend/db/crud.py：所有函数 async def + async with async_session_maker。
v2 字段重命名：fields→placeholders，field_values→placeholder_values。
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import select, delete as sa_delete

from db.engine import async_session_maker
from db.models import Template, TemplateFill


async def create_template(
    name: str,
    filename: str,
    file_path: str,
    placeholders: list,
    created_by: Optional[str] = None,
) -> Template:
    """落库一条新模板记录。"""
    async with async_session_maker() as session:
        tpl = Template(
            name=name,
            filename=filename,
            file_path=file_path,
            placeholders=placeholders,
            created_by=created_by,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        session.add(tpl)
        await session.commit()
        await session.refresh(tpl)
        return tpl


async def update_template_file_path(template_id: int, file_path: str) -> Optional[Template]:
    """模板入库后将文件实际落到 output/templates/{id}/，回写 file_path。"""
    async with async_session_maker() as session:
        res = await session.execute(select(Template).where(Template.id == template_id))
        tpl = res.scalar_one_or_none()
        if not tpl:
            return None
        tpl.file_path = file_path
        tpl.updated_at = datetime.now()
        await session.commit()
        await session.refresh(tpl)
        return tpl


async def list_templates(limit: int = 200, offset: int = 0) -> list[dict]:
    """模板列表（按更新时间倒序）。"""
    async with async_session_maker() as session:
        stmt = (
            select(Template)
            .order_by(Template.updated_at.desc().nullslast(), Template.id.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await session.execute(stmt)
        rows = res.scalars().all()

        result = []
        for tpl in rows:
            placeholders = tpl.placeholders or []
            # v2 模板：placeholders[0] 含 dict {anchor: {...}} 字段；老模板是 {original_text: ...}
            is_v2 = bool(placeholders) and isinstance(placeholders[0], dict) and isinstance(placeholders[0].get("anchor"), dict)
            result.append({
                "id": tpl.id,
                "name": tpl.name,
                "filename": tpl.filename,
                "placeholder_count": len(placeholders) if isinstance(placeholders, list) else 0,
                "legacy": not is_v2,  # v1 老模板标记为 legacy
                "created_at": tpl.created_at.strftime("%Y-%m-%d %H:%M:%S") if tpl.created_at else "",
                "updated_at": tpl.updated_at.strftime("%Y-%m-%d %H:%M:%S") if tpl.updated_at else "",
            })
        return result


async def get_template(template_id: int) -> Optional[Template]:
    """按 id 查询模板。"""
    async with async_session_maker() as session:
        res = await session.execute(select(Template).where(Template.id == template_id))
        return res.scalar_one_or_none()


async def get_template_dict(template_id: int) -> Optional[dict]:
    """按 id 查询模板并返回字典（含 placeholders）。"""
    async with async_session_maker() as session:
        res = await session.execute(select(Template).where(Template.id == template_id))
        tpl = res.scalar_one_or_none()
        if not tpl:
            return None
        return {
            "id": tpl.id,
            "name": tpl.name,
            "filename": tpl.filename,
            "file_path": tpl.file_path,
            "placeholders": tpl.placeholders or [],
            "created_at": tpl.created_at.strftime("%Y-%m-%d %H:%M:%S") if tpl.created_at else "",
            "updated_at": tpl.updated_at.strftime("%Y-%m-%d %H:%M:%S") if tpl.updated_at else "",
        }


async def delete_template(template_id: int) -> bool:
    """删除模板（级联清理 template_fills）。"""
    async with async_session_maker() as session:
        res = await session.execute(select(Template).where(Template.id == template_id))
        tpl = res.scalar_one_or_none()
        if not tpl:
            return False
        # 显式清理 fills（保险，避免外键约束）
        await session.execute(
            sa_delete(TemplateFill).where(TemplateFill.template_id == template_id)
        )
        await session.delete(tpl)
        await session.commit()
        return True


async def create_template_fill(
    template_id: int,
    client_id: Optional[int],
    placeholder_values: dict,
    output_pdf: Optional[str] = None,
) -> int:
    """记录一次模板填充。"""
    async with async_session_maker() as session:
        fill = TemplateFill(
            template_id=template_id,
            client_id=client_id,
            placeholder_values=placeholder_values,
            output_pdf=output_pdf,
            created_at=datetime.now(),
        )
        session.add(fill)
        await session.commit()
        await session.refresh(fill)
        return fill.id


async def get_cached_fill(template_id: int, client_id: int) -> Optional[dict]:
    """取最新一条 (template_id, client_id) 填充作为映射缓存。"""
    async with async_session_maker() as session:
        stmt = (
            select(TemplateFill)
            .where(
                TemplateFill.template_id == template_id,
                TemplateFill.client_id == client_id,
            )
            .order_by(TemplateFill.created_at.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        fill = res.scalar_one_or_none()
        if not fill or not fill.placeholder_values:
            return None
        return dict(fill.placeholder_values) if not isinstance(fill.placeholder_values, dict) else fill.placeholder_values


async def list_fills_by_client(client_id: int, limit: int = 100) -> list[dict]:
    """按客户 id 反查模板生成历史，按时间倒序。
    供客户详情页"已生成文件"tab 使用。
    JOIN templates 拿模板名称。
    output_pdf 转为可通过 /uploads/ 访问的相对路径（位于 output/templates/{tid}/fills/ 下）。
    """
    import os as _os
    backend_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # backend/
    output_root = _os.path.normpath(_os.path.join(backend_dir, "..", "output"))

    async with async_session_maker() as session:
        stmt = (
            select(TemplateFill, Template.name, Template.filename)
            .join(Template, Template.id == TemplateFill.template_id)
            .where(TemplateFill.client_id == client_id)
            .order_by(TemplateFill.created_at.desc())
            .limit(limit)
        )
        res = await session.execute(stmt)
        rows = res.all()

        result = []
        for fill, tpl_name, tpl_filename in rows:
            placeholder_count = 0
            if isinstance(fill.placeholder_values, dict):
                placeholder_count = sum(1 for v in fill.placeholder_values.values() if v)

            # 把绝对路径转为 /uploads/ 相对路径
            output_url = None
            output_kind = None
            if fill.output_pdf:
                abs_path = fill.output_pdf
                if _os.path.exists(abs_path):
                    try:
                        rel = _os.path.relpath(abs_path, output_root).replace("\\", "/")
                        if not rel.startswith(".."):
                            output_url = f"/uploads/{rel}"
                            output_kind = "pdf" if abs_path.lower().endswith(".pdf") else "docx"
                    except ValueError:
                        # Windows 跨盘符 relpath 抛 ValueError，忽略
                        pass

            result.append({
                "id": fill.id,
                "template_id": fill.template_id,
                "template_name": tpl_name,
                "template_filename": tpl_filename,
                "placeholder_count": placeholder_count,
                "output_url": output_url,
                "output_kind": output_kind,
                "created_at": fill.created_at.strftime("%Y-%m-%d %H:%M:%S") if fill.created_at else "",
            })
        return result
