"""6R15：长期 AI 教练快照表（match coach snapshot / profile coach snapshot）。

- 版本化（schema/metric version）+ content_hash（sha256）实现幂等去重与可追溯。
- 只纳入「解析完成 + 绑定本人确实在 roster」的 demo；身份解绑后停止新增且不可跨身份读取。
- 不输出完整 SteamID（API 一律按掩码）；facts/evidence 全部来自 validated 结构数据，绝不伪造。
"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MatchCoachSnapshot(Base):
    """每场每人一份的确定性 match 快照。

    幂等键：UniqueConstraint(steam_id64, demo_id, metric_version)。
    同一 (steam_id64, demo_id) 同一 metric_version 重跑：若 content_hash 相同则保留原行（去重），
    数据变化则重算覆盖。
    """

    __tablename__ = "match_coach_snapshots"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    steam_id64: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    demo_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("demos.id"), index=True, nullable=False
    )
    match_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("matches.id"), index=True, nullable=False
    )
    map_name: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)  # win | loss（无法判定则 None）
    score: Mapped[str | None] = mapped_column(String(16), nullable=True)   # 如 "13:9"（本人:对手）
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    match_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)   # match 时间（demo.created_at）
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint(
            "steam_id64", "demo_id", "metric_version", name="uq_match_coach_sid_demo_metric"
        ),
    )


class ProfileCoachSnapshot(Base):
    """长期画像聚合快照（5/10/30 窗口 + 重复问题/趋势/Top3 + 确定性中文）。

    幂等键：UniqueConstraint(steam_id64, metric_version)。
    重跑时若 content_hash 相同则保留原行；不同则覆盖。
    """

    __tablename__ = "profile_coach_snapshots"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    steam_id64: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    window: Mapped[int] = mapped_column(Integer, nullable=False, default=30)  # 5/10/30 窗口
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    windows: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    repeated_problems: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    trends: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    top3: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    states: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rendered: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("steam_id64", "metric_version", "window", name="uq_profile_coach_sid_metric_window"),
    )
