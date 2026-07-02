"""一次性脚本:修正历史批次的 done_files 计数。

背景: 业务审核(worker)路径下 done_files 从不被维护,一直停在初始值(reused_count)。
本脚本用真实 SQL 统计每个批次 status='done' 的文件数回填 done_files。

用法(在 backend/ 目录):
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ../.venv312/Scripts/python.exe backfill_done_files.py
"""
import asyncio
from sqlalchemy import text as sa_text
from db.engine import async_session_maker


async def main():
    async with async_session_maker() as session:
        # 用子查询算每个 batch 的真实 done 数,一条 UPDATE 全量回填
        stmt = sa_text("""
            UPDATE archive_detect_batches b
            SET done_files = sub.done_cnt,
                updated_at = now()
            FROM (
                SELECT batch_id, COUNT(*) FILTER (WHERE status = 'done') AS done_cnt
                FROM archive_detect_files
                GROUP BY batch_id
            ) sub
            WHERE b.batch_id = sub.batch_id
              AND b.done_files IS DISTINCT FROM sub.done_cnt
        """)
        res = await session.execute(stmt)
        await session.commit()
        print(f"回填完成: 修正了 {res.rowcount} 个批次的 done_files")


if __name__ == "__main__":
    asyncio.run(main())
