"""doc_extract CRUD: 提取结果(doc_extract_results) + 复核状态更新。

提取规则已迁至代码常量 backend/extract_rules.py(不再走 DB);提取结果一次运行一行,全程留痕可回溯。
2026-08:旧 clients/family_members 归因写库路径(find_person_match/apply_extracted_fields)
已随旧客户档案体系删除;画像 v2 的归因在 db/profile_crud.find_person_match。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func

from db.ai_api_call_crud import _clean_text
from db.engine import async_session_maker
from db.models import DocExtractResult


# ==================== 脱敏/证件号判定(service 与测试共用) ====================

MASKED_TOKENS = ("[身份证]", "[手机号]", "[银行卡]", "[金额]", "[座机]")


def is_masked(v) -> bool:
    """值是否被脱敏(含星号或任一占位词)。脱敏值不写库、不参与归因。"""
    if not v or not isinstance(v, str):
        return False
    return "*" in v or any(tok in v for tok in MASKED_TOKENS)


def valid_id_number(v) -> bool:
    """身份证号基本格式:17 位数字 + 数字/X。"""
    if not v or not isinstance(v, str):
        return False
    s = v.strip()
    return len(s) == 18 and s[:17].isdigit() and (s[17].isdigit() or s[17] in "Xx")


# ==================== 序列化 ====================

def _to_result_dict(r: DocExtractResult) -> dict:
    return {
        "id": r.id,
        "customer_file_id": r.customer_file_id,
        "import_task_id": r.import_task_id,
        "file_id": r.file_id,
        "client_id": r.client_id,
        "doc_type": r.doc_type,
        "rule_id": r.rule_id,
        "rule_version": r.rule_version,
        "status": r.status,
        "skip_reason": r.skip_reason,
        "extracted": r.extracted,
        "mapped": r.mapped,
        "write_stats": r.write_stats,
        "error_msg": r.error_msg,
        "elapsed_ms": r.elapsed_ms,
        "review_status": r.review_status,
        "corrected": r.corrected,
        "reviewed_by": r.reviewed_by,
        "reviewed_at": r.reviewed_at.strftime("%Y-%m-%d %H:%M:%S") if r.reviewed_at else None,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


# ==================== 结果 CRUD ====================

async def insert_result(*, customer_file_id: int, import_task_id: int,
                        file_id: Optional[str], client_id: Optional[int],
                        doc_type: str, rule_id: Optional[int], rule_version: Optional[int],
                        status: str, skip_reason: Optional[str] = None,
                        extracted=None, mapped=None, write_stats=None,
                        error_msg: Optional[str] = None, elapsed_ms: Optional[int] = None) -> dict:
    async with async_session_maker() as session:
        row = DocExtractResult(
            customer_file_id=customer_file_id,
            import_task_id=import_task_id,
            file_id=file_id,
            client_id=client_id,
            doc_type=doc_type,
            rule_id=rule_id,
            rule_version=rule_version,
            status=status,
            skip_reason=skip_reason,
            extracted=extracted,
            mapped=mapped,
            write_stats=write_stats,
            error_msg=_clean_text(error_msg, 2000),
            elapsed_ms=elapsed_ms,
            created_at=datetime.now(),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_result_dict(row)


async def list_results(*, import_task_id: Optional[int] = None,
                       customer_file_id: Optional[int] = None,
                       file_id: Optional[str] = None,
                       doc_type: Optional[str] = None,
                       status: Optional[str] = None,
                       limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    async with async_session_maker() as session:
        stmt = select(DocExtractResult)
        cnt = select(func.count(DocExtractResult.id))
        for col, val in ((DocExtractResult.import_task_id, import_task_id),
                         (DocExtractResult.customer_file_id, customer_file_id),
                         (DocExtractResult.doc_type, doc_type),
                         (DocExtractResult.status, status)):
            if val is not None:
                stmt = stmt.where(col == val)
                cnt = cnt.where(col == val)
        if file_id:
            stmt = stmt.where(DocExtractResult.file_id.ilike(f"%{file_id}%"))
            cnt = cnt.where(DocExtractResult.file_id.ilike(f"%{file_id}%"))
        total = await session.scalar(cnt) or 0
        rows = (await session.execute(
            stmt.order_by(DocExtractResult.created_at.desc(), DocExtractResult.id.desc())
                .limit(limit).offset(offset)
        )).scalars().all()
        return [_to_result_dict(r) for r in rows], total


async def get_result(row_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        row = await session.get(DocExtractResult, row_id)
        return _to_result_dict(row) if row else None


async def update_result_review(result_id: int, *, status: str, corrected=None,
                               reviewed_by: Optional[str] = None) -> None:
    """复核结果:更新 review_status/corrected/reviewed_by/reviewed_at。"""
    async with async_session_maker() as session:
        row = await session.get(DocExtractResult, result_id)
        if row:
            row.review_status = status
            if corrected is not None:
                row.corrected = corrected
            row.reviewed_by = reviewed_by
            row.reviewed_at = datetime.now()
            await session.commit()


async def get_latest_result_for_file(customer_file_id: int) -> Optional[dict]:
    async with async_session_maker() as session:
        row = (await session.execute(
            select(DocExtractResult)
            .where(DocExtractResult.customer_file_id == customer_file_id)
            .order_by(DocExtractResult.id.desc()).limit(1)
        )).scalars().first()
        return _to_result_dict(row) if row else None
