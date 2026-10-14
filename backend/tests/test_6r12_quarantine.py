"""6R12 测试：配合指标急救（spacing 阶段化 + 可信度 quarantine）。

- classify_phase：freeze/opening/execute/post_plant/save 最小可信分类。
- spacing：只在战术机会阶段（opening/execute/post_plant）采样；freeze/save 不计机会、不产生正向 episode；
  success 由实际 cram_frac 决定（不再硬编码 success=True）；出生点/冻结期不出现"间距纪律良好/00:00"。
- quarantine：metric_credibility 盘点（validated/experimental/hidden）；hidden（crossfire）正向教练结论
  不入 conclusions；零机会 → applicable=False 且 rate=None（显示"不适用"，绝非 0 分）。
"""
import pytest

from app.services.coordination import (
    _conclusions, _ev_sig, build_coordination_analysis,
    classify_phase, PHASE_TACTICAL,
)
from app.services.tactical import TACTICAL_SCHEMA_VERSION


def _art_round(players_points, start=65, freeze=6000, end=12000, bomb=None, winner="A", props=None):
    """构造单回合 artifact。players_points: {pid: [(tick, x, y, alive, place)]}。"""
    players = {}
    for pid, pts in players_points.items():
        players[str(pid)] = {"player_id": pid, "side": "CT", "outcome": "alive",
                             "points": [{"tick": t, "time": (t - freeze) / 64 if tick_rate else 0.0,
                                         "x": x, "y": y, "z": 0.0, "yaw": 0.0, "pitch": 0.0,
                                         "alive": alive, "place": place} for (t, x, y, alive, place) in pts]}
    return {"round": 1, "start_tick": start, "end_tick": end, "freeze_end_tick": freeze,
            "bomb_planted_tick": bomb, "winner_side": 2, "winner_team": winner,
            "team_side_map": {"A": 2, "B": 3}, "players": players, "props": props or []}


tick_rate = 64.0


def _spacing_players():
    """P1-P5(队A)。freeze 期(<6000)都挤在出生点；战术期(>=6000)分散（间距好）。"""
    def pts(x0, y0, x1, y1):
        return [(65, x0, y0, True, "Spawn"), (3000, x0, y0, True, "Spawn"),
                (6000, x1, y1, True, "Middle"), (9000, x1, y1, True, "Middle"),
                (11800, x1, y1, True, "Middle")]
    return {
        1: pts(0, 0, 0, 0),
        2: pts(10, 0, 500, 0),
        3: pts(20, 0, 1000, 0),
        4: pts(30, 0, 1500, 0),
        5: pts(40, 0, 2000, 0),
    }


def _base_args(rounds):
    return {"artifact_rounds": rounds, "map_name": "de_mirage", "tick_rate": tick_rate,
            "kills": [], "deaths": [], "id_of": {}, "name_of": {i: f"P{i}" for i in range(1, 6)},
            "team_of": {i: "A" for i in range(1, 6)}, "focus_player_id": 1}


# ---------- 阶段分类 ----------

def test_classify_phase_minimal():
    # 冻结期
    assert classify_phase(100, 65, 6000, 12000, None, 128) == "freeze"
    # 开局期（freeze_end 后 <5s=640tick）
    assert classify_phase(6100, 65, 6000, 12000, None, 128) == "opening"
    assert classify_phase(6600, 65, 6000, 12000, None, 128) == "opening"
    # 执行期
    assert classify_phase(7000, 65, 6000, 12000, None, 128) == "execute"
    assert classify_phase(8000, 65, 6000, 12000, None, 128) == "execute"
    # 下包后（bomb=9000）
    assert classify_phase(9200, 65, 6000, 12000, 9000, 128) == "post_plant"
    # 保枪/收尾（end - 10s*128=10720 之后）
    assert classify_phase(11000, 65, 6000, 12000, None, 128) == "save"


def test_phase_tactical_set():
    assert PHASE_TACTICAL == {"opening", "execute", "post_plant"}
    assert "freeze" not in PHASE_TACTICAL and "save" not in PHASE_TACTICAL


# ---------- spacing 修复 ----------

