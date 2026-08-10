"""028_prompts_apply_overall1

提示词库「应用到总体1」开关：

archive_detect_prompts 加 apply_to_overall1 列（NOT NULL DEFAULT FALSE）。
开启后，该五元组批次的总体判定1 改走提示词库驱动（prompt1 模板 + prompt2 项目留底标准），
不再使用业务方提交批次时传的 criteria；关闭则回到硬编码默认模板 + criteria 的原有路径。

Revision ID: 028_prompts_apply_overall1
Revises: 027_drop_legacy_client_archive
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '028_prompts_apply_overall1'
down_revision: Union[str, None] = '027_drop_legacy_client_archive'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('archive_detect_prompts',
                  sa.Column('apply_to_overall1', sa.Boolean(), nullable=False,
                            server_default=sa.text('false'),
                            comment='应用到总体1：TRUE 时该五元组批次的总体判定1 改用 prompt1 模板+prompt2 标准驱动'))


def downgrade() -> None:
    op.drop_column('archive_detect_prompts', 'apply_to_overall1')
