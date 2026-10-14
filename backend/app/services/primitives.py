"""6R8：可解释深度配合原语（本阶段打地基，不造黑箱总分）。

为每回合产出**可追溯事件候选**，每条带 round / 可读秒 / 中文区域 / 涉及玩家，并能定位到
同步回放。全部基于已证明字段（位置 + 真实 yaw + 道具生命周期 + 击杀事件），给出定义、证据
与限制；**不随意加权成"深度配合总分"**，留给 6R9 标定。

诚实约束：
- crossfire 只有在真实 yaw + 几何证据成立时才能命名；本阶段无 navmesh/墙体遮挡验证，
  因此只产生「交叉火力候选」并写 reason（无 LoS 墙体验证）。
- 坐标/原始 tick 仅用于计算与定位，原语对外只输出 round / mm:ss / 中文区域 / 玩家。
"""
from __future__ import annotations

import math
from itertools import combinations

from app.services.places import translate_place

TRADE_WINDOW_SEC = 5.0
CLOSE_SPACING_WORLD = 600.0
SIMULT_WINDOW_SEC = 3.0
CROSSFIRE_ANGLE_DEG = 40.0
CROSSFIRE_RANGE_WORLD = 1400.0
UTILITY_WINDOW_SEC = 6.0
ISOLATED_SUPPORT_DIST = 900.0
AREA_MIN_OCCUPANCY_SEC = 10.0
AREA_SAMPLE_STEP = 16

_PROP_ENTRY = ("flash", "smoke", "he")
_PROP_LABEL = {"flash": "闪光", "smoke": "烟雾", "he": "手雷", "fire": "燃烧"}


def _dist(ax, ay, bx, by):
    return math.hypot((ax or 0) - (bx or 0), (ay or 0) - (by or 0))


def _deg(a, b):
    d = abs((a or 0) - (b or 0)) % 360
    return min(d, 360 - d)


def _team(tid_map, pid):
    return tid_map.get(pid)


def _at(points, tick):
    """某玩家 tick 时最近采样点（含位置/存活/yaw）。"""
    if not points:
        return None
    best = points[0]; bd = 10 ** 18
    for p in points:
        d = abs((p["tick"] or 0) - tick)
        if d < bd:
            bd = d; best = p
    return best


def _region_cn(map_name, place):
    return translate_place(map_name, place) if place else "未知区域"


def _place_of(pts_by_pid, pid, tick):
    return (_at(pts_by_pid.get(str(pid), {}).get("points", []), tick) or {}).get("place")


def _id_for_steam(id_of, steam):
    return id_of.get(str(steam)) if steam is not None else None


def _cand(kind, rnd, time, region, player_ids, **kw):
    return {"kind": kind, "round": rnd, "time": time, "region": region, "players": player_ids, **kw}


