"""SteamIdentity：站内账号与 Steam 身份的事实绑定（6R14）。

设计（MVP 一个站内账号至多一个 Steam）：
- provider+subject 唯一（subject=steam_id64）
- user_id 唯一（但可空：解绑后置 NULL，保留历史行；NULL 在 UNIQUE 下可重复，允许后续重绑）
- steam_id64 唯一
- status: active | unbound（解绑暂停权益，不删除历史）
- bound_at / verified_at / last_verified_at / version
audit：绑定的全生命周期事件由 IdentityAudit 记录。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SteamIdentity(Base):
    __tablename__ = "steam_identities"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="steam")
    subject: Mapped[str] = mapped_column(String(64), nullable=False)  # steam 时 = steam_id64
    steam_id64: Mapped[str] = mapped_column(String(17), nullable=False, index=True, unique=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active | unbound
    bound_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    verified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 6R21：Steam persona 名（只读徽标，与 users.nickname 分层持久化；绑定/解绑绝不覆盖本地昵称）。
    persona_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_steam_identity_provider_subject"),
        UniqueConstraint("user_id", name="uq_steam_identity_user"),
        UniqueConstraint("steam_id64", name="uq_steam_identity_sid"),
    )
