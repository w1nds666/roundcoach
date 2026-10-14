"""heatmap_cache 热力图缓存表（计划书 5.7）。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HeatmapCache(Base):
    __tablename__ = "heatmap_cache"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("matches.id"), index=True, nullable=False)
    player_steam: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    map_name: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # kills/route_all/route_ct/route_t
    heatmap_key: Mapped[str] = mapped_column(String(255), nullable=False)  # MinIO key
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
