"""ai_reports AI 诊断报告表（Phase 5A）。

记录每次报告生成的完整证据链：输入哈希（幂等）、prompt 版本、provider/model、
指标快照、结果 markdown、错误与 token/成本元数据。
"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIReport(Base):
    __tablename__ = "ai_reports"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("users.id"), index=True, nullable=False)
    demo_id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"), ForeignKey("demos.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending/running/succeeded/failed
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)   # sha256(metrics canonical)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="fake")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    metrics_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)   # 版本化指标快照（证据契约）
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)         # 稳定错误信息（不泄露内部细节）
    token_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # 同 demo + input_hash + prompt_version 幂等（唯一约束）
        __import__("sqlalchemy").UniqueConstraint(
            "demo_id", "input_hash", "prompt_version",
            name="uq_ai_reports_demo_input_prompt",
        ),
    )
