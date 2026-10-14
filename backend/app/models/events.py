"""events 事件表（进阶分析，计划书 5.6）。"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("matches.id"), index=True, nullable=False)
    tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # kill/death/grenade
    actor_steam: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    victim_steam: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weapon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    x: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # deprecated：旧兼容（见 actor_x/victim_x）
    y: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # 6R1：结构化坐标（actor=攻击者/事件发起者，victim=受害者；环境击杀 actor 坐标 NULL）
    actor_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    actor_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    victim_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    victim_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    grenade_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # smoke/flash/he/molotov
    flash_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 闪是否闪到人
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        # 索引：match_id + event_type（计划书 5.6 要求）
        Index("ix_events_match_type", "match_id", "event_type"),
    )
