"""重审/重跑迁移到 worker DB 队列。

新增两列:
- archive_detect_files.reuse_ocr_text: 重审入队时预填源文件已有 ocr_text,
  worker 见到非空则跳过下载+OCR,直接用它跑 LLM(AI-only 复用)。
- archive_detect_batches.stage: 存 pre_submit|post_submit,worker 读它传给 LLM,
  不再写死 post_submit。

Revision ID: 015_recheck_to_queue
Revises: 014_api_request_logs
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '015_recheck_to_queue'
down_revision: Union[str, None] = '014_api_request_logs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'archive_detect_files',
        sa.Column('reuse_ocr_text', sa.Text(), nullable=True,
                  comment='重审入队时预填的源 OCR 文本;worker 非空则跳过下载+OCR 直接跑 LLM'),
    )
    op.add_column(
        'archive_detect_batches',
        sa.Column('stage', sa.String(length=20), nullable=True,
                  comment='审核阶段 pre_submit|post_submit;worker 读取传给 LLM'),
    )


def downgrade() -> None:
    op.drop_column('archive_detect_batches', 'stage')
    op.drop_column('archive_detect_files', 'reuse_ocr_text')
