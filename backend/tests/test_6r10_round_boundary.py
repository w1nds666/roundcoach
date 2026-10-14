"""6R10A 回合边界权威修复回归。

背景（原始证据见 docs/phase6r10-round-boundary-audit.md）：
- demo 19/20/21 的首个正式回合 round_start 均从 tick=3896 开始，
  而每场在 3896 之前都存在 12–13 个"备战期"击杀（含 knife 刀杀）。
- 旧 parser.round_mapper 对"首回合 start 之前 / 末回合 end 之后"的 tick 用 `else 1` 兜底，
  把这些备战击杀错标为 round=1，从而污染回合 1 的配合分析/回放，
  再由 `_sec` 的 `max(0,...)` 钳成 00:00 正赛事件（伪装），掩盖真实边界。

本文件锁定权威语义：
- round_mapper 返回 None（表示"不在任何正式回合"），绝不回退成 1。
- compute_basic_stats 排除 round 不在 [1, 回合数] 的事件（备战/场外击杀不计 K/D/ADR）。
- order 首回合窗口内事件 tick 必须 >= freeze_end（无负秒病态样本）。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.parser import DemoAnalyzer  # noqa: E402
from app.services.stats import compute_basic_stats  # noqa: E402


ROUNDS_SYNTH = [
    {"round": 1, "start_tick": 3896, "end_tick": 9077, "freeze_end_tick": 5592},
    {"round": 2, "start_tick": 9397, "end_tick": 14576, "freeze_end_tick": 10677},
]


def test_round_mapper_out_of_window_returns_none():
    """备战击杀（tick < 首回合 start）与末回合之后的事件：不得回退成 1。"""
    mapper = DemoAnalyzer.round_mapper(ROUNDS_SYNTH)
    # 备战期（首回合 start=3896 之前）→ None，绝不能是 1
    assert mapper(904) is None
    assert mapper(2817) is None
    # 窗口内 → 对应 round
    assert mapper(3896) == 1
    assert mapper(6321) == 1
    assert mapper(9077) == 1
    assert mapper(9397) == 2
    assert mapper(14576) == 2
    # 末回合之后 → None
    assert mapper(99999) is None
    # 回合间空隙（上一 end 之后、下一 start 之前）→ None
    assert mapper(9300) is None


def test_round_mapper_aligns_with_routes_mapper():
    """routes._round_mapper 已返回 None；parser.round_mapper 必须与之对齐。"""
    from app.services.routes import _round_mapper as routes_mapper

    p = DemoAnalyzer.round_mapper(ROUNDS_SYNTH)
    r = routes_mapper(ROUNDS_SYNTH)
    for tick in (904, 2817, 3896, 6321, 9077, 9397, 99999):
        assert p(tick) == r(tick)


def test_basic_stats_excludes_warmup_round():
    """备战期（round=None/0）击杀不计入 K/D/首杀；正式回合击杀计入。"""
    deaths = [
        # 备战击杀：round=None → 不计
        {"attacker_steamid": "a", "user_steamid": "v", "tick": 904, "round": None,
         "weapon": "ak47", "headshot": False},
        # 正式回合 R1 击杀：计入
        {"attacker_steamid": "a", "user_steamid": "v2", "tick": 6321, "round": 1,
         "weapon": "glock", "headshot": True},
    ]
    stats = compute_basic_stats(deaths, [], ["a", "v", "v2"], ROUNDS_SYNTH)
    assert stats["a"]["kills"] == 1, "备战击杀不得计为正式击杀"
    assert stats["v"]["deaths"] == 0, "备战期死亡不得计为正式死亡"
    assert stats["v2"]["deaths"] == 1
    assert stats["a"]["hs_kills"] == 1
    assert stats["a"]["first_kills"] == 1, "首杀只应从正式回合击杀中判定"


def test_basic_stats_excludes_warmup_damage():
    """备战期伤害不计入 ADR 分子。"""
    hurts = [
        {"attacker_steamid": "a", "tick": 904, "round": None, "weapon": "ak47", "dmg_health": 40},
        {"attacker_steamid": "a", "tick": 6321, "round": 1, "weapon": "glock", "dmg_health": 60},
    ]
    stats = compute_basic_stats([], hurts, ["a"], ROUNDS_SYNTH)
    assert stats["a"]["damage"] == 60, "备战期伤害不得计入 ADR 分子"


_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "demo19_round_boundary.json"


@pytest.mark.skipif(not _FIXTURE.exists(), reason="demo19_round_boundary.json fixture 缺失")
def test_demo19_r1_no_early_kill_regression():
    """真实 fixture 回归：demo19 R1 窗口内事件 tick 必须落在 [freeze_end, end]，
    即不再出现"早期击杀落在战术窗口前"的病态样本。"""
    fix = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    r1 = fix["round1"]
    assert r1["start_tick"] <= r1["freeze_end_tick"] <= r1["end_tick"]
    mapper = DemoAnalyzer.round_mapper(fix["rounds"])
    for k in fix["early_kills"]:
        assert mapper(k["tick"]) is None, f"备战击杀 tick={k['tick']} 必须不在任何正式回合"
    for k in fix["round1_kills"]:
        assert mapper(k["tick"]) == 1
        # 窗口内事件不得早于 freeze_end（否则产生负秒/伪装 00:00）
        assert k["tick"] >= r1["freeze_end_tick"], \
            f"R1 事件 tick={k['tick']} < freeze_end={r1['freeze_end_tick']}（病态负秒样本）"
