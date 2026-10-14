"""player_stats 玩家统计表（基础版，计划书 5.5）。"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlayerStats(Base):
    __tablename__ = "player_stats"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("matches.id"), index=True, nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("players.id"), nullable=False)
    kills: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deaths: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assists: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hs_kills: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hs_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    damage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    adr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    first_kills: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clutches_won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weapon_stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # {武器: {kills, dmg}}
    # 6R10B：Rating Pro v1 原始分量（解析期计算）；无则为 None（Rating Pro 显示 unavailable）
    rating_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
