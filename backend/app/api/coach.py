"""6R15：长期 AI 教练 API（画像 / 状态 / 权益门禁 / AI 表达）。

- 主体 = 当前登录用户 active SteamIdentity；任何 query/body/focus/cache key 都不能切换他人。
- Free → 锁定预览（/status 返回 locked + 已纳入场次）；Pro → 最近 10 场；Max → 最近 30 场 + 10/30 对照。
- 降级立即收紧读取；历史快照可保留但不越权；不输出完整 SteamID。
- AI 表达：仅 trusted + matching snapshot 可缓存表达；provider=fake/未启用 → 退回确定性中文状态。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.errors import ApiError
from app.models import User
from app.services.coach import mask_steam, profile_snapshot
from app.services.identity import get_active_identity
from app.services.tier import effective_tier, require_tier

router = APIRouter(prefix="/api/coach", tags=["coach"])

COACH_HINT = "长期教练属于进阶版（Pro/Max）功能，升级后解锁"


def _max_window(tier: str) -> int:
    return 10 if tier == "pro" else 30


@router.get("/status")
def coach_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """只读状态：档位 / 是否锁定 / 最大窗口 / 绑定与覆盖预览（供入口卡与锁定态）。"""
    tier = effective_tier(current_user)
    bound = get_active_identity(db, current_user) is not None
    return {
        "tier": tier,
        "locked": tier == "free",
        "requires_steam_binding": tier in ("pro", "max"),
        "max_window": _max_window(tier) if tier in ("pro", "max") else 0,
        "bound": bound,
        "note": "绑定 Steam 并完成复盘（demo 解析且你在 roster）后自动累计长期画像" if bound else
                "请先绑定 Steam 才能使用长期教练",
    }


@router.get("/profile")
def coach_profile(
    window: int = Query(30, ge=5, le=30),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """长期教练画像。window ∈ {10,30}；按档位收紧（Pro→10，Max→30）。"""
    tier = effective_tier(current_user)
    require_tier(current_user, "pro", COACH_HINT)
    eff_window = min(window, _max_window(tier))  # Pro 请求 30 → 只给 10；Max 最多 30
    snap = profile_snapshot(db, current_user, window=eff_window)

    # AI 表达层：仅 trusted + provider 非 fake 才可能渲染；本阶段默认 fake → 退回确定性中文。
    ai_status = "fake" if settings.ai_provider == "fake" else ("disabled" if not settings.allow_external_llm else "unavailable")
    rendered = {
        "deterministic": snap.rendered["deterministic"],
        "ai": {"enabled": False, "body": None, "status": ai_status},
    }
    return {
        "steam_id64_masked": mask_steam(snap.steam_id64),
        "schema_version": snap.schema_version,
        "metric_version": snap.metric_version,
        "content_hash": snap.content_hash,
        "generated_at": snap.generated_at.isoformat() if snap.generated_at else None,
        "window": eff_window,
        "coverage": snap.coverage,
        "windows": snap.windows,
        "repeated_problems": snap.repeated_problems,
        "trends": snap.trends,
        "top3": snap.top3,
        "summary": snap.summary,
        "states": snap.states,
        "rendered": rendered,
        "privileges": {"tier": tier, "max_window": _max_window(tier),
                       "can_toggle_30": tier == "max"},
    }
