"""doc_extract_crud 规则 CRUD 测试(依赖真实 DB,用独立测试 doc_type,测后清理)。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_doc_extract_rules.py
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import delete

from db import doc_extract_crud
from db.engine import async_session_maker
from db.models import DocExtractRule

_T = "test_rule_type"  # 独立测试类型,不碰真实规则
_FIELDS = [
    {"key": "name", "label": "姓名", "description": "", "required": True,
     "target": {"entity": "person", "column": "name"}, "example": "张三"},
    {"key": "id_number", "label": "公民身份号码", "description": "18 位", "required": True,
     "target": {"entity": "person", "column": "id_number"}, "example": "110101199001011234"},
]


async def _cleanup(ids):
    async with async_session_maker() as s:
        await s.execute(delete(DocExtractRule).where(DocExtractRule.id.in_(ids or [-1])))
        await s.commit()


async def _cleanup_all():
    async with async_session_maker() as s:
        await s.execute(delete(DocExtractRule).where(DocExtractRule.doc_type == _T))
        await s.commit()


def test_rule_lifecycle():
    created = []

    async def run():
        try:
            # 预清理历史测试残留(版本断言依赖干净起点)
            await _cleanup_all()

            # create: version 自增
            r1 = await doc_extract_crud.create_rule(doc_type=_T, fields=_FIELDS, drafted_by="ai")
            created.append(r1["id"])
            r2 = await doc_extract_crud.create_rule(doc_type=_T, fields=_FIELDS, drafted_by="ai")
            created.append(r2["id"])
            assert (r1["version"], r2["version"]) == (1, 2), (r1["version"], r2["version"])
            assert r1["status"] == "draft" and r2["status"] == "draft"

            # 此时无 active
            assert await doc_extract_crud.get_active_rule(_T) is None

            # draft 可编辑
            new_fields = _FIELDS + [{"key": "gender", "label": "性别", "description": "",
                                     "required": False,
                                     "target": {"entity": "person", "column": "gender"},
                                     "example": "男"}]
            r1u = await doc_extract_crud.update_rule_draft(r1["id"], fields=new_fields,
                                                           reviewed_by="tester")
            assert len(r1u["fields"]) == 3 and r1u["reviewed_by"] == "tester", r1u

            # activate r1 → 唯一 active
            r1a = await doc_extract_crud.activate_rule(r1["id"], reviewed_by="tester")
            assert r1a["status"] == "active" and r1a["reviewed_at"], r1a
            active = await doc_extract_crud.get_active_rule(_T)
            assert active and active["id"] == r1["id"], active

            # active 不可编辑
            try:
                await doc_extract_crud.update_rule_draft(r1["id"], fields=_FIELDS)
                raise AssertionError("active 不应可编辑")
            except ValueError as e:
                assert "draft" in str(e), str(e)

            # activate r2 → r2 active,r1 自动 disabled
            await doc_extract_crud.activate_rule(r2["id"])
            active = await doc_extract_crud.get_active_rule(_T)
            assert active and active["id"] == r2["id"], active
            r1_now = await doc_extract_crud.get_rule(r1["id"])
            assert r1_now["status"] == "disabled", r1_now

            # list 过滤
            items, total = await doc_extract_crud.list_rules(doc_type=_T, status="disabled")
            assert total == 1 and items[0]["id"] == r1["id"], (items, total)
            items, total = await doc_extract_crud.list_rules(doc_type=_T)
            assert total == 2, total

            # disable r2 → 无 active
            await doc_extract_crud.disable_rule(r2["id"])
            assert await doc_extract_crud.get_active_rule(_T) is None

            # activate 不存在的规则
            try:
                await doc_extract_crud.activate_rule(999999999)
                raise AssertionError("应当 LookupError")
            except LookupError:
                pass
        finally:
            # 同一事件循环内清理(async 引擎连接池绑 loop,不能开第二个 asyncio.run)
            await _cleanup(created)

    asyncio.run(run())


if __name__ == "__main__":
    test_rule_lifecycle()
    print("PASS test_rule_lifecycle")
    print("\n全部 1 个测试通过")
