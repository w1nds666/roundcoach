"""DeepSeek 定价与成本保护（Phase 5B，版本化配置，便于官方调价）。

价格基准：2026-08-15 DeepSeek 官方（人民币 / 1M tokens）：
- V4-Flash：输入 cache hit ¥0.02、cache miss ¥1、输出 ¥2
- V4-Pro：输入 cache hit ¥0.025、cache miss ¥3、输出 ¥6

注意：文档估算不可当账单；最终以 API usage 回填为准。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.config import settings
from app.models import AIReport

# 版本化价格表（元 / 1M tokens）
PRICING = {
    "v1": {
        "deepseek-v4-flash": {"input_cache_hit": 0.02, "input_cache_miss": 1.0, "output": 2.0},
        "deepseek-v4-pro": {"input_cache_hit": 0.025, "input_cache_miss": 3.0, "output": 6.0},
    }
}


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int,
                      cached_tokens: int = 0, pricing_version: str = "v1") -> float:
    """按版本化价格表估算人民币成本（输入 cache hit/miss 分开计）。"""
    table = PRICING.get(pricing_version, PRICING["v1"]).get(model)
    if table is None:
        return 0.0
    miss = max(prompt_tokens - cached_tokens, 0)
    cost = (cached_tokens * table["input_cache_hit"] + miss * table["input_cache_miss"]
            + completion_tokens * table["output"]) / 1_000_000
    return round(cost, 6)


def check_monthly_budget(db, extra_cost_cny: float) -> bool:
    """月预算检查：本月累计成本 + 本次估算 ≤ 预算则放行（稳定拒绝，不静默扣费）。"""
    if settings.monthly_budget_cny <= 0:
        return True  # 0 或负值视为不设限（保守默认在配置层保证）
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar(
        select(func.coalesce(func.sum(AIReport.cost_cents), 0)).where(
            AIReport.created_at >= month_start,
            AIReport.status == "succeeded",
        )
    ) or 0
    return (used / 100.0) + extra_cost_cny <= settings.monthly_budget_cny
