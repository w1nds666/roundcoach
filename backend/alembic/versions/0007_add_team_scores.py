"""matches 表增加队伍比分字段（6R0：score_ct/score_t 为阵营胜局，队伍比分需 A/B）。

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("team_a_score", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("team_b_score", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("team_a_name", sa.String(64), nullable=True))
    op.add_column("matches", sa.Column("team_b_name", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("matches", "team_b_name")
    op.drop_column("matches", "team_a_name")
    op.drop_column("matches", "team_b_score")
    op.drop_column("matches", "team_a_score")
