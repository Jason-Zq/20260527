"""admin_daily_report 每日留底检测报告聚合测试(依赖真实 DB,测后清理)。

造数: 2099-01-15(未来日期,与真实数据天然隔离) 两个客户 + 一个无客户批次,
覆盖 match/partial/mismatch/other(done 无判定)/error/in_progress 六种桶;
2099-01-16 一个批次验证跨日隔离。

  cd e:/qoderproject/20260527
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_daily_report.py
"""
import sys
import os
import asyncio
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from db import archive_detect_crud
from db.engine import async_session_maker
from db.models import ArchiveDetectBatch, ArchiveDetectProgress, Client
from sqlalchemy import delete as sa_delete

DAY1 = "2099-01-15"
DAY2 = "2099-01-16"


def _ts(day: str, hour: int = 10) -> datetime:
    return datetime.strptime(f"{day} {hour}:00:00", "%Y-%m-%d %H:%M:%S")


async def _setup(suffix: str):
    """建 2 客户 + 2 进展包 + 9 个批次(8 个在 DAY1,1 个在 DAY2)。返回 (client_ids, progress_ids)。"""
    async with async_session_maker() as s:
        ca = Client(name=f"日报测试甲-{suffix}", client_code=f"DRT-A-{suffix}")
        cb = Client(name=f"日报测试乙-{suffix}", client_code=f"DRT-B-{suffix}")
        s.add_all([ca, cb])
        await s.flush()
        pa = ArchiveDetectProgress(client_id=ca.id, progress_oid=f"DRT-PA-{suffix}", handler="测试办理人",
                                   project_name="项目A", progress_name="进展A")
        pb = ArchiveDetectProgress(client_id=cb.id, progress_oid=f"DRT-PB-{suffix}", handler="测试办理人",
                                   project_name="项目B", progress_name="进展B")
        s.add_all([pa, pb])
        await s.flush()

        def batch(i, day, status, verdict=None, score=None, progress_id=None, total_files=2):
            return ArchiveDetectBatch(
                batch_id=f"drtest-{suffix}-{i}", user_prompt="测试标准", source_kind="batch",
                status=status, overall_verdict=verdict, overall_score=score,
                total_files=total_files, done_files=total_files if status in ("done", "error") else 0,
                progress_id=progress_id, created_at=_ts(day), updated_at=_ts(day),
            )

        s.add_all([
            # 客户甲: 5 批,五桶各一
            batch(1, DAY1, "done", "match", 90, pa.id, 3),
            batch(2, DAY1, "done", "partial", 70, pa.id),
            batch(3, DAY1, "done", "mismatch", 30, pa.id),
            batch(4, DAY1, "running", None, None, pa.id),
            batch(5, DAY1, "error", None, None, pa.id),
            # 客户乙: 2 批, match + done 无判定(other)
            batch(6, DAY1, "done", "match", 80, pb.id, 4),
            batch(7, DAY1, "done", None, None, pb.id),
            # 无客户批次(历史 quick 形态)
            batch(8, DAY1, "done", "match", 60, None, 1),
            # 次日批次: 不应进 DAY1 报告
            batch(9, DAY2, "done", "mismatch", 10, pa.id),
        ])
        await s.commit()
        return [ca.id, cb.id], [pa.id, pb.id]


async def _cleanup(suffix: str, client_ids, progress_ids):
    async with async_session_maker() as s:
        await s.execute(sa_delete(ArchiveDetectBatch).where(ArchiveDetectBatch.batch_id.like(f"drtest-{suffix}-%")))
        await s.execute(sa_delete(ArchiveDetectProgress).where(ArchiveDetectProgress.id.in_(progress_ids)))
        await s.execute(sa_delete(Client).where(Client.id.in_(client_ids)))
        await s.commit()


async def main():
    suffix = uuid.uuid4().hex[:8]
    client_ids, progress_ids = await _setup(suffix)
    try:
        rep = await archive_detect_crud.admin_daily_report(DAY1)

        # ---- 全量合计 ----
        t = rep["totals"]
        assert rep["date"] == DAY1
        assert t["batches"] == 8, t
        assert t["files"] == 3 + 2 + 2 + 2 + 2 + 4 + 2 + 1, t
        assert t["clients"] == 3, t
        assert (t["match"], t["partial"], t["mismatch"]) == (3, 1, 1), t
        assert t["in_progress"] == 1 and t["error"] == 1 and t["other"] == 1, t
        # avg_score: done 且有分 = [90,70,30,80,60] -> 66.0
        assert t["avg_score"] == 66.0, t

        # ---- 客户分桶(按批次数降序: 甲5 > 乙2 > 无客户1) ----
        rows = {r["name"]: r for r in rep["clients"]}
        assert [r["batches"] for r in rep["clients"]] == [5, 2, 1], rep["clients"]

        a = rows[f"日报测试甲-{suffix}"]
        assert (a["match"], a["partial"], a["mismatch"], a["in_progress"], a["error"], a["other"]) == (1, 1, 1, 1, 1, 0), a
        assert a["avg_score"] == round((90 + 70 + 30) / 3, 1), a
        assert a["client_code"] == f"DRT-A-{suffix}"

        b = rows[f"日报测试乙-{suffix}"]
        assert (b["match"], b["other"]) == (1, 1) and b["avg_score"] == 80.0, b

        nc = rows["（无客户信息）"]
        assert nc["client_id"] is None and nc["match"] == 1 and nc["batches"] == 1, nc

        # ---- 跨日隔离: DAY2 只有 1 个 mismatch ----
        rep2 = await archive_detect_crud.admin_daily_report(DAY2)
        assert rep2["totals"]["batches"] == 1 and rep2["totals"]["mismatch"] == 1, rep2["totals"]
        assert rep2["totals"]["clients"] == 1, rep2["totals"]

        # ---- 空日期 ----
        rep3 = await archive_detect_crud.admin_daily_report("2099-02-01")
        assert rep3["totals"]["batches"] == 0 and rep3["clients"] == [], rep3
    finally:
        await _cleanup(suffix, client_ids, progress_ids)


if __name__ == "__main__":
    asyncio.run(main())
    print("All tests passed.")
