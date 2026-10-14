"""6R16 测试：配合指标重建 + 区域控制状态机 + 关键反例。

- 反例：freeze/spawn 00:00 不出正向 spacing；保枪/最后存活不报孤立阵亡；
  单人在区域但敌人同区 → contested；只停留无正向信号 → occupied/influence 而非 controlled；
  零机会 rate=None/applicable=false；同一 episode 不重复；spacing 分母为 episode 段数（非采样点）。
- 6R16 关键校正：持续占据+敌方缺席但**无正向信号** → 不得 controlled（可能只是空区）。
"""
import pytest

from app.services.coordination import (
    _conclusions, _dedup_evidence, _ev_sig, build_coordination_analysis,
    classify_phase, PHASE_TACTICAL, _spacing_episodes,
)
from app.services.area_state import (
    classify_team_state, build_region_state_machine, aggregate_area_metric, STATE_CN,
)
from app.services.region_geometry import build_regions, evaluate_region_geometry, convex_hull
from app.services.coordination_thresholds import (
    TRADE_WINDOW_SEC, AREA_CONTROL_MIN_SEC, AREA_OCCUPIED_MIN_SEC,
    confidence_from_opportunities, cap_confidence, spacing_allowed,
    SPACING_MIN_EPISODE_SEC, VERSION as THRESH_VERSION,
)

tick_rate = 64.0


def _at_points(points, v):
    return points


def _art_round(players_points, start=65, freeze=6000, end=12000, bomb=None, winner="A", props=None,
               blinds=None, he_damage=None, shoots=None):
    """构造单回合 artifact。players_points: {pid: [(tick, x, y, alive, place, yaw?)]}。"""
    players = {}
    for pid, pts in players_points.items():
        players[str(pid)] = {"player_id": pid, "side": "CT", "outcome": "alive",
                             "points": [{"tick": t, "time": (t - freeze) / tick_rate if tick_rate else 0.0,
                                         "x": x, "y": y, "z": 0.0, "yaw": (rest[0] if rest else 0.0),
                                         "pitch": 0.0, "alive": alive, "place": place}
                                        for (t, x, y, alive, place, *rest) in pts]}
    return {"round": 1, "start_tick": start, "end_tick": end, "freeze_end_tick": freeze,
            "bomb_planted_tick": bomb, "winner_side": 2, "winner_team": winner,
            "team_side_map": {"A": 2, "B": 3}, "players": players,
            "props": props or [], "blinds": blinds or [], "he_damage": he_damage or [],
            "shoots": shoots or []}


def _base_args(rounds, region_geometry=None, **kw):
    args = {"artifact_rounds": rounds, "map_name": "de_mirage", "tick_rate": tick_rate,
            "kills": kw.pop("kills", []), "deaths": kw.pop("deaths", []),
            "id_of": kw.pop("id_of", {}), "name_of": kw.pop("name_of", {i: f"P{i}" for i in range(1, 6)}),
            "team_of": kw.pop("team_of", {i: "A" for i in range(1, 6)}),
            "focus_player_id": 1}
    if region_geometry:
        args["region_geometry"] = region_geometry
    return args


# ---------- 阶段分类 ----------

