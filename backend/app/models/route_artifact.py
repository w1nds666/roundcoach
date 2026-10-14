"""route_artifacts：动态路线回放 artifact 的 manifest（6R4）。

每个 match 一份版本化压缩 JSON artifact 存 MinIO，本表记录可查询的
manifest（MinIO key + 校验和 + 版本），供 /personal/route 定位与校验。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RouteArtifact(Base):
    __tablename__ = "route_artifacts"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("matches.id"), index=True, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)   # 路线数据契约版本
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)      # MinIO key（gzip JSON）
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)           # 压缩 artifact 的 sha256（幂等/追溯）
    player_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    round_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("match_id", name="uq_route_artifacts_match"),
    )
