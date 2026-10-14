"""创建 heatmap_cache 热力图缓存表（计划书 5.7）。

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "heatmap_cache",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("player_steam", sa.String(64), nullable=False, server_default=""),
        sa.Column("map_name", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),  # kills/route_all/route_ct/route_t
        sa.Column("heatmap_key", sa.String(255), nullable=False),  # MinIO key
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
    )
    op.create_index("ix_heatmap_cache_match_id", "heatmap_cache", ["match_id"])


def downgrade() -> None:
    op.drop_index("ix_heatmap_cache_match_id", table_name="heatmap_cache")
    op.drop_table("heatmap_cache")
