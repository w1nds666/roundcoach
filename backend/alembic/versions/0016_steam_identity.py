"""6R14: Steam identity + OpenID state + identity audit tables; users recent-auth/cooldown cols.

可回滚；现有 users 行 nullable 兼容。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "steam_identities",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=16), nullable=False, server_default="steam"),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("steam_id64", sa.String(length=17), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("bound_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("verified_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("unbound_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("provider", "subject", name="uq_steam_identity_provider_subject"),
        sa.UniqueConstraint("user_id", name="uq_steam_identity_user"),
        sa.UniqueConstraint("steam_id64", name="uq_steam_identity_sid"),
    )
    op.create_index("ix_steam_identities_steam_id64", "steam_identities", ["steam_id64"])

    op.create_table(
        "openid_states",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("realm", sa.String(length=512), nullable=False),
        sa.Column("return_to", sa.String(length=512), nullable=False),
        sa.Column("callback_url", sa.String(length=512), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("response_nonce", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_openid_states_state", "openid_states", ["state"])

    op.create_table(
        "identity_audits",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("steam_id64", sa.String(length=17), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.add_column("users", sa.Column("last_password_auth_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("steam_cooldown_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "steam_cooldown_until")
    op.drop_column("users", "last_password_auth_at")
    op.drop_table("identity_audits")
    op.drop_index("ix_openid_states_state", table_name="openid_states")
    op.drop_table("openid_states")
    op.drop_index("ix_steam_identities_steam_id64", table_name="steam_identities")
    op.drop_table("steam_identities")
