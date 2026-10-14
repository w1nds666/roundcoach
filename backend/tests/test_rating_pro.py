"""6R10B：Rating Pro v1 合约测试。

性质：权重和为 1、确定性、零机会不当作 0 分、字段缺失/零回合不产生 NaN/Infinity、
KAST 含可证明 traded、分量为权威字段、provisional 标注。
"""
import pytest

from app.services.rating_pro import (
    WEIGHTS, RATING_PRO_VERSION, PROVISIONAL, REFERENCE_SCOPE,
    build_rating, compute_rating_raw,
)


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_version_and_provisional():
    assert RATING_PRO_VERSION == "1.0"
    assert PROVISIONAL is True
    assert "非 HLTV" in REFERENCE_SCOPE or "行业" in REFERENCE_SCOPE or "基线" in REFERENCE_SCOPE


_RAW_BASE = {
    "schema": "rating_raw_v1", "version": "1.0", "rounds": 20, "tick_rate": 64.0,
    "components": {
        "frag": {"numerator": 20.0, "denominator": 20, "value": 1.0},
        "survival": {"numerator": 10, "denominator": 20, "value": 0.5},
        "kast": {"numerator": 15, "denominator": 20, "value": 0.75},
        "adr": {"value": 80.0},
        "trade": {"numerator": 3, "denominator": 6, "value": 0.5, "status": "ok"},
    },
}

_CAL = {"frag": 0.7, "survival": 0.5, "kast": 0.7, "adr": 70.0, "trade": 0.45}


def test_build_rating_available_with_all_components():
    r = build_rating(_RAW_BASE, cal=_CAL)
    assert r["status"] == "available"
    assert isinstance(r["rating"], float)
    assert 0.0 <= r["rating"] <= 3.0
    assert r["version"] == RATING_PRO_VERSION
    assert r["confidence"] in ("high", "medium", "low")
    assert r["coverage"]["rounds"] == 20
    assert len(r["components"]) == 5
    assert r["provisional"] is True
    # 分量都有值
    for c in r["components"]:
        assert c["status"] == "ok"
        assert c["weight"] > 0


def test_build_rating_zero_opportunity_not_zero():
    """trade=no_opportunity → 不计分（排除并重归一化），而不是当作 0 分。"""
    raw = {**_RAW_BASE, "components": {
        **_RAW_BASE["components"],
        "trade": {"numerator": 0, "denominator": 0, "value": None, "status": "no_opportunity"},
    }}
    r = build_rating(raw, cal=_CAL)
    assert r["status"] == "available"
    assert r["rating"] is not None and r["rating"] > 0  # 不被 trade 拉成 0
    assert any(e["component"] == "trade" and e["status"] == "no_opportunity" for e in r["exclusions"])
    # 可用权重和 = 1 - trade 权重
    avail_w = sum(c["weight"] for c in r["components"] if c["status"] == "ok")
    assert abs(avail_w - 0.90) < 1e-9


def test_build_rating_all_unavailable_returns_null():
    """全部分量不可用（缺数据/无机会）→ rating=None + 原因，不给出 0 分。"""
    raw = {**_RAW_BASE, "components": {
        **_RAW_BASE["components"],
        "frag": {"numerator": 0, "denominator": 20, "value": None},
        "survival": {"numerator": 0, "denominator": 20, "value": None},
        "kast": {"numerator": 0, "denominator": 20, "value": None},
        "adr": {"value": None},
        "trade": {"numerator": 0, "denominator": 0, "value": None, "status": "no_opportunity"},
    }}
    r = build_rating(raw, cal=_CAL)
    assert r["rating"] is None
    assert r["status"] == "unavailable"
    assert r["reason"]


def test_build_rating_null_raw_returns_unavailable():
    r = build_rating(None, cal=_CAL)
    assert r["rating"] is None
    assert r["status"] == "unavailable"


def test_build_rating_zero_rounds_returns_unavailable():
    raw = {**_RAW_BASE, "rounds": 0, "components": {**_RAW_BASE["components"]}}
    r = build_rating(raw, cal=_CAL)
    assert r["rating"] is None
    assert r["status"] == "unavailable"


