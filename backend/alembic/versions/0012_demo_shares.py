"""6R3D2 revocable and expiring redacted report shares."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demo_shares",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("demo_id", sa.BigInteger(), sa.ForeignKey("demos.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("demo_id", name="uq_demo_shares_demo_id"),
        sa.UniqueConstraint("token_hash", name="uq_demo_shares_token_hash"),
    )
    op.create_index("ix_demo_shares_user_id", "demo_shares", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_demo_shares_user_id", table_name="demo_shares")
    op.drop_table("demo_shares")
