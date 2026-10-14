"""6R8: tactical replay artifact (v2.0) manifest."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tactical_artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.BigInteger(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("player_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prop_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("round_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("match_id", name="uq_tactical_artifacts_match"),
    )
    op.create_index("ix_tactical_artifacts_match_id", "tactical_artifacts", ["match_id"])


def downgrade() -> None:
    op.drop_index("ix_tactical_artifacts_match_id", table_name="tactical_artifacts")
    op.drop_table("tactical_artifacts")