def compute_primitives(artifact_rounds, map_name, tick_rate, kills, deaths, id_of, name_of, team_of):
    """从战术 artifact 回合 + DB 击杀/死亡事件计算每回合原语候选。

    artifact_rounds：[{round, start_tick, end_tick, freeze_end_tick, players:{pid:{points,side}}, props}]
    kills/deaths：[{round, tick, actor_steam, victim_steam, actor_x, actor_y, victim_x, victim_y}]
    id_of: steam→player_id；name_of: player_id→name；team_of: player_id→"A"|"B"
    """
    out = []
    ks_r: dict[int, list[dict]] = {}
    for k in kills:
        ks_r.setdefault(k["round"], []).append(k)
    ds_r: dict[int, list[dict]] = {}
    for d in deaths:
        ds_r.setdefault(d["round"], []).append(d)

    for rnd in artifact_rounds:
        r = rnd["round"]
        start_t = rnd["start_tick"]; end_t = rnd["end_tick"]
        freeze = rnd.get("freeze_end_tick") or start_t
        players = rnd.get("players", {})
        props = rnd.get("props", [])
        pts_by_pid = {int(pid): pd for pid, pd in players.items()}

        def _sec(tick):
            return round((tick - freeze) / tick_rate, 2) if tick_rate else None

        ks = ks_r.get(r, [])
        ds = ds_r.get(r, [])

        # ----- trade window -----
        for d in ds:
            k_steam = d["actor_steam"]
            v_id = _id_for_steam(id_of, d["victim_steam"])
            if v_id is None:
                continue
            v_team = _team(team_of, v_id)
            if v_team is None:
                continue
            dtick = int(d["tick"])
            for k in ks:
                if str(k["victim_steam"]) == str(k_steam):
                    kb_id = _id_for_steam(id_of, k["actor_steam"])
                    if kb_id is None:
                        continue
                    if _team(team_of, kb_id) == v_team and kb_id != v_id:
                        if tick_rate and 0 < (int(k["tick"]) - dtick) <= TRADE_WINDOW_SEC * tick_rate:
                            out.append(_cand("trade_window", r, _sec(int(k["tick"])),
                                             _region_cn(map_name, _place_of(pts_by_pid, kb_id, int(k["tick"]))),
                                             [v_id, kb_id],
                                             definition="队友在死亡后补掉击杀者（交换）",
                                             evidence=f"死亡 {d.get('victim_name')} → 补枪 {k.get('actor_name')}"))
                            break

        # ----- utility-assisted entry -----
        for k in ks:
            k_actor = _id_for_steam(id_of, k["actor_steam"])
            if k_actor is None:
                continue
            team = _team(team_of, k_actor)
            if team is None:
                continue
            kt = int(k["tick"])
            for pr in props:
                if pr["type"] not in _PROP_ENTRY:
                    continue
                if pr.get("thrower_player_id") in (None, k_actor):
                    continue
                if _team(team_of, pr["thrower_player_id"]) != team:
                    continue
                if tick_rate and 0 <= (kt - int(pr["detonate_tick"])) <= UTILITY_WINDOW_SEC * tick_rate:
                    if _dist(pr.get("x"), pr.get("y"), k.get("actor_x"), k.get("actor_y")) <= CROSSFIRE_RANGE_WORLD:
                        out.append(_cand("utility_assisted_entry", r, _sec(kt),
                                         _region_cn(map_name, _place_of(pts_by_pid, k_actor, kt)),
                                         [k_actor, pr["thrower_player_id"]],
                                         definition=f"队友《{_PROP_LABEL.get(pr['type'], pr['type'])}》辅助下的进点击杀",
                                         evidence=f"道具 detonate≈{_sec(int(pr['detonate_tick']))}s，击杀≈{_sec(kt)}s"))
                        break

        # ----- isolated death -----
        for d in ds:
            v_id = _id_for_steam(id_of, d["victim_steam"])
            if v_id is None:
                continue
            team = _team(team_of, v_id)
            if team is None:
                continue
            dt = int(d["tick"])
            nearest = 10 ** 18
            for pid, pd in pts_by_pid.items():
                if _team(team_of, pid) != team or pid == v_id:
                    continue
                a = _at(pd.get("points", []), dt)
                if a and a.get("alive"):
                    nearest = min(nearest, _dist(d.get("victim_x"), d.get("victim_y"), a["x"], a["y"]))
            if nearest >= ISOLATED_SUPPORT_DIST:
                out.append(_cand("isolated_death", r, _sec(dt),
                                 _region_cn(map_name, _place_of(pts_by_pid, v_id, dt)),
                                 [v_id],
                                 definition="阵亡时队友距离过远（孤立）",
                                 evidence=f"最近队友≈{round(nearest)}（阈值 {ISOLATED_SUPPORT_DIST}）"))

        # ----- simultaneous contact（同队多击杀集中） -----
        team_kills: dict[str, list[dict]] = {}
        for k in ks:
            actor = _id_for_steam(id_of, k["actor_steam"])
            t = _team(team_of, actor) if actor is not None else None
            if t:
                team_kills.setdefault(t, []).append(k)
        for t, tk in team_kills.items():
            tk.sort(key=lambda x: int(x["tick"]))
            for i in range(1, len(tk)):
                if tick_rate and (int(tk[i]["tick"]) - int(tk[i - 1]["tick"])) <= SIMULT_WINDOW_SEC * tick_rate:
                    out.append(_cand("simultaneous_contact", r, _sec(int(tk[i]["tick"])),
                                     _region_cn(map_name, _place_of(pts_by_pid, _id_for_steam(id_of, tk[i]["actor_steam"]), int(tk[i]["tick"]))),
                                     [_id_for_steam(id_of, tk[i - 1]["actor_steam"]), _id_for_steam(id_of, tk[i]["actor_steam"])],
                                     definition=f"{t} 在 {SIMULT_WINDOW_SEC}s 内接连击杀",
                                     evidence=f"击杀1≈{_sec(int(tk[i-1]['tick']))}s，击杀2≈{_sec(int(tk[i]['tick']))}s"))
                    break

        # ----- crossfire candidate -----
        contact_points = [(k["actor_x"], k["actor_y"], int(k["tick"])) for k in ks if k.get("actor_x") is not None]
        team_ids = list({pid for pid in pts_by_pid if _team(team_of, pid) is not None})
        for c in contact_points:
            cx, cy, ct = c
            for a_pid, b_pid in combinations(team_ids, 2):
                if _team(team_of, a_pid) != _team(team_of, b_pid):
                    continue
                a = _at(pts_by_pid.get(a_pid, {}).get("points", []), ct)
                b = _at(pts_by_pid.get(b_pid, {}).get("points", []), ct)
                if not a or not b or a.get("yaw") is None or b.get("yaw") is None:
                    continue
                if _dist(a["x"], a["y"], cx, cy) > CROSSFIRE_RANGE_WORLD or _dist(b["x"], b["y"], cx, cy) > CROSSFIRE_RANGE_WORLD:
                    continue
                ang_a = math.degrees(math.atan2(cy - a["y"], cx - a["x"]))
                ang_b = math.degrees(math.atan2(cy - b["y"], cx - b["x"]))
                if _deg(ang_a, ang_b) >= CROSSFIRE_ANGLE_DEG:
                    out.append(_cand("crossfire_candidate", r, _sec(ct),
                                     _region_cn(map_name, _place_of(pts_by_pid, a_pid, ct)),
                                     [a_pid, b_pid],
                                     definition="两队友朝向同一交战点且角度差大（交叉火力候选）",
                                     evidence=f"角度差 {_deg(ang_a, ang_b):.0f}°",
                                     reason="无墙体遮挡验证（本阶段无 navmesh），仅朝向+几何候选"))
                    break

        # ----- spacing（间距：两队友同时存活且距离很近） -----
        if tick_rate:
            best_spacing = None   # (min_dist, tick, pids)
            for t in range(start_t, end_t + 1, AREA_SAMPLE_STEP):
                # 该 tick 存活队员位置
                alive_pos = []
                for pid, pd in pts_by_pid.items():
                    if _team(team_of, pid) is None:
                        continue
                    a = _at(pd.get("points", []), t)
                    if a and a.get("alive"):
                        alive_pos.append((pid, a["x"], a["y"]))
                for pid_a, ax, ay in alive_pos:
                    for pid_b, bx, by in alive_pos:
                        if pid_a >= pid_b or _team(team_of, pid_a) != _team(team_of, pid_b):
                            continue
                        d = _dist(ax, ay, bx, by)
                        if d < CLOSE_SPACING_WORLD and (best_spacing is None or d < best_spacing[0]):
                            best_spacing = (d, t, [pid_a, pid_b])
            if best_spacing:
                d, t, pids = best_spacing
                out.append(_cand("spacing", r, _sec(t), _region_cn(map_name, _place_of(pts_by_pid, pids[0], t)),
                                 pids, definition="两队友同时存活且距离很近（协同间距）",
                                 evidence=f"最近距离≈{round(d)}（阈值 {CLOSE_SPACING_WORLD}）"))

        # ----- area control（在 _area_control 中追加） -----
        for team in ("CT", "T"):
            ac = _area_control(r, team, pts_by_pid, start_t, end_t, freeze, tick_rate, map_name)
            if ac:
                out.append(ac)

    out.sort(key=lambda x: (x["round"], x.get("time") or 0))
    # 克制信息密度：每个 (round, kind) 最多保留前 MAX_PER_KIND_Round 条候选，
    # 避免 139 条 crossfire 等事实列表充斥主界面（原语是"候选"，交由 UI 呈现，不刷屏）。
    MAX_PER_KIND_ROUND = 4
    capped: list[dict] = []
    counts: dict[tuple[int, str], int] = {}
    for p in out:
        key = (p["round"], p["kind"])
        if counts.get(key, 0) < MAX_PER_KIND_ROUND:
            capped.append(p)
            counts[key] = counts.get(key, 0) + 1
    return capped


