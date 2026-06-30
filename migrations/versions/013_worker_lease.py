"""archive_detect_files 加 worker lease 字段 + 索引。

支持方案二 2b: 多 worker 进程通过 DB 抢任务。

Revision ID: 013_worker_lease
Revises: 012_system_events
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '013_worker_lease'
down_revision: Union[str, None] = '012_system_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'archive_detect_files',
        sa.Column('worker_lease_until', sa.DateTime(), nullable=True,
                  comment='worker 抢到任务后写入,超时未更新则被 watchdog 回收'),
    )
    op.add_column(
        'archive_detect_files',
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0',
                  comment='worker 失败后的重试次数,>= 1 时不再 retry'),
    )
    # local_path: upload 模式下,主进程把文件落到磁盘,worker 进程直接读这个路径
    # 之前内存态保存,改成 DB 持久化(worker 跨进程才能拿到)
    op.add_column(
        'archive_detect_files',
        sa.Column('local_path', sa.Text(), nullable=True,
                  comment='upload 模式下的本地文件绝对路径(worker 直读)'),
    )

    # partial index: 只索引 pending 行,worker 抢任务用
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_archive_detect_files_pending
        ON archive_detect_files (created_at)
        WHERE status = 'pending'
    """)

    # partial index: 只索引 leased 行,watchdog 回收用
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_archive_detect_files_leased_until
        ON archive_detect_files (worker_lease_until)
        WHERE status = 'leased'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_archive_detect_files_leased_until")
    op.execute("DROP INDEX IF EXISTS ix_archive_detect_files_pending")
    op.drop_column('archive_detect_files', 'local_path')
    op.drop_column('archive_detect_files', 'retry_count')
    op.drop_column('archive_detect_files', 'worker_lease_until')
