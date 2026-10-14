"""6R19: 跨团队唯一 active Steam 部分唯一索引（team_memberships.steam_id64 WHERE status='active'）。

防止同一 Steam 同时属于多个.active 付费团队（B.4 门禁：跨团队席位共享/填充被 DB 级兜底）。
部分索引允许同一 Steam 在历史/移除座位上重复出现。

防御性：仅当 team_memberships 表存在时才建索引（隔离迁移测试以 stamp 0017 方式不建表，
此时 0019 应为 no-op；真实链路此前已由 0017 建表）。用 IF NOT EXISTS 保证幂等。

可回滚（drop index）。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = inspect(bind).get_table_names()
    if "team_memberships" not in tables:
        return
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_membership_active_steam "
        "ON team_memberships (steam_id64) WHERE status = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_team_membership_active_steam")