def _area_control(r, team, pts_by_pid, start_t, end_t, freeze, tick_rate, map_name):
    if not tick_rate:
        return None
    team_members = [pid for pid, pd in pts_by_pid.items() if pd.get("side") == team]
    if not team_members:
        return None
    best_region = None; best_sec = 0.0
    cur_region = None; cur_start = None
    for t in range(start_t, end_t + 1, AREA_SAMPLE_STEP):
        region = None
        for pid in team_members:
            a = _at(pts_by_pid.get(pid, {}).get("points", []), t)
            if a and a.get("alive"):
                region = _region_cn(map_name, a.get("place"))
                break
        if region != cur_region:
            if cur_region and cur_start is not None:
                sec = (t - cur_start) / tick_rate
                if sec > best_sec and sec >= AREA_MIN_OCCUPANCY_SEC:
                    best_sec = sec; best_region = cur_region
            cur_region = region; cur_start = t
    if cur_region and cur_start is not None:
        sec = (end_t - cur_start) / tick_rate
        if sec > best_sec and sec >= AREA_MIN_OCCUPANCY_SEC:
            best_sec = sec; best_region = cur_region
    if best_region and best_sec > 0:
        return _cand("area_control", r, round(best_sec, 2), best_region, team_members,
                     definition=f"{team} 连续占据 {best_region}", team=team,
                     evidence=f"连续占据≈{best_sec:.0f} 秒（阈值 {AREA_MIN_OCCUPANCY_SEC}s）")
    return None