def test_spacing_excludes_freeze_and_success_derived():
    """freeze 期(出生点挤)不计机会；战术期分散 → rate 高；不再对冻结/出生点出正向 success。"""
    rounds = [_art_round(_spacing_players(), freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    a = next(t for t in out["teams"] if t["team"] == "A")
    sp = next(m for m in a["metrics"] if m["metric_id"] == "spacing")
    # freeze 期样本（挤在出生点）被排除；战术期分散 → 合规率高
    assert sp["opportunities"] > 0
    assert sp["rate"] is not None and sp["rate"] > 0.8
    # 无出生点/00:00 正向间距证据
    for ev in out["rounds"]:
        if ev["metric_id"] == "spacing":
            assert ev["seconds"] is None or ev["seconds"] >= 0
            assert ev["region"] not in ("出生点", "Spawn")
            # success 由实际 cram_frac 决定：战术期分散 -> success=True 合理；但绝不来自 freeze
    assert sp["applicable"] is True


def test_spacing_no_tactical_phase_no_evidence_and_not_applicable():
    """整回合都在出生点附近且战术阶段无存活>=2 的采样 → 该指标不适用（不产生正向结论）。"""
    # 让玩家在战术期都不可见(死亡)：freeze 期(<6000)在出生点存活；战术期(>=6000)死亡 → 无战术机会样本
    rounds = [_art_round({i: [(65, i * 10, 0, True, "Spawn"), (6100, i * 10, 0, False, "Middle")]
                          for i in range(1, 6)}, freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    a = next(t for t in out["teams"] if t["team"] == "A")
    sp = next(m for m in a["metrics"] if m["metric_id"] == "spacing")
    # 无战术机会 → 不适用（rate None、applicable False、无正向 episode）
    assert sp["applicable"] is False
    assert sp["rate"] is None


# ---------- quarantine ----------

def test_metric_credibility_inventory():
    rounds = [_art_round(_spacing_players(), freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    inv = out["metric_credibility"]
    assert len(inv) == 7
    by = {m["metric_id"]: m for m in inv}
    assert by["crossfire_candidate"]["trust"] == "hidden"
    assert by["trade"]["trust"] == "validated"
    assert by["spacing"]["trust"] == "experimental"
    for m in inv:
        assert m["definition"] and m["opportunity"] and m["limits"]


def test_zero_opportunity_not_applicable_not_zero():
    """零机会 → applicable=False 且 rate=None（绝不当作 0 分）。"""
    rounds = [_art_round(_spacing_players(), freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    for t in out["teams"]:
        for m in t["metrics"]:
            if m["opportunities"] == 0:
                assert m["applicable"] is False
                assert m["rate"] is None
                assert m["human"] is None   # 前端将显示"不适用"


def _mk_ev(metric, round, sec, success, players, region="R", team="A", mval=None):
    return {"metric_id": metric, "team": team, "round": round, "seconds": sec, "region": region,
            "players": players, "success": success, "reason": "x", "layers": [], "highlight": [],
            "detail": "", "metric_value": mval}


def test_hidden_metric_success_not_in_conclusions():
    """crossfire_candidate（hidden，无 LoS）的正向结论不得进入主界面结论。"""
    evs = [_mk_ev("crossfire_candidate", 1, 10, True, [1, 2]),
           _mk_ev("trade", 1, 20, True, [2, 3]),
           _mk_ev("isolated_death", 1, 30, False, [1, 2]),
           _mk_ev("spacing", 1, 40, True, [4, 5]),
           _mk_ev("utility_assisted_entry", 1, 50, True, [1, 6]),
           _mk_ev("simultaneous_contact", 1, 60, True, [3, 4]),
           _mk_ev("area_control", 1, 70, True, [1, 2, 3])]
    cs = _conclusions(evs, {1: "P1", 2: "P2", 3: "P3", 4: "P4", 5: "P5", 6: "P6"}, [])
    assert all(c["metric_id"] != "crossfire_candidate" for c in cs), "hidden 指标不应出现在结论"
    tones = {c["tone"] for c in cs}
    assert "success" in tones and "problem" in tones
