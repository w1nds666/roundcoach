"""6R15: long-term AI coach snapshots (match + profile).

revision: 0018 (chains from 0017); downgrade drops both tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "match_coach_snapshots",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("steam_id64", sa.String(length=17), nullable=False),
        sa.Column("demo_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("demos.id"), nullable=False),
        sa.Column("match_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("map_name", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=8), nullable=True),
        sa.Column("score", sa.String(length=16), nullable=True),
        sa.Column("rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("match_at", sa.DateTime(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("metric_version", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.UniqueConstraint("steam_id64", "demo_id", "metric_version", name="uq_match_coach_sid_demo_metric"),
    )
    op.create_index("ix_match_coach_snapshots_steam_id64", "match_coach_snapshots", ["steam_id64"])
    op.create_index("ix_match_coach_snapshots_demo_id", "match_coach_snapshots", ["demo_id"])
    op.create_index("ix_match_coach_snapshots_match_id", "match_coach_snapshots", ["match_id"])

    op.create_table(
        "profile_coach_snapshots",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("steam_id64", sa.String(length=17), nullable=False),
        sa.Column("window", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("metric_version", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("windows", sa.JSON(), nullable=False),
        sa.Column("repeated_problems", sa.JSON(), nullable=False),
        sa.Column("trends", sa.JSON(), nullable=False),
        sa.Column("top3", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("states", sa.JSON(), nullable=False),
        sa.Column("rendered", sa.JSON(), nullable=False),
        sa.UniqueConstraint("steam_id64", "metric_version", "window", name="uq_profile_coach_sid_metric_window"),
    )
    op.create_index("ix_profile_coach_snapshots_steam_id64", "profile_coach_snapshots", ["steam_id64"])


def downgrade() -> None:
    op.drop_index("ix_profile_coach_snapshots_steam_id64", table_name="profile_coach_snapshots")
    op.drop_table("profile_coach_snapshots")
    op.drop_index("ix_match_coach_snapshots_match_id", table_name="match_coach_snapshots")
    op.drop_index("ix_match_coach_snapshots_demo_id", table_name="match_coach_snapshots")
    op.drop_index("ix_match_coach_snapshots_steam_id64", table_name="match_coach_snapshots")
    op.drop_table("match_coach_snapshots")
