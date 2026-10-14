"""6R1 测试：tier 档位矩阵（free/pro/max、过期降级、MAX⊃PRO、entitlement）。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import User
from app.services import tier


def _user(tier_name="free", expire_days=None):
    u = User(email="x@y.com", password_hash="h", nickname="x", subscription_tier=tier_name)
    if expire_days is not None:
        u.subscription_expire = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expire_days)
    return u


def test_effective_tier_free_and_pro():
    assert tier.effective_tier(_user("free")) == "free"
    assert tier.effective_tier(_user("pro", 30)) == "pro"
    assert tier.effective_tier(_user("max", 30)) == "max"
    assert tier.effective_tier(None) == "free"


def test_expired_pro_max_downgrade_to_free():
    """过期 pro/max 一律降级 free（不泄漏原档位权限）。"""
    assert tier.effective_tier(_user("pro", -1)) == "free"
    assert tier.effective_tier(_user("max", -1)) == "free"
    # 无过期时间的 pro 视为有效（测试/管理端开通）
    assert tier.effective_tier(_user("pro")) == "pro"


def test_max_includes_pro_features():
    """MAX 包含 PRO 全部功能。"""
    for f in ("personal_analysis", "combat_map", "ai_report", "route_replay"):
        assert tier.has_feature(_user("pro", 30), f)
        assert tier.has_feature(_user("max", 30), f)
    assert tier.has_feature(_user("free"), "personal_analysis") is False


def test_team_analysis_only_max():
    assert tier.has_feature(_user("max", 30), "team_analysis") is True
    assert tier.has_feature(_user("pro", 30), "team_analysis") is False
    assert tier.has_feature(_user("free"), "team_analysis") is False


def test_require_tier_enforcement():
    from app.errors import ApiError

    # free 请求 pro 功能 → 403
    with pytest.raises(ApiError) as e:
        tier.require_tier(_user("free"), "pro")
    assert e.value.code == "SUBSCRIPTION_REQUIRED"
    # 过期 pro → 403 且提示过期
    with pytest.raises(ApiError) as e:
        tier.require_tier(_user("pro", -1), "pro")
    assert "过期" in e.value.message
    # pro 满足 pro；max 满足 pro
    tier.require_tier(_user("pro", 30), "pro")
    tier.require_tier(_user("max", 30), "pro")
    # max 请求 max
    tier.require_tier(_user("max", 30), "max")
    with pytest.raises(ApiError):
        tier.require_tier(_user("pro", 30), "max")


def test_entitlement_matrix_shape():
    m = tier.entitlement_matrix(_user("pro", 30))
    assert m["tier"] == "pro"
    assert m["expired"] is False
    assert m["features"]["personal_analysis"] is True
    assert m["features"]["team_analysis"] is False
    assert m["upload_daily_limit"] == 5
    m2 = tier.entitlement_matrix(_user("pro", -1))
    assert m2["tier"] == "free"
    assert m2["expired"] is True
