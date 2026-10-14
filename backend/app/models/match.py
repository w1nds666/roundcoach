"""matches 比赛表（计划书 5.3）。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    demo_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("demos.id"), unique=True, nullable=False)
    map_name: Mapped[str] = mapped_column(String(64), nullable=False)  # de_mirage 等
    score_ct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # 兼容：CT 阵营胜局（非队伍比分）
    score_t: Mapped[int] = mapped_column(Integer, nullable=False, default=0)    # 兼容：T 阵营胜局
    team_a_score: Mapped[int | None] = mapped_column(Integer, nullable=True)    # 6R0：队伍 A 比分（换边正确）
    team_b_score: Mapped[int | None] = mapped_column(Integer, nullable=True)    # 6R0：队伍 B 比分
    team_a_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 队名未知时为 "队伍 A"
    team_b_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