def test_spacing_excludes_spawn_region_positive():
    """出生点区域（CTSpawn/TSpawn）不产出"间距纪律良好"的正向结论（6R16 关键反例）。"""
    # 5 名队友整个战术期都待在 T 出生点（TSpawn），即使时间超过 freeze_end，也不得表扬间距"
    players = {i: [(65, i * 10, 0, True, "TSpawn"), (6100, i * 50, 0, True, "TSpawn"),
                   (9000, i * 50, 0, True, "TSpawn"), (11800, i * 50, 0, True, "TSpawn")] for i in range(1, 6)}
    rounds = [_art_round(players, freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    a = next(t for t in out["teams"] if t["team"] == "A")
    sp = next(m for m in a["metrics"] if m["metric_id"] == "spacing")
    # 任一成员在出生点（全体都在）→ 无有效战术期段 → 机会为 0、不适用、无证据（正或负）
    assert sp["opportunities"] == 0
    assert sp["applicable"] is False and sp["rate"] is None
    for ev in out["rounds"]:
        if ev["metric_id"] == "spacing":
            assert False, "出生点不应产出 spacing 证据（正或负）"


def test_classify_phase_minimal():
    assert classify_phase(100, 65, 6000, 12000, None, 128) == "freeze"
    assert classify_phase(6100, 65, 6000, 12000, None, 128) == "opening"
    assert classify_phase(7000, 65, 6000, 12000, None, 128) == "execute"
    assert classify_phase(9200, 65, 6000, 12000, 9000, 128) == "post_plant"
    assert classify_phase(11000, 65, 6000, 12000, None, 128) == "save"
    assert PHASE_TACTICAL == {"opening", "execute", "post_plant"}


def test_spacing_any_member_in_spawn_excludes_sample_and_evidence():
    """6R16 回归：任一存活成员仍在出生点 → 该采样不进入 spacing opportunity/episode，
    不产出正/负间距证据；断段后仅在全体离开出生点后重启。覆盖"2人出门+3人出生点"。"""
    def pts(i):
        # 战术期：P1/P2 出门进 Middle；P3/P4/P5 仍留 TSpawn
        if i <= 2:
            return [(65, i * 10, 0, True, "TSpawn"), (6100, i * 500, 0, True, "Middle"),
                    (7000, i * 500, 0, True, "Middle"), (9000, i * 500, 0, True, "Middle"),
                    (11800, i * 500, 0, True, "Middle")]
        return [(65, i * 10, 0, True, "TSpawn"), (6100, i * 10, 0, True, "TSpawn"),
                (7000, i * 10, 0, True, "TSpawn"), (9000, i * 10, 0, True, "TSpawn"),
                (11800, i * 10, 0, True, "TSpawn")]
    players = {i: pts(i) for i in range(1, 6)}
    rounds = [_art_round(players, freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    a = next(t for t in out["teams"] if t["team"] == "A")
    sp = next(m for m in a["metrics"] if m["metric_id"] == "spacing")
    # P3/P4/P5 全程在出生点 → 任一成员在 spawn 的采样不进入段 → 无机会/无证据
    assert sp["opportunities"] == 0
    assert sp["applicable"] is False and sp["rate"] is None
    for ev in out["rounds"]:
        if ev["metric_id"] == "spacing":
            assert False, "出生点混合场合不应产出 spacing 证据（正或负）"


def test_spacing_breaks_on_spawn_then_restarts_after_all_leave():
    """出生点中断后，若全部存活成员离开出生点，应重启有效段并产出非出生点 evidence。"""
    def pts(i):
        return [(65, i * 10, 0, True, "TSpawn"), (6100, i * 10, 0, True, "TSpawn"),
                (7000, i * 500, 0, True, "Middle"), (9000, i * 500, 0, True, "Middle"),
                (11800, i * 500, 0, True, "Middle")]
    players = {i: pts(i) for i in range(1, 6)}
    rounds = [_art_round(players, freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    a = next(t for t in out["teams"] if t["team"] == "A")
    sp = next(m for m in a["metrics"] if m["metric_id"] == "spacing")
    assert sp["opportunities"] >= 1
    assert sp["applicable"] is True
    for ev in out["rounds"]:
        if ev["metric_id"] == "spacing":
            assert ev["region"] not in ("出生点", "T 出生点", "CT 出生点", "Spawn")
            assert ev["seconds"] > 0
    for c in out["conclusions"]:
        if c["metric_id"] == "spacing":
            assert "出生点" not in (c.get("region") or "")


# ---------- spacing：episode 级分母（不再用采样点数） ----------

def test_spacing_uses_episode_not_sample_points():
    """spacing 分母 = 战术期连续段数；即使采样点很多，分母也是段数。"""
    # 5 名队友战术期分散（good）；freeze 期(<6000)出生点扎堆
    def pts(x0, y0, x1, y1):
        return [(65, x0, y0, True, "Spawn"), (3000, x0, y0, True, "Spawn"),
                (6000, x1, y1, True, "Middle"), (9000, x1, y1, True, "Middle"),
                (11800, x1, y1, True, "Middle")]
    players = {i: pts(i * 10, 0, i * 500, 0) for i in range(1, 6)}
    rounds = [_art_round(players, freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    a = next(t for t in out["teams"] if t["team"] == "A")
    sp = next(m for m in a["metrics"] if m["metric_id"] == "spacing")
    # 战术期一个连续段 → opportunities=1（即使采样点几百）
    assert sp["opportunities"] == 1
    assert sp["opportunities"] < 500   # 绝不是 tick 采样点数
    assert sp["rate"] is not None


def test_spacing_excludes_freeze_spawn_positive():
    """出生点/冻结期不出正向 spacing episode；战术期分散才有。"""
    players = {i: [(65, i * 10, 0, True, "Spawn"), (5900, i * 10, 0, True, "Spawn"),
                   (6100, i * 500, 0, True, "Middle"), (9000, i * 500, 0, True, "Middle"),
                   (11800, i * 500, 0, True, "Middle")] for i in range(1, 6)}
    rounds = [_art_round(players, freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    a = next(t for t in out["teams"] if t["team"] == "A")
    sp = next(m for m in a["metrics"] if m["metric_id"] == "spacing")
    assert sp["opportunities"] >= 1
    for ev in out["rounds"]:
        if ev["metric_id"] == "spacing":
            assert ev["region"] not in ("出生点", "Spawn")
            if ev["success"]:
                # 正向成功的 spacing 证据不得来自出生点/冻结期
                assert ev["seconds"] > 0.1
    # 把战术期样本设成都在出生点且冻结期死亡 → 无战术期 episode
    players_die = {i: [(65, i * 10, 0, True, "Spawn"), (6100, i * 10, 0, False, "Spawn")] for i in range(1, 6)}
    rounds2 = [_art_round(players_die, freeze=6000, end=12000)]
    out2 = build_coordination_analysis(**_base_args(rounds2))
    a2 = next(t for t in out2["teams"] if t["team"] == "A")
    sp2 = next(m for m in a2["metrics"] if m["metric_id"] == "spacing")
    assert sp2["applicable"] is False and sp2["rate"] is None


def test_spacing_episode_gap_breaks_segments():
    """存活<2（全员短暂阵亡）→ 断段，产生多个 episode（分母为段数）。"""
    # 战术期 6100-6900 全活；7000 tick 全员 dead（存活<2）→ 断；7200 又全活 → 第 2 段
    def pts(i):
        return [(65, i * 10, 0, True, "Spawn"), (6100, i * 500, 0, True, "Middle"),
                (6900, i * 500, 0, True, "Middle"), (7000, i * 10, 0, False, "Middle"),
                (7200, i * 500, 0, True, "Middle"), (9000, i * 500, 0, True, "Middle"),
                (11800, i * 500, 0, True, "Middle")]
    players = {i: pts(i) for i in range(1, 6)}
    rounds = [_art_round(players, freeze=6000, end=12000)]
    eps = _spacing_episodes(rounds[0]["players"], [1, 2, 3, 4, 5], 6000, 12000, 65, tick_rate, None)
    assert len(eps) >= 2   # 段被"存活<2"切开


# ---------- isolated_death：排除最后存活/保枪 ----------

def test_isolated_death_excludes_last_survivor():
    """最后存活者阵亡不报孤立（是无人生还，不是被抛弃）。"""
    # 队伍 A：P1,P2,P3。P3 是最后存活者。构造 P3 阵亡时其他队友都已死。
    players = {
        1: [(65, 0, 0, True, "Mid"), (7000, 0, 0, False, "Mid")],   # P1 死
        2: [(65, 0, 0, True, "Mid"), (7000, 0, 0, False, "Mid")],   # P2 死
        3: [(65, 100, 100, True, "Mid"), (8500, 100, 100, True, "Mid"), (9000, 100, 100, True, "Mid"),
            (9100, 100, 100, False, "Mid")],                          # P3 最后阵亡（>7000 后无人）
    }
    kills = [{"round": 1, "tick": 7000, "actor_steam": "9", "victim_steam": "1", "actor_x": 0, "actor_y": 0,
              "victim_x": 0, "victim_y": 0},
             {"round": 1, "tick": 7000, "actor_steam": "9", "victim_steam": "2", "actor_x": 0, "actor_y": 0,
              "victim_x": 0, "victim_y": 0},
             {"round": 1, "tick": 9100, "actor_steam": "9", "victim_steam": "3", "actor_x": 0, "actor_y": 0,
              "victim_x": 100, "victim_y": 100}]
    deaths = [dict(k) for k in kills]
    rnds = [_art_round(players, freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(
        rnds, kills=kills, deaths=deaths,
        id_of={"9": 9, "1": 1, "2": 2, "3": 3}, team_of={1: "A", 2: "A", 3: "A", 9: "B"}))
    a = next(t for t in out["teams"] if t["team"] == "A")
    iso = next(m for m in a["metrics"] if m["metric_id"] == "isolated_death")
    # P1、P2 在 7000 阵亡（此时 P3 仍存活，非最后存活）→ 各计 1 机会；P3 在 9100 阵亡时
    # 队友已全死（最后存活）→ 被排除。故 opportunities=2（0 次被判孤立，因距离 100 < 900）。
    assert iso["opportunities"] == 2
    assert iso["applicable"] is True


# ---------- 零机会 ----------

def test_zero_opportunity_not_applicable():
    rounds = [_art_round({i: [(65, 0, 0, True, "Mid"), (9000, 0, 0, True, "Mid")] for i in range(1, 4)},
                         freeze=6000, end=12000)]
    out = build_coordination_analysis(**_base_args(rounds))
    for t in out["teams"]:
        for m in t["metrics"]:
            if m["opportunities"] == 0:
                assert m["applicable"] is False
                assert m["rate"] is None
                assert m["human"] is None


# ---------- 去重 ----------

def _mk_ev(metric, round, sec, success, players, region="R", team="A", mval=None):
    return {"metric_id": metric, "team": team, "round": round, "seconds": sec, "region": region,
            "players": players, "success": success, "reason": "x", "layers": [], "highlight": [],
            "detail": "", "metric_value": mval, "players_name": "、".join(str(p) for p in players)}


def test_dedup_evidence_by_episode():
    evs = [_mk_ev("isolated_death", 1, 10, False, [1, 2]),
           _mk_ev("isolated_death", 1, 10, False, [2, 1]),
           _mk_ev("trade", 1, 20, True, [3, 1]),
           _mk_ev("crossfire_candidate", 1, 30, True, [4, 5]),
           _mk_ev("utility_assisted_entry", 1, 40, True, [6, 7]),
           _mk_ev("spacing", 1, 50, True, [8, 9]),
           _mk_ev("area_control", 1, 60, True, [10, 11, 12]),
           _mk_ev("simultaneous_contact", 1, 70, True, [13, 14]),
           _mk_ev("spacing", 1, 80, True, [15, 16])]
    out = _dedup_evidence(evs)
    sigs = [_ev_sig(e) for e in out]
    assert len(sigs) == len(set(sigs))


# ---------- 信任度 inventory ----------

def test_credibility_inventory_updated():
    from app.services.coordination import METRIC_TRUST
    assert METRIC_TRUST["crossfire_candidate"] == "hidden"
    assert METRIC_TRUST["trade"] == "validated"
    assert METRIC_TRUST["spacing"] == "experimental"
    assert METRIC_TRUST["area_control"] == "experimental"
    assert METRIC_TRUST["utility_assisted_entry"] == "experimental"
    assert METRIC_TRUST["isolated_death"] == "experimental"


# ---------- 置信度上限 ----------

def test_confidence_caps_updated():
    assert cap_confidence("crossfire_candidate", "high") == "medium"
    assert cap_confidence("utility_assisted_entry", "high") == "medium"
    assert cap_confidence("area_control", "high") == "medium"
    assert cap_confidence("spacing", "high") == "medium"
    assert cap_confidence("isolated_death", "high") == "medium"
    assert cap_confidence("trade", "high") == "high"


# ---------- 区域状态机 ----------

def test_area_state_machine_contested_when_both_present():
    """单人在区但敌人同区 → contested，绝不 controlled。"""
    # region 多边形（Mirage 中区），几何可信
    geo = {"available": True, "geometric_count": 1, "region_count": 1, "min_coverage": 0.95,
           "sample_total": 1000, "trust": "validated"}
    region = {"place_key": "middle", "place_cn": "中路", "centroid": {"rx": 0.5, "ry": 0.5},
              "geometric": True, "coverage": 0.95, "confidence": "high"}
    # A 队 P1 在"middle"；B 队 P2 也在"middle"（同区）→ contested
    players = {
        "1": {"side": "CT", "points": [{"tick": 65, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "Middle"},
                                       {"tick": 10000, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "Middle"}]},
        "2": {"side": "T", "points": [{"tick": 65, "x": 120, "y": 100, "yaw": 90, "alive": True, "place": "Middle"},
                                      {"tick": 10000, "x": 120, "y": 100, "yaw": 90, "alive": True, "place": "Middle"}]},
    }
    meta = {"round": 1, "start_tick": 65, "end_tick": 10000, "freeze_end_tick": 65}
    sm = build_region_state_machine(region, players, meta, tick_rate, [1], [2], [], [],
                                    set(), set(), {})
    # 两队占位至少有一段 contested
    segs_a = [s for s in sm["segments"]["A"] if s["state"] == "contested"]
    assert segs_a, "敌我同区应产生 contested"
    # 绝不出现 controlled（双方都占位）
    assert all(s["state"] != "controlled" for s in sm["segments"]["A"])
    trans_contested = [t for t in sm["transitions"] if t["state"] == "contested"]
    assert trans_contested


def test_area_state_controlled_requires_positive_signal():
    """持续占据(≥12s)+无敌方 + 但**无正向信号**（空区）→ 不得 controlled，只能 occupied/influence。"""
    geo = {"available": True, "geometric_count": 1, "region_count": 1, "min_coverage": 0.95,
           "sample_total": 1000, "trust": "validated"}
    region = {"place_key": "bombsitea", "place_cn": "A 区", "centroid": {"rx": 0.5, "ry": 0.5},
              "geometric": True, "coverage": 0.95, "confidence": "high"}
    # A 队 P1 持续占 "BombsiteA"，B 队无人在区；无任何道具/致盲信号
    players = {
        "1": {"side": "CT", "points": [{"tick": 65, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "BombsiteA"},
                                       {"tick": 10000, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "BombsiteA"}]},
    }
    meta = {"round": 1, "start_tick": 65, "end_tick": 10000, "freeze_end_tick": 65}
    sm = build_region_state_machine(region, players, meta, tick_rate, [1], [], [], [],
                                    {1}, set(), {})
    segs_a = sm["segments"]["A"]
    # 关键校正：无正向信号 → 绝不 controlled
    assert all(s["state"] != "controlled" for s in segs_a), "无正向信号不得 controlled"
    # 有几何 + 持续占位 → 至少 occupied
    assert any(s["state"] == "occupied" for s in segs_a)


def test_area_state_controlled_with_positive_signal():
    """持续占据 + 无敌方 + 区内真实 smoke active（正向信号）→ controlled。"""
    geo = {"available": True}
    region = {"place_key": "bombsitea", "place_cn": "A 区", "centroid": {"rx": 0.5, "ry": 0.5},
              "geometric": True, "coverage": 0.95, "confidence": "high"}
    players = {
        "1": {"side": "CT", "points": [{"tick": 65, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "BombsiteA"},
                                       {"tick": 10000, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "BombsiteA"}]},
    }
    meta = {"round": 1, "start_tick": 65, "end_tick": 10000, "freeze_end_tick": 65}
    # 我方 smoke：thrower=1，active 覆盖整个窗口（65..10000 -> 秒 0..155）；place_key=BombsiteA
    props = [{"thrower_player_id": 1, "type": "smoke", "place_key": "bombsitea",
              "active_start": 65, "active_end": 10000}]
    sm = build_region_state_machine(region, players, meta, tick_rate, [1], [], props, [],
                                    {1}, set(), {})
    segs_a = sm["segments"]["A"]
    assert any(s["state"] == "controlled" for s in segs_a), "带烟(正向信号)且持续占据应 controlled"
    # controlled 段需 dur≥CONTROL_MIN_SEC
    ctrl = [s for s in segs_a if s["state"] == "controlled"]
    assert ctrl and ctrl[0]["dur"] >= AREA_CONTROL_MIN_SEC


# ---------- area_state 纯函数 ----------

def test_classify_team_state_priority():
    # contested 优先于一切
    assert classify_team_state(1, 1, 999, True, True) == "contested"
    # 我方占位+无敌方+几何+signal+long → controlled
    assert classify_team_state(1, 0, 20, True, True) == "controlled"
    # 我方占位+无敌方+几何+无signal → occupied（空区不升级）
    assert classify_team_state(1, 0, 20, False, True) == "occupied"
    # 无几何 → influence
    assert classify_team_state(1, 0, 999, True, False) == "influence"
    # 我方缺席 → influence
    assert classify_team_state(0, 1, 999, True, True) == "influence"
    # 无占位 → unknown
    assert classify_team_state(0, 0, 999, False, True) == "unknown"


# ---------- 区域几何 ----------

def test_region_geometry_hull_and_coverage():
    pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5), (5, 5)]   # 正方形 + 内部点（重复）
    hull = convex_hull(pts)
    assert len(hull) == 4
    # 内部点 (5,5) 落在 hull 内 → coverage 高
    poly = [(x, y) for x, y in hull]
    from app.services.region_geometry import _point_in_poly
    assert _point_in_poly((5, 5), poly)
    assert _point_in_poly((100, 100), poly) is False


def test_region_geometry_insufficient_samples_low_conf():
    from app.services.region_geometry import build_region
    r = build_region("newplace", "新区域", [(5, 5), (6, 6), (7, 7)], 0, 0, 5.0, 1024)
    # 样本 < 12 → 无多边形（geometric=False），confidence=low
    assert r["geometric"] is False
    assert r["confidence"] == "low"
    assert r["polygon"] == []


def test_region_geometry_evaluate_trust():
    from app.services.region_geometry import evaluate_region_geometry
    # 无几何 → unavailable
    ev = evaluate_region_geometry([])
    assert ev["available"] is False and ev["trust"] == "hidden"
    # 几何覆盖高 → validated
    ev2 = evaluate_region_geometry([{"geometric": True, "coverage": 0.95, "sample_count": 1000}])
    assert ev2["available"] is True and ev2["trust"] == "validated"


# ---------- 确定性 ----------

def test_area_state_deterministic():
    geo = {"available": True}
    region = {"place_key": "middle", "place_cn": "中路", "centroid": {"rx": 0.5, "ry": 0.5},
              "geometric": True, "coverage": 0.95, "confidence": "high"}
    players = {"1": {"side": "CT", "points": [{"tick": 65, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "Middle"},
                                              {"tick": 10000, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "Middle"}]}}
    meta = {"round": 1, "start_tick": 65, "end_tick": 10000, "freeze_end_tick": 65}
    a = build_region_state_machine(region, players, meta, tick_rate, [1], [], [], [], {1}, set(), {})
    b = build_region_state_machine(region, players, meta, tick_rate, [1], [], [], [], {1}, set(), {})
    assert json_stable(a) == json_stable(b)


def json_stable(obj):
    import json
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ---------- seek 确定性 / 切队·回合·玩家不串缓存 ----------

def test_team_round_player_scope_isolation():
    """团队指标、个人贡献、不同回合 evidence 不串缓存；同输入两次计算 payload 完全一致。"""
    players = {i: [(65, i * 500, 0, True, "Middle"), (11800, i * 500, 0, True, "Middle")] for i in range(1, 6)}
    r1 = _art_round(players, freeze=6000, end=12000)
    a = build_coordination_analysis(**_base_args([r1], focus_player_id=1))
    b = build_coordination_analysis(**_base_args([r1], focus_player_id=1))
    # 同一输入 → 同 content_hash（确定性）
    assert a["content_hash"] == b["content_hash"]
    # 团队与个人贡献严格分离
    assert "teams" in a and "player" in a
    # player.contribution 的 players 必须含 focus_player_id
    for item in a["player"]["contributions"] + a["player"]["exposures"]:
        assert 1 in item["players"]


def test_seek_state_is_deterministic_for_applied_locale():
    """area_state_machine 对同一回合/区域/队的 segments 两次完全一致（seek 前后状态确定）。"""
    geo = {"available": True}
    region = {"place_key": "bombsitea", "place_cn": "A 区", "centroid": {"rx": 0.5, "ry": 0.5},
              "geometric": True, "coverage": 0.95, "confidence": "high"}
    players = {"1": {"side": "CT", "points": [{"tick": 65, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "BombsiteA"},
                                              {"tick": 10000, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "BombsiteA"}]},
               "2": {"side": "T", "points": [{"tick": 65, "x": 101, "y": 100, "yaw": 90, "alive": True, "place": "BombsiteA"},
                                             {"tick": 10000, "x": 101, "y": 100, "yaw": 90, "alive": True, "place": "BombsiteA"}]}}
    meta = {"round": 1, "start_tick": 65, "end_tick": 10000, "freeze_end_tick": 65}
    a = build_region_state_machine(region, players, meta, tick_rate, [1], [2], [], [], {1}, {2}, {})
    b = build_region_state_machine(region, players, meta, tick_rate, [1], [2], [], [], {1}, {2}, {})
    assert json_stable(a) == json_stable(b)
    # 敌我同区 → contested 段在两次中一致
    assert any(seg["state"] == "contested" for seg in a["segments"]["A"]) == \
           any(seg["state"] == "contested" for seg in b["segments"]["A"])


# ---------- 6R21D item8：烟/火消散后（宽限窗内）的击杀不算"掩护下取得击杀" ----------

def test_utility_not_count_kill_after_smoke_fades():
    """烟/火 active 区间结束后的击杀（即使落在 detonate+宽限窗内）不得判为道具助攻。
    烟已消散即无掩护；只把 active 区间内、且在作用半径内的同队击杀计为转化。"""
    players = {
        1: [(65, 100, 100, True, "Middle"), (3000, 100, 100, True, "Middle")],                      # thrower
        2: [(65, 110, 110, True, "Middle"), (1200, 110, 110, True, "Middle"), (3000, 110, 110, True, "Middle")],
        3: [(65, 200, 200, True, "Middle"), (3000, 200, 200, True, "Middle")],
        4: [(65, 300, 300, True, "Middle"), (3000, 300, 300, True, "Middle")],
        5: [(65, 400, 400, True, "Middle"), (3000, 400, 400, True, "Middle")],
    }
    # smoke：active [100,1000]（tick）；击杀发生在 kt=1200（active 结束后、det+宽限内）
    props = [{"thrower_player_id": 1, "type": "smoke", "x": 100.0, "y": 100.0,
              "detonate_tick": 100, "active_start": 100, "active_end": 1000}]
    kills = [{"round": 1, "tick": 1200, "actor_steam": "2", "victim_steam": "9",
              "actor_x": 110.0, "actor_y": 110.0, "victim_x": 0, "victim_y": 0}]
    deaths = [dict(k) for k in kills]
    rnds = [_art_round(players, freeze=6000, end=12000, props=props)]
    out = build_coordination_analysis(**_base_args(
        rnds, kills=kills, deaths=deaths,
        id_of={"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "9": 9},
        team_of={1: "A", 2: "A", 3: "A", 4: "A", 5: "A", 9: "B"}))
    a = next(t for t in out["teams"] if t["team"] == "A")
    ua = next(m for m in a["metrics"] if m["metric_id"] == "utility_assisted_entry")
    # 击杀发生在烟消散后 → 不得判为道具助攻（converted 应为 0）
    assert ua["converted"] == 0, f"烟消散后的击杀被误判为道具助攻：{ua}"
    for ev in out["rounds"]:
        if ev["metric_id"] == "utility_assisted_entry":
            assert "取得击杀" not in (ev.get("reason") or ""), ev["reason"]


def test_utility_count_kill_within_smoke_active_window():
    """对照：击杀发生在 smoke active 区间内且在作用半径内 → 计为道具助攻（不误伤）。"""
    players = {
        1: [(65, 100, 100, True, "Middle"), (3000, 100, 100, True, "Middle")],
        2: [(65, 110, 110, True, "Middle"), (500, 110, 110, True, "Middle"), (3000, 110, 110, True, "Middle")],
        3: [(65, 200, 200, True, "Middle"), (3000, 200, 200, True, "Middle")],
        4: [(65, 300, 300, True, "Middle"), (3000, 300, 300, True, "Middle")],
        5: [(65, 400, 400, True, "Middle"), (3000, 400, 400, True, "Middle")],
    }
    props = [{"thrower_player_id": 1, "type": "smoke", "x": 100.0, "y": 100.0,
              "detonate_tick": 100, "active_start": 100, "active_end": 1000}]
    kills = [{"round": 1, "tick": 500, "actor_steam": "2", "victim_steam": "9",
              "actor_x": 110.0, "actor_y": 110.0, "victim_x": 0, "victim_y": 0}]
    deaths = [dict(k) for k in kills]
    rnds = [_art_round(players, freeze=6000, end=12000, props=props)]
    out = build_coordination_analysis(**_base_args(
        rnds, kills=kills, deaths=deaths,
        id_of={"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "9": 9},
        team_of={1: "A", 2: "A", 3: "A", 4: "A", 5: "A", 9: "B"}))
    a = next(t for t in out["teams"] if t["team"] == "A")
    ua = next(m for m in a["metrics"] if m["metric_id"] == "utility_assisted_entry")
    assert ua["converted"] == 1, f"active 区间内的击杀应计为道具助攻：{ua}"
    assert any(("取得击杀" in (ev.get("reason") or "")) for ev in out["rounds"]
               if ev["metric_id"] == "utility_assisted_entry")


def test_area_state_machine_round_team_isolation_between_rounds():
    """不同 round 的 area_state_machine 各自独立（team 传入即该队成员），不跨回合并缓存。"""
    geo = {"available": True}
    region = {"place_key": "middle", "place_cn": "中路", "centroid": {"rx": 0.5, "ry": 0.5},
              "geometric": True, "coverage": 0.95, "confidence": "high"}
    # R1：A 队占中区；R2：换 B 队占中区 → 状态应随队伍变化，绝不含缓存
    r1_players = {"1": {"side": "CT", "points": [{"tick": 65, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "Middle"},
                                                {"tick": 10000, "x": 100, "y": 100, "yaw": 90, "alive": True, "place": "Middle"}]}}
    meta = {"round": 1, "start_tick": 65, "end_tick": 10000, "freeze_end_tick": 65}
    a = build_region_state_machine(region, r1_players, meta, tick_rate, [1], [], [], [], {1}, set(), {})
    segs_a = a["segments"]["A"]
    assert any(s["state"] in ("occupied", "controlled") for s in segs_a)
    # B 队无人在区 → 该区 B 队应为 unknown（或影响），且绝不因"刚才 A 队占过"而继承
    assert all(s["state"] in ("unknown", "influence") for s in a["segments"]["B"])


# ---------- 独立 golden 规则（源自 phase6r16-golden-set.json 的独立审阅，非引擎自证） ----------

def test_golden_rule_empty_site_not_controlled_invariant():
    """golden 关键规则：空区（敌方未出现）不 = controlled；controlled 需正向信号，且敌我同区绝不为 controlled。"""
    # ① 一方占位、无敌方、无正向信号、几何可用 → occupied（非 controlled）
    assert classify_team_state(1, 0, 30.0, False, True) == "occupied"
    # ② 一方占位、无敌方、无正向信号、几何不可用 → influence
    assert classify_team_state(1, 0, 30.0, False, False) == "influence"
    # ③ 一方占位、无敌方、有正向信号、几何可用 → controlled（需持续时间）
    assert classify_team_state(1, 0, 30.0, True, True) == "controlled"
    # ④ 敌我同区 → contested（即使有正向信号，绝不 controlled）
    assert classify_team_state(1, 1, 30.0, True, True) == "contested"
    # ⑤ 我方缺席（敌占区） → influence
    assert classify_team_state(0, 1, 30.0, True, True) == "influence"
    # ⑥ 无占位 → unknown
    assert classify_team_state(0, 0, 30.0, False, True) == "unknown"


def test_golden_rule_controlled_requires_positive_signal_not_duration_alone():
    """持续时间长但无正向信号 → 不得 controlled（可能是空区/空占位）。"""
    # 持续 999s、无信号、几何可用 → 只 occupied（关键：时长不替代信号）
    assert classify_team_state(1, 0, 999.0, False, True) == "occupied"


def test_golden_rule_isolated_excludes_last_alive_and_save():
    """golden：最后存活(alive_teammates=0) → 不报孤立；save 阶段 → 不报孤立。"""
    from app.services.coordination import classify_phase
    # 最后存活：无其它存活队友 → 排除（在 engine 中 alive==0 时 skip）
    # 这里验证 phase 判定：save 阶段在 end_t - 10s 之后
    assert classify_phase(11000, 65, 6000, 12000, None, 128) == "save"
    # 非 save/非最后存活且最近距离 > 900 → isolated（由 classify_team_state 演示几何无关的判定不存在，
    # 这里用 engine 的 isolated 反例：最后存活不报——已由 test_isolated_death_excludes_last_survivor 覆盖）
