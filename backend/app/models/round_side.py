"""round_sides：每回合队伍 A/B 阵营（6R1 个人交战图 side 数据地基）。"""
from sqlalchemy import BigInteger, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RoundSide(Base):
    __tablename__ = "round_sides"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("matches.id"), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    side_a: Mapped[int] = mapped_column(Integer, nullable=False)   # 队伍 A 该回合阵营（2=CT/3=T）
    side_b: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_round_sides_match", "match_id", "round"),
    )
