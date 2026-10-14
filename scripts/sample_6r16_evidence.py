"""6R16 证据管线 A：分层抽样（有界）——只取原始事实 + 记录引擎 actual（分离，避免循环）。

目标：抽到覆盖所有分支的【有界】集合（≥30），写 docs/phase6r16-review-input.json（纯原始事实，
供独立审阅者判定 expected），并写 docs/phase6r16-engine-actual.json（该 ep 的引擎实际输出，仅对比）。

审阅者只读 review-input.json；引擎 actual 绝不进审阅输入，因此不循环。
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from minio import Minio  # noqa: E402
from app.services.routes import gunzip_json  # noqa: E402
from app.services.region_geometry import load_region_geometry  # noqa: E402
from app.services.places import translate_place  # noqa: E402
import app.services.coordination as C  # noqa: E402
from app.services.area_state import build_region_state_machine_for_round  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCKER = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe"
DEMOS = {19: (59, "de_mirage"), 20: (60, "de_anubis"), 21: (61, "de_dust2"),
         22: (62, "de_inferno"), 23: (63, "de_ancient")}
SCHEMA = "3.0"
TICK_RATE = 64.0
ISOLATED_SUPPORT_DIST = 900.0
CLOSE_SPACING = 600.0
TRADE_WINDOW_SEC = 5.0
SIMULT_WINDOW_SEC = 3.0
UTILITY_WINDOW_SEC = 6.0

# 分支覆盖目标：metrics(7) + area(5 states)。每分支至少 1，正反例尽量都有。
BRANCHES = ["trade", "simultaneous_contact", "spacing", "utility_assisted_entry",
            "isolated_death", "crossfire_candidate", "area_control"]
# 每分支、每 demo 最多取 1-2 条（有界）
MAX_PER_BRANCH_PER_DEMO = 2
TOTAL_TARGET = 200   # 放宽上限：覆盖全部 5 demo（每分支每 demo 1-2 条）


def _psql_rows(sql):
    out = subprocess.run([DOCKER, "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo",
                          "-t", "-A", "-F", "|", "-c", sql], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return [line.split("|") for line in out.stdout.splitlines() if line.strip()]


def _load(demo_id):
    match_id, map_name = DEMOS[demo_id]
    c = Minio("localhost:9000", access_key="cs2minio", secret_key="cs2minio_secret", secure=False)
    art = gunzip_json(c.get_object("cs2demo", f"tactical/{match_id}/{SCHEMA}/tactical.json.gz").read())
    players = _psql_rows(f"SELECT id, steam_id, name, team_identity FROM players WHERE match_id={match_id};")
    id_of = {r[1]: int(r[0]) for r in players}
    name_of = {int(r[0]): r[2] for r in players}
    team_of = {int(r[0]): r[3] for r in players}
    kills = []
    for r in _psql_rows(f"SELECT round, tick, actor_steam, victim_steam, actor_x, actor_y, victim_x, victim_y "
                        f"FROM events WHERE match_id={match_id} AND event_type='kill';"):
        def _f(x):
            try:
                return float(x) if x not in ("", "None", None) else None
            except Exception:
                return None
        kills.append({"round": int(r[0]), "tick": int(r[1]), "actor_steam": r[2], "victim_steam": r[3],
                      "actor_x": _f(r[4]), "actor_y": _f(r[5]), "victim_x": _f(r[6]), "victim_y": _f(r[7]),
                      "actor_name": name_of.get(id_of.get(r[2], 0), (r[2] or "")[:8]),
                      "victim_name": name_of.get(id_of.get(r[3] or "", 0), (r[3] or "")[:8])})
    return {"artifact": art, "map_name": map_name, "id_of": id_of, "name_of": name_of,
            "team_of": team_of, "kills": kills, "deaths": [dict(k) for k in kills], "match_id": match_id}


def _phase(tick, start, freeze, end, bomb, tr):
    if not tr:
        return "execute"
    if tick < freeze:
        return "freeze"
    if tick >= end - 10 * tr:
        return "save"
    if tick < freeze + 5 * tr:
        return "opening"
    if bomb is not None and tick >= bomb:
        return "post_plant"
    return "execute"


def _norm(p):
    return "".join(ch.lower() for ch in str(p or "") if ch.isalnum())


def _at(points, tick):
    if not points:
        return None
    n = len(points)
    lo, hi = 0, n - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if (points[mid]["tick"] or 0) < tick:
            lo = mid + 1
        else:
            hi = mid
    best = None; bd = 10 ** 18
    for i in (lo, lo - 1, lo + 1):
        if 0 <= i < n:
            d = abs((points[i]["tick"] or 0) - tick)
            if d < bd:
                bd = d; best = points[i]
    return best


def _dist(ax, ay, bx, by):
    return math.hypot((ax or 0) - (bx or 0), (ay or 0) - (by or 0))


def _sec(tick, freeze, tr):
    return round(max(0.0, (tick - freeze) / tr), 2) if tr else None


def _raw_area_windows(mem, pts, props, rk, freeze, tr):
    """原始 per-tick 占位/信号窗口（raw facts，非引擎判定）。

    返回 (occupancy_windows, signal_windows)：
    - occupancy_windows: {team: [连续占位段]}，每段 {start_sec,end_sec,dur}（该队在该区至少 1 名存活成员）。
    - signal_windows: [{start_sec,end_sec}]（该区 smoke/fire active 覆盖的连续段）。
    按 tick 采样（AREA_SAMPLE_STEP，此处用 16 tick）聚合连续段；秒相对 freeze_end。
    """
    pre = {"A": 0, "B": 0}
    # 先收集每采样 tick 的 (team 占位人数)
    samples_t = []
    # 用该回合全部 players 的 place 构造 per-tick 占位（跨玩家聚合）
    tick_min = None; tick_max = None
    for pid in mem["A"] + mem["B"]:
        for s in pts.get(pid, {}).get("points", []):
            tk = s["tick"]
            tick_min = tk if tick_min is None or tk < tick_min else tick_min
            tick_max = tk if tick_max is None or tk > tick_max else tick_max
    if tick_min is None:
        return {"A": [], "B": []}, []
    for tk in range(tick_min, tick_max + 1, 16):
        a = b = 0
        for pid in mem["A"]:
            pt = _at(pts.get(pid, {}).get("points", []), tk)
            if pt and pt.get("alive") and _norm(pt.get("place") or "") == rk:
                a += 1
        for pid in mem["B"]:
            pt = _at(pts.get(pid, {}).get("points", []), tk)
            if pt and pt.get("alive") and _norm(pt.get("place") or "") == rk:
                b += 1
        sec = _sec(tk, freeze, tr)
        samples_t.append({"sec": sec, "A": a, "B": b})
    # 聚合连续占位段（按 team）
    def _segments(key):
        segs = []
        cur = None
        for s in samples_t:
            on = s[key] > 0
            if cur and on == cur["on"]:
                cur["last_sec"] = s["sec"]
            else:
                if cur and cur["on"]:
                    segs.append(cur)
                cur = {"on": on, "start_sec": s["sec"], "last_sec": s["sec"]}
        if cur and cur["on"]:
            segs.append(cur)
        return [{"start_sec": round(s["start_sec"], 2), "end_sec": round(s["last_sec"], 2),
                 "dur": round(s["last_sec"] - s["start_sec"], 2)} for s in segs]
    occupancy = {"A": _segments("A"), "B": _segments("B")}
    # signal：区内 smoke/fire active 段（frame 相对 freeze）
    signal = []
    for p in props:
        if p.get("type") in ("smoke", "fire") and p.get("active_start") is not None and p.get("active_end") is not None:
            thrower = p.get("thrower_player_id"); det = p.get("detonate_tick")
            if thrower is not None and det is not None:
                a = _at(pts.get(thrower, {}).get("points", []), det)
                if a and _norm(a.get("place") or "") == rk:
                    signal.append({"start_sec": _sec(p["active_start"], freeze, tr),
                                   "end_sec": _sec(p["active_end"], freeze, tr)})
    signal.sort(key=lambda x: x.get("start_sec") or 0)
    return occupancy, signal


def main():
    samples = []
    cur_branch_n = {}
    # 引擎 actual 聚合（一次 per demo）
    engine_metrics = {}
    area_state_by_ep = {}
    # 先全局建立引擎 actual（每 demo 一次协调分析 + 每回合区域状态机）
    for demo_id, (match_id, map_name) in sorted(DEMOS.items()):
        snap = _load(demo_id)
        art = snap["artifact"]; tr = art.get("tick_rate") or TICK_RATE
        geo = load_region_geometry(map_name)
        id_of = snap["id_of"]; team_of = snap["team_of"]
        eng = C.build_coordination_analysis(
            art["rounds"], map_name, tr, snap["kills"], snap["deaths"], id_of, snap["name_of"], team_of,
            region_geometry={map_name: geo} if geo else None)
        eng_agg = {}
        for t in eng["teams"]:
            for m in t["metrics"]:
                eng_agg.setdefault(m["metric_id"], {})[t["team"]] = m
        engine_metrics[demo_id] = eng_agg
    # 引擎区域状态机 actual
    for demo_id, (match_id, map_name) in sorted(DEMOS.items()):
        snap = _load(demo_id)
        art = snap["artifact"]; tr = art.get("tick_rate") or TICK_RATE
        geo = load_region_geometry(map_name)
        regions = (geo or {}).get("regions", [])
        team_of = snap["team_of"]
        for rnd in art["rounds"]:
            pts = {int(p): pd for p, pd in rnd.get("players", {}).items()}
            members = {"A": [p for p in pts if team_of.get(p) == "A"], "B": [p for p in pts if team_of.get(p) == "B"]}
            props = rnd.get("props", []); blinds = rnd.get("blinds", [])
            for region in regions:
                for t in ("A", "B"):
                    tm = set(p.get("thrower_player_id") for p in props if p.get("thrower_player_id") in members[t])
                    sm = build_region_state_machine_for_round(region, pts, rnd, tr, members, props, blinds, tm, lambda p: "")
                    segs = sm.get("segments", {}).get(t, [])
                    # 最有意义状态：优先非 unknown，再按持续时长（本轮先用，后处理合并两队的可证状态）
                    meaningful = [s for s in segs if s["state"] != "unknown"]
                    pool = meaningful if meaningful else segs
                    best = max(pool, key=lambda s: s.get("dur", 0)) if pool else None
                    area_state_by_ep.setdefault(f"area_{demo_id}_{rnd['round']}_{region['place_key']}", {}).setdefault(t, []).extend(
                        [(s["state"], s["dur"]) for s in segs if s["state"] != "unknown"])

    for demo_id, (match_id, map_name) in sorted(DEMOS.items()):
        snap = _load(demo_id)
        art = snap["artifact"]; tr = art.get("tick_rate") or TICK_RATE
        geo = load_region_geometry(map_name)
        regions = (geo or {}).get("regions", [])
        id_of = snap["id_of"]; name_of = snap["name_of"]; team_of = snap["team_of"]
        kills = snap["kills"]
        eng_agg = engine_metrics[demo_id]

        for rnd in art["rounds"]:
            if len(samples) >= TOTAL_TARGET:
                break
            r = rnd["round"]
            start_t = rnd["start_tick"]; end_t = rnd["end_tick"]
            freeze = rnd.get("freeze_end_tick") or start_t
            bomb = rnd.get("bomb_planted_tick")
            pts = {int(p): pd for p, pd in rnd.get("players", {}).items()}
            members = {"A": [p for p in pts if team_of.get(p) == "A"], "B": [p for p in pts if team_of.get(p) == "B"]}
            props = rnd.get("props", []); blinds = rnd.get("blinds", [])
            he_damage = rnd.get("he_damage", []); shoots = rnd.get("shoots", [])
            round_kills = [k for k in kills if k["round"] == r]

            def _branch_ok(branch, demo):
                return cur_branch_n.get((branch, demo), 0) < MAX_PER_BRANCH_PER_DEMO

            # ---- trade ----
            if round_kills and _branch_ok("trade", demo_id):
                k = round_kills[0]
                vpid = id_of.get(k["victim_steam"]); vteam = team_of.get(vpid) if vpid is not None else None
                a_team = team_of.get(id_of.get(k["actor_steam"]))
                if vpid is not None and vteam and vteam != a_team:
                    dt = int(k["tick"])
                    # 该阵亡后 5s 内是否有"击杀者被击杀"（补枪）
                    trade = False; trader = None
                    for k2 in round_kills:
                        if str(k2.get("victim_steam")) == str(k.get("actor_steam")):
                            kb = id_of.get(k2.get("actor_steam"))
                            if kb is not None and team_of.get(kb) == vteam and kb != vpid and int(k2["tick"]) > dt and int(k2["tick"]) - dt <= TRADE_WINDOW_SEC * tr:
                                trade = True; trader = name_of.get(kb); break
                    ep = f"trade_{demo_id}_{r}_{dt}"
                    samples.append({"ep_id": ep, "branch": "trade", "demo": demo_id, "map": map_name, "round": r,
                                    "sec": _sec(dt, freeze, tr), "region": translate_place(map_name, (_at(pts.get(vpid, {}).get("points", []), dt) or {}).get("place")),
                                    "victim": k["victim_name"], "victim_team": vteam,
                                    "killer": k["actor_name"], "killer_team": a_team, "death_tick": dt,
                                    "killer_alive_at_death": bool((_at(pts.get(id_of.get(k["actor_steam"]), {}).get("points", []), dt) or {}).get("alive")),
                                    "alive_teammates": [name_of.get(p) for p in members[vteam] if p != vpid and bool((_at(pts.get(p, {}).get("points", []), dt) or {}).get("alive"))],
                                    "followup_trade_kill": {"occurred": trade, "trader": trader, "within_5s": trade}})
                    cur_branch_n[("trade", demo_id)] = cur_branch_n.get(("trade", demo_id), 0) + 1
                    t_agg = eng_agg.get("trade", {}).get(vteam)
                    if t_agg: print(f"trade {demo_id} R{r}: opp={t_agg['opportunities']} conv={t_agg['converted']} trust={t_agg['trust']}", flush=True)

            # ---- simultaneous_contact ----
            if len(round_kills) >= 2 and _branch_ok("simultaneous_contact", demo_id):
                k1, k2 = round_kills[0], round_kills[1]
                a1 = id_of.get(k1["actor_steam"]); a2 = id_of.get(k2["actor_steam"])
                t1 = team_of.get(a1) if a1 is not None else None
                same = abs(int(k1["tick"]) - int(k2["tick"])) <= SIMULT_WINDOW_SEC * tr
                ep = f"sim_{demo_id}_{r}_{k1['tick']}"
                samples.append({"ep_id": ep, "branch": "simultaneous_contact", "demo": demo_id, "map": map_name, "round": r,
                                "sec": _sec(int(k2["tick"]), freeze, tr), "region": translate_place(map_name, (_at(pts.get(id_of.get(k2['victim_steam']), {}).get("points", []), int(k2["tick"])) or {}).get("place")),
                                "k1": {"actor": name_of.get(a1), "team": t1, "tick": k1["tick"]},
                                "k2": {"actor": name_of.get(a2), "team": team_of.get(a2), "tick": k2["tick"]},
                                "same_3s_window": same, "distinct_actors": a1 != a2, "same_team": t1 == team_of.get(a2)})
                cur_branch_n[("simultaneous_contact", demo_id)] = cur_branch_n.get(("simultaneous_contact", demo_id), 0) + 1
                t_agg = eng_agg.get("simultaneous_contact", {}).get(t1 or "A")
                if t_agg: print(f"simult {demo_id} R{r}: opp={t_agg['opportunities']} conv={t_agg['converted']} trust={t_agg['trust']}", flush=True)

            # ---- spacing（战术期非出生点样本）：取扎堆最严重 + 最分散 两个代表（覆盖正/反例）----
            if _branch_ok("spacing", demo_id):
                best_cram = None   # cf 最大（最扎堆）
                best_disc = None   # cf 最小（最分散）
                for tk in range(freeze if freeze is not None else start_t, end_t + 1, 16):
                    ph = _phase(tk, start_t, freeze, end_t, bomb, tr)
                    if ph not in ("opening", "execute", "post_plant"):
                        continue
                    alive = [p for p in members["A"] if bool((_at(pts.get(p, {}).get("points", []), tk) or {}).get("alive"))]
                    if len(alive) < 2:
                        continue
                    # 任一存活成员仍在出生点 → 该采样不是有效战术区间距时刻（出生点不产出正/负间距证据）
                    if any(_norm((_at(pts.get(p, {}).get("points", []), tk) or {}).get("place")) in ("ctspawn", "tspawn", "spawn") for p in alive):
                        continue
                    allow = CLOSE_SPACING * (0.85 if len(alive) == 2 else 1.0 if len(alive) == 3 else 1.15 if len(alive) == 4 else 1.30)
                    total = len(alive) * (len(alive) - 1) // 2
                    cram = 0
                    for i in range(len(alive)):
                        for j in range(i + 1, len(alive)):
                            a = _at(pts.get(alive[i], {}).get("points", []), tk); b = _at(pts.get(alive[j], {}).get("points", []), tk)
                            if a and b and _dist(a.get("x"), a.get("y"), b.get("x"), b.get("y")) < allow:
                                cram += 1
                    cf = cram / total if total else 0.0
                    rec = {"tk": tk, "cf": round(cf, 3), "alive": alive, "phase": ph}
                    if best_cram is None or cf > best_cram["cf"]:
                        best_cram = rec
                    if best_disc is None or cf < best_disc["cf"]:
                        best_disc = rec
                chosen = []
                seen_tk = set()
                for cand in (best_cram, best_disc):
                    if cand is None or cand["tk"] in seen_tk:
                        continue
                    seen_tk.add(cand["tk"]); chosen.append(cand)
                for rec in chosen[:2]:
                    tk = rec["tk"]; alive = rec["alive"]; ph2 = rec["phase"]
                    allow = CLOSE_SPACING * (0.85 if len(alive) == 2 else 1.0 if len(alive) == 3 else 1.15 if len(alive) == 4 else 1.30)
                    total = len(alive) * (len(alive) - 1) // 2
                    cram = 0
                    for i in range(len(alive)):
                        for j in range(i + 1, len(alive)):
                            a = _at(pts.get(alive[i], {}).get("points", []), tk); b = _at(pts.get(alive[j], {}).get("points", []), tk)
                            if a and b and _dist(a.get("x"), a.get("y"), b.get("x"), b.get("y")) < allow:
                                cram += 1
                    ep = f"spc_{demo_id}_{r}_{tk}"
                    samples.append({"ep_id": ep, "branch": "spacing", "demo": demo_id, "map": map_name, "round": r,
                                    "sec": _sec(tk, freeze, tr), "region": translate_place(map_name, (_at(pts.get(alive[0], {}).get("points", []), tk) or {}).get("place")),
                                    "alive_team": "A", "alive_count": len(alive), "phase": ph2,
                                    "cram_frac": round(cram / total if total else 0.0, 3), "allow_dist": round(allow, 1),
                                    "pair_positions": [{"pid": p, "x": (_at(pts.get(p, {}).get("points", []), tk) or {}).get("x"),
                                                        "y": (_at(pts.get(p, {}).get("points", []), tk) or {}).get("y"),
                                                        "place": (_at(pts.get(p, {}).get("points", []), tk) or {}).get("place")} for p in alive]})
                    cur_branch_n[("spacing", demo_id)] = cur_branch_n.get(("spacing", demo_id), 0) + 1
                    t_agg = eng_agg.get("spacing", {}).get("A")
                    if t_agg: print(f"spacing {demo_id} R{r}: cram_frac={rec['cf']} trust={t_agg['trust']}", flush=True)

            # ---- utility_assisted_entry ----
            if props and _branch_ok("utility_assisted_entry", demo_id):
                pr = props[0]
                thrower = pr.get("thrower_player_id"); det = pr.get("detonate_tick"); ptype = pr.get("type")
                if thrower is not None and det is not None:
                    t = team_of.get(thrower)
                    validated = None
                    if ptype == "flash" and any(b.get("attacker_player_id") == thrower and abs(int(b.get("tick") or 0) - det) <= UTILITY_WINDOW_SEC * tr for b in blinds):
                        validated = "player_blind"
                    elif ptype == "he" and any(hd.get("attacker_player_id") == thrower and abs(int(hd.get("tick") or 0) - det) <= UTILITY_WINDOW_SEC * tr for hd in he_damage):
                        validated = "player_hurt"
                    elif ptype in ("smoke", "fire") and pr.get("active_start") is not None and pr.get("active_end") is not None:
                        validated = "temporal_block_candidate"
                    ws = pr.get("active_start") or det; we = pr.get("active_end") or det
                    hit = None
                    for k in round_kills:
                        actor = id_of.get(k["actor_steam"]); kt = int(k["tick"])
                        if actor is not None and team_of.get(actor) == t and actor != thrower:
                            if ws <= kt <= we + UTILITY_WINDOW_SEC * tr:
                                hit = k["actor_name"]; break
                    ep = f"util_{demo_id}_{r}_{det}"
                    samples.append({"ep_id": ep, "branch": "utility_assisted_entry", "demo": demo_id, "map": map_name, "round": r,
                                    "sec": _sec(det, freeze, tr), "region": translate_place(map_name, (_at(pts.get(thrower, {}).get("points", []), det) or {}).get("place")),
                                    "prop_type": ptype, "thrower_team": t, "real_effect": validated,
                                    "active_start": pr.get("active_start"), "active_end": pr.get("active_end"),
                                    "beneficiary_alive": any(p != thrower and bool((_at(pts.get(p, {}).get("points", []), ws or det) or {}).get("alive")) for p in members[t]),
                                    "entry_kill_in_window": hit})
                    cur_branch_n[("utility_assisted_entry", demo_id)] = cur_branch_n.get(("utility_assisted_entry", demo_id), 0) + 1
                    t_agg = eng_agg.get("utility_assisted_entry", {}).get(t or "A")
                    if t_agg: print(f"utility {demo_id} R{r}: opp={t_agg['opportunities']} conv={t_agg['converted']} trust={t_agg['trust']}", flush=True)

            # ---- isolated_death ----
            if round_kills and _branch_ok("isolated_death", demo_id):
                k = round_kills[0]
                vpid = id_of.get(k["victim_steam"]); vteam = team_of.get(vpid) if vpid is not None else None
                if vpid is not None and vteam:
                    dt = int(k["tick"]); ph = _phase(dt, start_t, freeze, end_t, bomb, tr)
                    alive_tm = [p for p in members[vteam] if p != vpid and bool((_at(pts.get(p, {}).get("points", []), dt) or {}).get("alive"))]
                    nearest = None
                    for p in alive_tm:
                        a = _at(pts.get(p, {}).get("points", []), dt)
                        if a and a.get("x") is not None and a.get("y") is not None and k.get("victim_x") is not None and k.get("victim_y") is not None:
                            d = _dist(k["victim_x"], k["victim_y"], a["x"], a["y"])
                            nearest = d if nearest is None or d < nearest else nearest
                    ep = f"iso_{demo_id}_{r}_{dt}"
                    samples.append({"ep_id": ep, "branch": "isolated_death", "demo": demo_id, "map": map_name, "round": r,
                                    "sec": _sec(dt, freeze, tr), "region": translate_place(map_name, (_at(pts.get(vpid, {}).get("points", []), dt) or {}).get("place")),
                                    "victim": k["victim_name"], "victim_team": vteam, "phase_at_death": ph,
                                    "nearest_alive_teammate_dist": nearest, "alive_teammates_count": len(alive_tm),
                                    "followup": [{"tick": k2["tick"], "actor": k2["actor_name"], "victim": k2["victim_name"],
                                                  "actor_team": team_of.get(id_of.get(k2["actor_steam"]))}
                                                 for k2 in round_kills if dt < k2["tick"] <= dt + TRADE_WINDOW_SEC * tr]})
                    cur_branch_n[("isolated_death", demo_id)] = cur_branch_n.get(("isolated_death", demo_id), 0) + 1
                    t_agg = eng_agg.get("isolated_death", {}).get(vteam)
                    if t_agg: print(f"isolated {demo_id} R{r}: opp={t_agg['opportunities']} conv={t_agg['converted']} trust={t_agg['trust']}", flush=True)

            # ---- crossfire_candidate（hidden；无 LoS，只给候选）----
            if round_kills and _branch_ok("crossfire_candidate", demo_id):
                k = round_kills[0]
                # 交战点 = 击杀者位置；我方朝向该点且夹角足够
                contact = (k.get("actor_x"), k.get("actor_y"), int(k["tick"]))
                cx, cy, ct = contact
                best = None
                for t in ("A", "B"):
                    for a_pid, b_pid in itertools_comb(members[t]):
                        a = _at(pts.get(a_pid, {}).get("points", []), ct); b = _at(pts.get(b_pid, {}).get("points", []), ct)
                        if a and b and a.get("yaw") is not None and b.get("yaw") is not None and cx is not None:
                            if _dist(a["x"], a["y"], cx, cy) <= 1400 and _dist(b["x"], b["y"], cx, cy) <= 1400:
                                ang_a = math.degrees(math.atan2(cy - a["y"], cx - a["x"]))
                                ang_b = math.degrees(math.atan2(cy - b["y"], cx - b["x"]))
                                d = abs((ang_a - ang_b) % 360); d = min(d, 360 - d)
                                if d >= 40 and (best is None or d > best[2]):
                                    best = (a_pid, b_pid, round(d, 1))
                    if best:
                        break
                ep = f"cf_{demo_id}_{r}_{ct}"
                samples.append({"ep_id": ep, "branch": "crossfire_candidate", "demo": demo_id, "map": map_name, "round": r,
                                "sec": _sec(ct, freeze, tr), "region": translate_place(map_name, (_at(pts.get(k.get('victim_steam') and id_of.get(k['victim_steam']), {}).get("points", []), ct) or {}).get("place")),
                                "contact_point": {"x": cx, "y": cy, "tick": ct},
                                "two_friend_yaw_to_contact_geom": best, "has_los_navmesh": False,
                                "note": "无 LoS/navmesh，只能 hidden 候选"})
                cur_branch_n[("crossfire_candidate", demo_id)] = cur_branch_n.get(("crossfire_candidate", demo_id), 0) + 1
                t_agg = eng_agg.get("crossfire_candidate", {})
                trust_any = next((m["trust"] for m in t_agg.values()), None)
                if trust_any: print(f"crossfire {demo_id} R{r}: trust={trust_any}", flush=True)

            # ---- area_control（区域状态分支）：扫描多个区域/回合以覆盖 occupied/contested/influence/controlled/unknown ----
            if regions and _branch_ok("area_control", demo_id):
                found_states = set()
                # 先扫所有回合/区域，收集不同状态（controlled/occupied/contested/influence/unknown）
                for rnd2 in art["rounds"]:
                    if found_states and len(found_states) >= 5:
                        break
                    r2 = rnd2["round"]
                    pts2 = {int(p): pd for p, pd in rnd2.get("players", {}).items()}
                    mem2 = {"A": [p for p in pts2 if team_of.get(p) == "A"], "B": [p for p in pts2 if team_of.get(p) == "B"]}
                    props2 = rnd2.get("props", []); blinds2 = rnd2.get("blinds", [])
                    fr2 = rnd2.get("freeze_end_tick") or rnd2.get("start_tick")
                    for region in regions:
                        rk = region["place_key"]
                        occ_a = occ_b = 0
                        for pid in mem2["A"]:
                            occ_a += sum(1 for s in pts2.get(pid, {}).get("points", []) if s.get("alive") and _norm(s.get("place") or "") == rk)
                        for pid in mem2["B"]:
                            occ_b += sum(1 for s in pts2.get(pid, {}).get("points", []) if s.get("alive") and _norm(s.get("place") or "") == rk)
                        sig = False
                        for p in props2:
                            if p.get("type") in ("smoke", "fire") and p.get("active_start") is not None and p.get("active_end") is not None:
                                thrower = p.get("thrower_player_id"); det = p.get("detonate_tick")
                                if thrower is not None and det is not None:
                                    a = _at(pts2.get(thrower, {}).get("points", []), det)
                                    if a and _norm(a.get("place") or "") == rk:
                                        sig = True
                        # 用引擎状态机判定该区域状态（仅用于分层抽样挑选代表，不作为审阅/expected 依据）
                        per_team = area_state_by_ep.get(f"area_{demo_id}_{r2}_{rk}", {})
                        states = [(st, dur) for lst in per_team.values() for st, dur in lst]
                        has = {st for st, _ in states}
                        rs = ("contested" if "contested" in has else "controlled" if "controlled" in has
                              else "occupied" if "occupied" in has else "influence" if "influence" in has else "unknown")
                        if rs in found_states:
                            continue
                        if occ_a == 0 and occ_b == 0 and rs != "unknown":
                            continue
                        team = "A" if occ_a >= occ_b else "B"
                        # 【原始 per-tick 占位窗口】——供独立审阅者判定 tick 级重叠/持续时长（不含引擎判定）
                        occ_windows, signal_windows = _raw_area_windows(mem2, pts2, props2, rk, fr2, tr)
                        ep = f"area_{demo_id}_{r2}_{rk}_{team}"
                        # sec = 该区域首段占位起点（相对该回合 freeze_end 00:00）；无占位则 0
                        allw = occ_windows.get("A", []) + occ_windows.get("B", [])
                        area_sec = min((w["start_sec"] for w in allw), default=0.0)
                        samples.append({"ep_id": ep, "branch": "area_control", "demo": demo_id, "map": map_name, "round": r2,
                                        "sec": round(area_sec, 2), "region": region.get("place_cn"), "place_key": rk,
                                        "team_occupancy": {"A": occ_a, "B": occ_b}, "has_signal": sig,
                                        "team_occupancy_windows": occ_windows, "signal_windows": signal_windows,
                                        "props_active": [{"type": p["type"], "thrower": p.get("thrower_player_id"),
                                                          "active_start": p.get("active_start"), "active_end": p.get("active_end"),
                                                          "authority_ok": p.get("authority") is not None}
                                                         for p in props2 if p.get("type") in ("smoke", "fire")],
                                        "geom": {"geometric": region.get("geometric"), "coverage": region.get("coverage")}})
                        found_states.add(rs)
                cur_branch_n[("area_control", demo_id)] = cur_branch_n.get(("area_control", demo_id), 0) + 1

    # 写审阅输入（纯原始事实；不含任何引擎字段；按 ep_id 去重避免重复）
    seen = set(); dedup = []
    for s in samples:
        if s["ep_id"] in seen:
            continue
        seen.add(s["ep_id"]); dedup.append(s)
    inp = {"schema": "raw-facts", "note": "纯原始事实，不含引擎判定。审阅者据此判定 expected。",
           "count": len(dedup), "samples": dedup}
    (ROOT / "docs" / "phase6r16-review-input.json").write_text(json.dumps(inp, ensure_ascii=False, indent=2), encoding="utf-8")
    # 写引擎 actual（供对比）
    eng_actual = {}
    for s in samples:
        if s["branch"] == "area_control":
            # 聚合该区域两队的有意义状态（含 contested/controlled/occupied/influence）
            rk = s["place_key"]
            per_team = area_state_by_ep.get(f"area_{s['demo']}_{s['round']}_{rk}", {})
            states = []
            for t, lst in per_team.items():
                for st, dur in lst:
                    states.append({"team": t, "state": st, "dur": round(dur, 2)})
            # 判定：含 contested -> contested；否则含 controlled -> controlled；否则含 occupied -> occupied；否则 influence/unknown
            has = {st["state"] for st in states}
            if "contested" in has:
                region_state = "contested"
            elif "controlled" in has:
                region_state = "controlled"
            elif "occupied" in has:
                region_state = "occupied"
            elif "influence" in has:
                region_state = "influence"
            else:
                region_state = "unknown"
            eng_actual[s["ep_id"]] = {"metric": "area_control", "state": region_state, "states": states}
        else:
            demo = s["demo"]
            t = None
            for key in ("victim_team", "thrower_team", "alive_team"):
                if s.get(key):
                    t = s[key]
                    break
            agg = engine_metrics.get(demo, {}).get(s["branch"], {})
            t = t or "A"
            m = agg.get(t)
            eng_actual[s["ep_id"]] = {"metric": s["branch"], "trust": (m.get("trust") if m else None),
                                      "opp": (m.get("opportunities") if m else None),
                                      "conv": (m.get("converted") if m else None),
                                      "rate": (m.get("rate") if m else None),
                                      "applicable": (m.get("applicable") if m else None)}
    (ROOT / "docs" / "phase6r16-engine-actual.json").write_text(
        json.dumps({"schema": "engine-actual", "note": "引擎在该 episode 上的实际输出，仅供 expected 对比", "actual": eng_actual},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    print("samples:", len(samples), "branches:", dict(Counter(s["branch"] for s in samples)))
    print("maps:", sorted({s["map"] for s in samples}))
    print("engine actual entries:", len(eng_actual))


def itertools_comb(xs):
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            yield xs[i], xs[j]


if __name__ == "__main__":
    main()
