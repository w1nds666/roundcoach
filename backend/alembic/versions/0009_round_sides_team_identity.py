"""6R1：round_sides（每回合队伍阵营）+ players.team_identity（队伍身份 A/B）。

个人交战图的 side 必须用玩家在该回合的真实阵营（非最终 team 字段）。
Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("players", sa.Column("team_identity", sa.String(1), nullable=True))  # 'A'|'B'（开局绑定）
    op.create_table(
        "round_sides",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("side_a", sa.Integer(), nullable=False),   # 队伍 A 该回合阵营（2=CT/3=T）
        sa.Column("side_b", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
    )
    op.create_index("ix_round_sides_match", "round_sides", ["match_id", "round"])


def downgrade() -> None:
    op.drop_index("ix_round_sides_match", table_name="round_sides")
    op.drop_table("round_sides")
    op.drop_column("players", "team_identity")
