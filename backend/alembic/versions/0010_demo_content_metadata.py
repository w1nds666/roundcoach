"""6R3C1 demo content identity/source metadata."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("demos", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.add_column("demos", sa.Column("source", sa.String(16), server_default="upload", nullable=False))
    op.add_column("demos", sa.Column("source_match_id", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_demos_user_content_sha256", "demos", ["user_id", "content_sha256"])

def downgrade() -> None:
    op.drop_constraint("uq_demos_user_content_sha256", "demos", type_="unique")
    op.drop_column("demos", "source_match_id")
    op.drop_column("demos", "source")
    op.drop_column("demos", "content_sha256")
