"""demos 上传记录表（计划书 5.2）。"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, false, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Demo(Base):
    __tablename__ = "demos"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("users.id"), index=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)  # MinIO key
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploaded")  # uploaded/parsing/parsed/failed
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    heatmap_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)  # 热力图失败警告（不阻断解析）
    parse_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)    # 解析一致性警告（6R1：score reconciliation）
    map_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("matches.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="upload", server_default="upload")
    source_match_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list, server_default="[]")
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "content_sha256", name="uq_demos_user_content_sha256"),)
