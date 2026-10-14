"""Max 团队工作区与实名 roster（6R14）。

- TeamWorkspace：一个 Max 账号最多一个 active 团队工作区（服务层强校验 + 事务收口）。
- TeamMembership：成员占座以已验证的 Steam identity 为准；5 主力 + 最多 2 替补（5-7 seats）；
  唯一 Steam 同一时刻最多属于一个 active 付费团队（服务层校验 + 事务）。
- 邀请/接受基于已验证站内账号，不允许 owner 手填 SteamID 冒充。
- roster 变更通过 TeamAudit 审计；增删/退出/转队有 7 天冷却。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TeamWorkspace(Base):
    __tablename__ = "team_workspaces"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    # 说明：一个 Max owner 最多一个团队工作区由服务层（create_workspace）检查 + 对 owner 行 FOR UPDATE 串行化收口。
    # 未加 DB 级 UNIQUE(owner_user_id)：既有 E2E 播种会为同一 owner 在多次运行中累积多个「无 active 座位」的孤儿
    # workspace，若加 DB UNIQUE 会迁移失败且破坏测试播种（协调项，见 phase6r19-gate-evidence 残留风险）。


class TeamMembership(Base):
    __tablename__ = "team_memberships"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("team_workspaces.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=False)
    steam_id64: Mapped[str] = mapped_column(String(17), nullable=False)   # 已验证占座 identity
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")  # owner/admin/member
    slot_type: Mapped[str] = mapped_column(String(8), nullable=False, default="main")  # main/sub
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active/removed/left
    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("team_id", "steam_id64", name="uq_team_membership_seat"),
        # 6R19（B.4）：同一 Steam 同一时刻最多属于一个 active 团队——跨团队部分唯一索引（防跨团队席位共享）。
        # 部分索引（WHERE status='active'）允许同一 Steam 在历史/移除座位中重复出现。
        Index("uq_team_membership_active_steam", "steam_id64", unique=True,
              sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
    )


class TeamAudit(Base):
    __tablename__ = "team_audits"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    team_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("team_workspaces.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # create/invite/accept/remove/leave/transfer/promote/demote/gate
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
