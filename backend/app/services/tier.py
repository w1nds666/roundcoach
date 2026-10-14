"""统一档位与权限（6R1：free/pro/max + 集中 require_tier）。

- TIER_RANK：free=0 < pro=1 < max=2（MAX 包含 PRO 权限）
- effective_tier：订阅过期 → 降级 free（pro/max 均适用）
- require_tier(user, required)：不满足抛 ApiError（SUBSCRIPTION_REQUIRED + 稳定提示）
- entitlement matrix：单一来源，供只读 entitlement API 与前端展示

各 API 不得再散落 _is_free/pro 布尔判断，统一走本模块。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.errors import SUBSCRIPTION_REQUIRED, ApiError
from app.models import User

TIER_RANK = {"free": 0, "pro": 1, "max": 2}
TIER_LABEL = {"free": "免费版", "pro": "进阶版", "max": "团队版"}

# 档位功能矩阵（单一来源；entitlement API 与前端共用）
FEATURES = {
    "match_result": {"free": True, "pro": True, "max": True},      # 基础但真实的比赛结果/记分板
    "basic_stats": {"free": True, "pro": True, "max": True},        # 公共记分板/基础统计预览
    "kills_heatmap": {"free": True, "pro": True, "max": True},      # 全场击杀热力图（Free 预览）
    "personal_analysis": {"free": False, "pro": True, "max": True}, # 个人复盘（击杀/死亡/摘要）
    "coach": {"free": False, "pro": True, "max": True},            # 长期 AI 教练（6R15）
    "combat_map": {"free": False, "pro": True, "max": True},        # 个人交战图（坐标）
    "route_replay": {"free": False, "pro": True, "max": True},      # 单回合路线（6R2）
    "team_analysis": {"free": False, "pro": False, "max": True},    # 团队级深度配合（MAX）
    "ai_report": {"free": False, "pro": True, "max": True},         # AI 诊断报告
    "upload_daily_limit": {"free": 1, "pro": 5, "max": 20},         # 每日上传数
}

# 订阅过期提示（pro/max 共用）
EXPIRED_HINT = "订阅已过期，已降级为免费版；续费后恢复进阶功能"


def effective_tier(user: User | None) -> str:
    """实际生效档位：无用户/过期 → free。"""
    if user is None or user.subscription_tier not in TIER_RANK:
        return "free"
    if user.subscription_tier == "free":
        return "free"
    expire = user.subscription_expire
    if expire is not None and expire < datetime.now(timezone.utc).replace(tzinfo=None):
        return "free"
    return user.subscription_tier


def has_feature(user: User | None, feature: str) -> bool:
    """档位是否包含功能（MAX 包含 PRO）。"""
    tier = effective_tier(user)
    return bool(FEATURES.get(feature, {}).get(tier, False))


def require_tier(user: User | None, required: str, feature_hint: str = "") -> None:
    """要求至少 required 档位；不满足抛稳定 403。"""
    if user is None:
        raise ApiError(403, SUBSCRIPTION_REQUIRED, "请先登录后使用该功能")
    tier = effective_tier(user)
    if TIER_RANK.get(tier, 0) < TIER_RANK.get(required, 0):
        if user.subscription_tier != "free" and tier == "free":
            raise ApiError(403, SUBSCRIPTION_REQUIRED, EXPIRED_HINT)
        hint = feature_hint or f"该功能属于{TIER_LABEL.get(required, required)}，升级后解锁"
        raise ApiError(403, SUBSCRIPTION_REQUIRED, hint)


def entitlement_matrix(user: User | None) -> dict:
    """只读 entitlement 视图（供 GET /api/entitlement 与前端显示/锁定）。"""
    tier = effective_tier(user)
    identity = {
        "requires_steam_binding": bool(tier in ("pro", "max")),  # 个人付费内容需绑定 Steam 身份
        "personal_features_require_identity": bool(tier in ("pro", "max")),
    }
    return {
        "tier": tier,
        "tier_label": TIER_LABEL.get(tier, tier),
        "subscription_tier": user.subscription_tier if user else None,
        "expired": bool(user and user.subscription_tier != "free" and tier == "free"),
        "identity": identity,
        "features": {
            feature: value.get(tier, False)
            for feature, value in FEATURES.items()
        },
        "upload_daily_limit": FEATURES["upload_daily_limit"].get(tier, 1),
    }
