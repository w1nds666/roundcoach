"""players 比赛内玩家表（计划书 5.4）。"""
from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("matches.id"), index=True, nullable=False)
    steam_id: Mapped[str] = mapped_column(String(64), nullable=False)   # 仅内部使用，API 不得返回
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    team: Mapped[str] = mapped_column(String(8), nullable=False, default="")  # CT/T（最终阵营，6R1 起不用于个人 side）
    team_identity: Mapped[str | None] = mapped_column(String(1), nullable=True)  # 'A'|'B'（6R1：开局 roster 绑定，贯穿换边）
