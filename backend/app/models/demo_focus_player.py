"""demo_focus_players：每用户每 demo 的焦点玩家（6R1「选择我」）。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DemoFocusPlayer(Base):
    __tablename__ = "demo_focus_players"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("users.id"), nullable=False)
    demo_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("demos.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("players.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "demo_id", name="uq_focus_user_demo"),
    )
