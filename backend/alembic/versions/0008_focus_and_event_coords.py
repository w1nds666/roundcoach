"""6R1：focus player 关联表 + events 结构化坐标 + demos.parse_warnings。

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # events 结构化坐标（actor=攻击者，victim=受害者；x/y deprecated）
    op.add_column("events", sa.Column("actor_x", sa.Float(), nullable=True))
    op.add_column("events", sa.Column("actor_y", sa.Float(), nullable=True))
    op.add_column("events", sa.Column("victim_x", sa.Float(), nullable=True))
    op.add_column("events", sa.Column("victim_y", sa.Float(), nullable=True))

    # demos 解析警告（score reconciliation 等）
    op.add_column("demos", sa.Column("parse_warnings", sa.Text(), nullable=True))

    # focus player 关联（每 user+demo 一个焦点玩家）
    op.create_table(
        "demo_focus_players",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("demo_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["demo_id"], ["demos.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.UniqueConstraint("user_id", "demo_id", name="uq_focus_user_demo"),
    )
    op.create_index("ix_focus_demo", "demo_focus_players", ["demo_id"])


def downgrade() -> None:
    op.drop_index("ix_focus_demo", table_name="demo_focus_players")
    op.drop_table("demo_focus_players")
    op.drop_column("demos", "parse_warnings")
    op.drop_column("events", "victim_y")
    op.drop_column("events", "victim_x")
    op.drop_column("events", "actor_y")
    op.drop_column("events", "actor_x")
