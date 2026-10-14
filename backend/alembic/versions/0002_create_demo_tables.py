"""创建 demos/matches/players/player_stats/events 表（计划书 5.2-5.6）。

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "matches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("demo_id", sa.BigInteger(), nullable=False),
        sa.Column("map_name", sa.String(64), nullable=False),
        sa.Column("score_ct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_t", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parsed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_unique_constraint("uq_matches_demo_id", "matches", ["demo_id"])

    op.create_table(
        "demos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploaded"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("map_name", sa.String(64), nullable=True),
        sa.Column("match_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
    )
    op.create_index("ix_demos_user_id", "demos", ["user_id"])

    op.create_table(
        "players",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("steam_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("team", sa.String(8), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
    )
    op.create_index("ix_players_match_id", "players", ["match_id"])

    op.create_table(
        "player_stats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("kills", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deaths", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assists", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hs_kills", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hs_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("damage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("adr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_kills", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clutches_won", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weapon_stats", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
    )
    op.create_index("ix_player_stats_match_id", "player_stats", ["match_id"])

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("actor_steam", sa.String(64), nullable=False, server_default=""),
        sa.Column("victim_steam", sa.String(64), nullable=True),
        sa.Column("weapon", sa.String(32), nullable=True),
        sa.Column("x", sa.Float(), nullable=False, server_default="0"),
        sa.Column("y", sa.Float(), nullable=False, server_default="0"),
        sa.Column("grenade_type", sa.String(16), nullable=True),
        sa.Column("flash_success", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
    )
    op.create_index("ix_events_match_id", "events", ["match_id"])
    op.create_index("ix_events_match_type", "events", ["match_id", "event_type"])


def downgrade() -> None:
    op.drop_index("ix_events_match_type", table_name="events")
    op.drop_index("ix_events_match_id", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_player_stats_match_id", table_name="player_stats")
    op.drop_table("player_stats")
    op.drop_index("ix_players_match_id", table_name="players")
    op.drop_table("players")
    op.drop_index("ix_demos_user_id", table_name="demos")
    op.drop_table("demos")
    op.drop_constraint("uq_matches_demo_id", "matches", type_="unique")
    op.drop_table("matches")