def test_build_rating_no_nan_infinity():
    raw = {**_RAW_BASE, "components": {
        "frag": {"numerator": float("nan"), "denominator": 20, "value": float("nan")},
        "survival": {**_RAW_BASE["components"]["survival"]},
        "kast": {**_RAW_BASE["components"]["kast"]},
        "adr": {"value": float("inf")},
        "trade": {"numerator": 0, "denominator": 0, "value": None, "status": "no_opportunity"},
    }}
    r = build_rating(raw, cal=_CAL)
    # NaN/Inf 分量要么被排除，要么被转换，绝不进入 rating
    assert r["rating"] is None or isinstance(r["rating"], float)
    if r["rating"] is not None:
        import math
        assert math.isfinite(r["rating"])


def test_compute_rating_raw_deterministic():
    rounds = [{"round": 1, "start_tick": 3896, "end_tick": 9077},
              {"round": 2, "start_tick": 9397, "end_tick": 14576}]
    deaths = [
        {"round": 1, "tick": 6000, "attacker_steamid": "A1", "user_steamid": "B1",
         "assister_steamid": "", "weapon": "ak47", "headshot": False,
         "attacker_X": 1.0, "attacker_Y": 1.0, "user_X": 2.0, "user_Y": 2.0},
        {"round": 2, "tick": 16000, "attacker_steamid": "B1", "user_steamid": "A1",
         "assister_steamid": "", "weapon": "glock", "headshot": True,
         "attacker_X": 3.0, "attacker_Y": 3.0, "user_X": 4.0, "user_Y": 4.0},
    ]
    stats = {"A1": {"kills": 1, "deaths": 1, "assists": 0, "damage": 60, "adr": 30.0,
                    "first_kills": 1, "hs_kills": 0},
             "B1": {"kills": 1, "deaths": 1, "assists": 0, "damage": 60, "adr": 30.0,
                    "first_kills": 0, "hs_kills": 1}}
    identity_of = {"A1": "A", "B1": "B"}
    a = compute_rating_raw(rounds, deaths, stats, ["A1", "B1"], identity_of, 64.0)
    b = compute_rating_raw(rounds, deaths, stats, ["A1", "B1"], identity_of, 64.0)
    assert a == b  # 确定性
    assert a["A1"]["components"]["frag"]["value"] is not None
    # A1 死于 R2，无队友补枪 → kast 只有 R1（击杀）→ 1/2
    assert a["A1"]["components"]["kast"]["value"] == 0.5
    assert a["A1"]["components"]["survival"]["value"] == 0.5
    # 无队员阵亡给 A1 补枪机会 → trade no_opportunity
    assert a["A1"]["components"]["trade"]["status"] == "no_opportunity"


def test_compute_rating_raw_kast_traded():
    """KAST 含可证明 traded：玩家死亡后其击杀者被队友补掉 → 该回合计入 KAST。"""
    rounds = [{"round": 1, "start_tick": 100, "end_tick": 8000}]
    deaths = [
        # A1 被 B1 击杀（R1）
        {"round": 1, "tick": 6000, "attacker_steamid": "B1", "user_steamid": "A1",
         "assister_steamid": "", "weapon": "ak47", "headshot": False,
         "attacker_X": 1.0, "attacker_Y": 1.0, "user_X": 2.0, "user_Y": 2.0},
        # A2（A1 队友）在窗口内补掉 B1
        {"round": 1, "tick": 6200, "attacker_steamid": "A2", "user_steamid": "B1",
         "assister_steamid": "", "weapon": "glock", "headshot": False,
         "attacker_X": 3.0, "attacker_Y": 3.0, "user_X": 4.0, "user_Y": 4.0},
    ]
    stats = {"A1": {"kills": 0, "deaths": 1, "assists": 0, "damage": 0, "adr": 0.0,
                    "first_kills": 0, "hs_kills": 0},
             "A2": {"kills": 1, "deaths": 0, "assists": 0, "damage": 60, "adr": 60.0,
                    "first_kills": 0, "hs_kills": 0}}
    identity_of = {"A1": "A", "A2": "A", "B1": "B"}
    raw = compute_rating_raw(rounds, deaths, stats, ["A1", "A2"], identity_of, 64.0)
    # A1 死亡但被补（traded）→ KAST T 命中
    assert raw["A1"]["components"]["kast"]["value"] == 1.0
    # A2 补枪击杀 → KAST K 命中
    assert raw["A2"]["components"]["kast"]["value"] == 1.0
