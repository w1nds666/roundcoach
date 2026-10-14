"""6R3C3 demo organization and recycle-bin fields."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("demos", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("demos", sa.Column("tags", sa.JSON().with_variant(JSONB(), "postgresql"), server_default="[]", nullable=False))
    op.add_column("demos", sa.Column("is_favorite", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("demos", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("demos", sa.Column("deleted_at", sa.DateTime(), nullable=True))

def downgrade() -> None:
    op.drop_column("demos", "deleted_at")
    op.drop_column("demos", "notes")
    op.drop_column("demos", "is_favorite")
    op.drop_column("demos", "tags")
    op.drop_column("demos", "display_name")
