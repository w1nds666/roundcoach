"""IdentityAudit：Steam 绑定/解绑/换绑/冲突 的全生命周期审计（6R14，P0 审计要求）。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IdentityAudit(Base):
    __tablename__ = "identity_audits"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=True)
    steam_id64: Mapped[str | None] = mapped_column(String(17), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # bind/unbind/rebind/conflict/expired
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
