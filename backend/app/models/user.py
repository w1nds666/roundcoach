"""users 用户表（计划书 5.1）。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    # 生产 PostgreSQL 用 BIGSERIAL；SQLite（本地测试）退化为 INTEGER 自增
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # bcrypt
    nickname: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="")  # perfect/5e/faceit
    rank: Mapped[str] = mapped_column(String(16), nullable=False, default="")  # A/B/C/S 等
    subscription_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")  # free/pro
    subscription_expire: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ai_consent_version: Mapped[str | None] = mapped_column(String(16), nullable=True)   # AI 外发同意版本（Phase 5B）
    ai_consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 6R14：recent-auth（解绑/换绑需最近 15 分钟密码再认证）与 Steam 换绑冷却
    last_password_auth_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    steam_cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
