"""6R14: Max team workspace + membership roster + team audit tables.

可回滚；现有 users/teams 兼容。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_workspaces",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("team_workspaces.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("steam_id64", sa.String(length=17), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.Column("slot_type", sa.String(length=8), nullable=False, server_default="main"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("joined_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("team_id", "steam_id64", name="uq_team_membership_seat"),
    )
    op.create_index("ix_team_memberships_steam_id64", "team_memberships", ["steam_id64"])

    op.create_table(
        "team_audits",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("team_workspaces.id"), nullable=True),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("team_audits")
    op.drop_index("ix_team_memberships_steam_id64", table_name="team_memberships")
    op.drop_table("team_memberships")
    op.drop_table("team_workspaces")
