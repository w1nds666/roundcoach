"""6R21: steam_identities.persona_name —— 分层持久化 Steam persona 名（只读徽标）。

- persona_name 仅来自 Steam OpenID 回带（openid.sreg.nickname），存于 steam_identities；
  **绝不写 users.nickname**（本地昵称由用户设置，绑定/解绑/取消/回退保持不变）。
- 幂等：列存在则跳过（SQLite 测试用 create_all 自建，不依赖本迁移；真实链路 Alembic 走本迁移）。
- 可回滚（drop column）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(table: str, col: str) -> bool:
    inspector = inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return col in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "steam_identities" not in inspect(bind).get_table_names():
        return   # 防御性：隔离迁移测试预置 DB 未必建该表，此时 no-op（真实链路已由 0016 建表）
    if not _has_col("steam_identities", "persona_name"):
        op.add_column("steam_identities", sa.Column("persona_name", sa.String(64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "steam_identities" not in inspect(bind).get_table_names():
        return
    if _has_col("steam_identities", "persona_name"):
        op.drop_column("steam_identities", "persona_name")
