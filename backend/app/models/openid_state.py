"""OpenIdState：Steam OpenID 2.0 的客户端 state/nonce（一次性、短 TTL、绑定用户与 return target）。

防御：state 高熵 + 一次性 + 绑定当前站内 user/return_to；callback 命中后标记 used；response_nonce 记录用于防重放。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OpenIdState(Base):
    __tablename__ = "openid_states"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)  # 高熵 state
    user_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("users.id"), nullable=True)
    realm: Mapped[str] = mapped_column(String(512), nullable=False)
    return_to: Mapped[str] = mapped_column(String(512), nullable=False)        # 前端回跳路径（前端 return_to）
    callback_url: Mapped[str] = mapped_column(String(512), nullable=False)     # openid.return_to（用于精确匹配校验）
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response_nonce: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 消费到的 response_nonce（防重放）
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
