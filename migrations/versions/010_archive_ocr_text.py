"""文件审核：archive_detect_files 加 ocr_text 列（存脱敏后的 OCR 识别文字）。

设计：
- OCR 原文经 redactor.redact() 脱敏后才写入（金额/身份证/银行卡/手机号/座机替换为占位符）
- 默认查询不拉 ocr_text（defer），只在单文件详情接口显式取，避免 N 文件轮询拉大文本
- 内存态 _batch_status 不存 ocr_text，只存 DB

Revision ID: 010_archive_ocr_text
Revises: 009_archive_audit
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '010_archive_ocr_text'
down_revision: Union[str, None] = '009_archive_audit'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'archive_detect_files',
        sa.Column('ocr_text', sa.Text(), nullable=True, comment='OCR识别文字（已脱敏）'),
    )


def downgrade() -> None:
    op.drop_column('archive_detect_files', 'ocr_text')
