"""6R4: dynamic per-player route replay artifact manifest."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "route_artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.BigInteger(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("player_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("round_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("match_id", name="uq_route_artifacts_match"),
    )
    op.create_index("ix_route_artifacts_match_id", "route_artifacts", ["match_id"])


def downgrade() -> None:
    op.drop_index("ix_route_artifacts_match_id", table_name="route_artifacts")
    op.drop_table("route_artifacts")
