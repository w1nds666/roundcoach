"""创建 ai_reports 表（Phase 5A：AI 诊断报告证据链）。

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("demo_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("provider", sa.String(32), nullable=False, server_default="fake"),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("metrics_snapshot", sa.JSON(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("token_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["demo_id"], ["demos.id"]),
        sa.UniqueConstraint("demo_id", "input_hash", "prompt_version", name="uq_ai_reports_demo_input_prompt"),
    )
    op.create_index("ix_ai_reports_user_id", "ai_reports", ["user_id"])
    op.create_index("ix_ai_reports_demo_id", "ai_reports", ["demo_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_reports_demo_id", table_name="ai_reports")
    op.drop_index("ix_ai_reports_user_id", table_name="ai_reports")
    op.drop_table("ai_reports")
