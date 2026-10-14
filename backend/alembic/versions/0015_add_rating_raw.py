"""6R10B: add player_stats.rating_raw (Rating Pro v1 raw component snapshot).

在解析期计算并存储 Rating Pro 的原始分量（frag/survival/kast/adr/trade），
供读取时按校准常数与权重归一化，避免每次请求重复解析 demo。
rating_raw 为 JSON，nullable（旧数据为空 → Rating Pro 标记 unavailable）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("player_stats", sa.Column("rating_raw", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("player_stats", "rating_raw")
