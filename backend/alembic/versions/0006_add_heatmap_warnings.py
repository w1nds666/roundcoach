"""demos 表增加热力图警告字段（Phase 5B P1：热力图失败不拖垮整场解析）。

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("demos", sa.Column("heatmap_warnings", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("demos", "heatmap_warnings")
