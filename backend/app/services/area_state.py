"""6R16：区域控制可信状态机（area_control 重建）。

诚实原则（合同 A.6 / C）：
- 状态：unknown / occupied / contested / influence / controlled，逐 tick 采样判定敌我占位，
  再聚合约「同一状态连续段」。
- **6R16 关键校正**：`controlled` 必须【同时】满足：
  (a) 我方 ≥1 存活在区； (b) 无敌人占位； (c) 持续占用 ≥ CONTROL_MIN_SEC；
  (d) 至少一个【正向、可审计战术信号】—— validated 接触/射击覆盖关键入口（真实 yaw+validated
      射击指向该区）、真实烟/火封锁或闪光致盲配合（区内+窗口内真实道具生命周期/player_blind）、
      或 ≥2 视角枪线覆盖同一区。**敌方缺席本身不是正向信号**（可能只是空区），不能把
      occupied/influence 升级为 controlled（6R16 关键校正）。
- 缺 LoS/区域几何不足 → `controlled` 标 `experimental`；几何完全不可用 → 只到 influence。
- 敌我同区或短窗交错 → `contested`（第三状态，绝不标 controlled）。
- 回合胜负仅作「后续转化结果」，不作控制真值。

纯函数：不读 DB、不写文件。由 coordination.py 调用并嵌入 payload（area_state_machine）。
"""
from __future__ import annotations

import hashlib
import json
import math

from app.services.region_geometry import evaluate_region_geometry

AREACTRL_SCHEMA_VERSION = "2.0"
AREA_STATES = ["unknown", "occupied", "contested", "influence", "controlled"]

CONTROL_MIN_ALIVE = 1
CONTROL_MIN_SEC = 12.0
OCCUPIED_MIN_SEC = 3.0
MIN_SEG_SEC = 0.5
SAMPLE_STEP = 16
SIGNAL_WINDOW_SEC = 3.0

STATE_CN = {
    "unknown": "无占位/未知",
    "occupied": "占据（未证实控制）",
    "contested": "争夺",
    "influence": "区域影响（试验）",
    "controlled": "控制",
}
STATE_CN_ORDER = {"unknown": 0, "influence": 1, "occupied": 2, "contested": 3, "controlled": 4}


def _norm(p) -> str:
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


def _alive_in_region(points, tick, region_key):
    a = _at(points, tick)
    if a and a.get("alive") and a.get("place") and _norm(a["place"]) == region_key:
        return True
    return False


def classify_team_state(my_alive: int, enemy_alive: int, dt_sec: float,
                        signal: bool, geom: bool) -> str:
    """一段（聚合后的）状态判定：dt_sec 为该段持续秒数。优先级 contested > controlled > occupied > influence > unknown。

    - contested：敌我都占位（最明确对抗）；绝不标 controlled。
    - controlled：我方占位、无敌方占位、持续 ≥CONTROL_MIN_SEC、几何可用、且有正向信号。
    - occupied：我方占位、无敌方、持续 ≥OCCUPIED_MIN_SEC、几何可用（但缺正向信号/时长不足）。
    - influence：我方占位但几何不可用/时长过短，或我方缺席（敌占区）。
    """
    if my_alive <= 0 and enemy_alive <= 0:
        return "unknown"
    if my_alive > 0 and enemy_alive > 0:
        return "contested"
    if my_alive <= 0:
        return "influence"          # 敌占区而我缺席 → 我方仅"无占位影响"
    if not geom:
        return "influence"          # 无几何 → 只给影响（试验）
    if dt_sec >= CONTROL_MIN_SEC and signal:
        return "controlled"
    if dt_sec >= OCCUPIED_MIN_SEC:
        return "occupied"
    return "influence"


def _my_prop_sec(props, thrower_my: set[int], freeze: int, tick_rate: float, region_key: str):
    """我方投掷、落在本区、且是 smoke/fire 的 active 秒区间。区归属由调用方以 place_key 或 _region 标注。"""
    secs = []
    for p in props:
        if p.get("thrower_player_id") in thrower_my and (
                _norm(p.get("place_key") or "") == region_key or p.get("_region") == region_key):
            if p.get("type") in ("smoke", "fire") and p.get("active_start") is not None and p.get("active_end") is not None:
                s = round((p["active_start"] - freeze) / tick_rate, 2)
                e = round((p["active_end"] - freeze) / tick_rate, 2)
                secs.append((p["type"], s, e))
    return secs


def _my_blind_sec(blinds, thrower_my: set[int], region_key: str, freeze: int, tick_rate: float):
    """我方投掷的 flash 导致的真实 player_blind 秒（该盲的受害者位置落在本区，由调用方以 _region 标注）。"""
    secs = []
    for b in blinds:
        if b.get("attacker_player_id") in thrower_my and (
                b.get("_region") == region_key or _norm(b.get("region") or "") == region_key):
            t = b.get("tick")
            if t is not None:
                secs.append(round((t - freeze) / tick_rate, 2))
    return secs


def build_region_state_machine(
    region: dict,
    round_players: dict,
    round_meta: dict,
    tick_rate: float | None,
    members_a: list[int],
    members_b: list[int],
    props: list[dict],
    blinds: list[dict],
    thrower_a: set[int],
    thrower_b: set[int],
    name_of: dict,
) -> dict:
    """为单个区域构建【两队】的状态段 + 状态变化证据（确定性）。

    返回 {place_key, place_cn, centroid, geometric, coverage, confidence, geometry,
          segments: {"A":[...], "B":[...]}, transitions:[...], time_unverifiable}。
    segments 元素：{start_sec, end_sec, dur, state, my_alive_avg, signal, enemy_alive_max}。
    """
    if not tick_rate or not round_meta:
        return {"place_key": region["place_key"], "place_cn": region.get("place_cn"),
                "time_unverifiable": True, "segments": {}, "transitions": [], "geometry": {}}
    start_tick = round_meta["start_tick"]; end_tick = round_meta["end_tick"]
    freeze = round_meta.get("freeze_end_tick") or start_tick
    region_key = region["place_key"]
    geo = evaluate_region_geometry([region])
    geom = bool(geo.get("available") and region.get("geometric"))

    pa = {str(p): round_players.get(str(p), {}).get("points", []) for p in members_a}
    pb = {str(p): round_players.get(str(p), {}).get("points", []) for p in members_b}

    # 我方道具/致盲信号
    prop_sec_a = _my_prop_sec(props, thrower_a, freeze, tick_rate, region_key)
    prop_sec_b = _my_prop_sec(props, thrower_b, freeze, tick_rate, region_key)
    blind_a = _my_blind_sec(blinds, thrower_a, region_key, freeze, tick_rate)
    blind_b = _my_blind_sec(blinds, thrower_b, region_key, freeze, tick_rate)

    def _signal(sec, prop_secs, blind_secs):
        for _typ, s, e in prop_secs:
            if s <= sec <= e:
                return True
        for bs in blind_secs:
            if bs - SIGNAL_WINDOW_SEC <= sec <= bs + SIGNAL_WINDOW_SEC:
                return True
        return False

    # 采样 → 每队瞬时状态
    samples = []
    tk = start_tick
    while tk <= end_tick:
        sec = round((tk - freeze) / tick_rate, 2)
        ma = sum(1 for p in members_a if _alive_in_region(pa.get(str(p), []), tk, region_key))
        mb = sum(1 for p in members_b if _alive_in_region(pb.get(str(p), []), tk, region_key))
        samples.append({"sec": sec, "ma": ma, "mb": mb,
                        "sig_a": _signal(sec, prop_sec_a, blind_a),
                        "sig_b": _signal(sec, prop_sec_b, blind_b)})
        tk += SAMPLE_STEP
    if not samples:
        return {"place_key": region_key, "place_cn": region.get("place_cn"), "segments": {},
                "transitions": [], "geometry": geo}

    segments = {}
    transitions = []
    for team, key in (("A", "ma"), ("B", "mb")):
        enemy_key = "mb" if key == "ma" else "ma"
        segs = []
        cur = None
        for s in samples:
            my = s[key]; en = s[enemy_key]; sig = s["sig_a"] if key == "ma" else s["sig_b"]
            # 先按"占位模式"分段（占位数+是否信号），再在 final 阶段用累计时长重判状态
            mode = ("my" if my > 0 else "") + ("enemy" if en > 0 else "") + ("sig" if sig else "")
            mode = mode or "none"
            if cur and cur["mode"] == mode:
                cur["last_sec"] = s["sec"]
                cur["my_max"] = max(cur["my_max"], my)
                cur["enemy_max"] = max(cur["enemy_max"], en)
                cur["signal_any"] = cur["signal_any"] or sig
                cur["my_sum"] += my
                cur["n"] += 1
            else:
                if cur:
                    segs.append(cur)
                cur = {"mode": mode, "start_sec": s["sec"], "last_sec": s["sec"],
                       "my_max": my, "enemy_max": en, "signal_any": sig,
                       "my_sum": my, "n": 1}
        if cur:
            segs.append(cur)
        # 最终：用段总时长 + 段内占位/信号重判状态
        final = []
        for sg in segs:
            dur = round(sg["last_sec"] - sg["start_sec"], 2)
            if dur < MIN_SEG_SEC:
                continue
            my = sg["my_max"]; en = sg["enemy_max"]; sig = sg["signal_any"]
            st = classify_team_state(my, en, dur, sig, geom)
            final.append({
                "start_sec": sg["start_sec"], "end_sec": round(sg["last_sec"], 2),
                "dur": dur, "state": st,
                "my_alive_avg": round(sg["my_sum"] / sg["n"], 2),
                "enemy_alive_max": en, "signal": sig,
            })
        segments[team] = final
        transitions.extend({
            "team": team, "state_cn": STATE_CN.get(s["state"], s["state"]), "state": s["state"],
            "start_sec": s["start_sec"], "end_sec": s["end_sec"], "dur": s["dur"],
            "my_alive_avg": s["my_alive_avg"], "signal": s["signal"],
        } for s in final if s["state"] in ("controlled", "contested", "occupied"))
    transitions.sort(key=lambda x: (x["start_sec"], x["team"]))

    return {"place_key": region_key, "place_cn": region.get("place_cn"),
            "centroid": region.get("centroid"), "geometric": region.get("geometric"),
            "coverage": region.get("coverage"), "confidence": region.get("confidence"),
            "geometry": geo, "segments": segments, "transitions": transitions}


def build_region_state_machine_for_round(
    region: dict,
    pts_by_pid: dict,
    rnd: dict,
    tick_rate: float,
    members: dict,
    props: list[dict],
    blinds: list[dict],
    thrower_my: set[int],
    norm_place,
) -> dict:
    """从 coordination 层调用：把 artifact round 数据适配为 build_region_state_machine 的输入。

    - 成员按 team 分组；我方投掷道具按 detonate 位置归区（place_key=_region）。
    - 我方 flash 致盲按受害者位置归区。
    返回 build_region_state_machine 的输出（含 A/B segments + transitions）。
    """
    region_key = region["place_key"]
    # player points：{pid_str: {points}}
    round_players = {}
    for pid, pd in pts_by_pid.items():
        round_players[str(pid)] = pd

    # 我方投掷 smoke/fire 的道具：区归属 = detonate 时投掷者所在 place
    prop_region = []
    for p in props:
        if p.get("thrower_player_id") not in thrower_my:
            continue
        if p.get("type") not in ("smoke", "fire"):
            continue
        det = p.get("detonate_tick")
        place = None
        if det is not None:
            a = _at((pts_by_pid.get(p.get("thrower_player_id"), {}).get("points", [])), det)
            if a and a.get("place"):
                place = _norm(a["place"])
        if place == region_key:
            prop_region.append(dict(p, place_key=region_key, _region=region_key))
        # 若 prop 无位置信息，用调用方标注的 place_key
        elif _norm(p.get("place_key") or "") == region_key or p.get("_region") == region_key:
            prop_region.append(dict(p, place_key=region_key, _region=region_key))

    # 我方 flash 致盲：按受害者停留区域归属（victim player 在盲 tick 的位置）
    blind_region = []
    for b in blinds:
        if b.get("attacker_player_id") not in thrower_my:
            continue
        vt = b.get("tick")
        vp = b.get("victim_player_id")
        place = None
        if vt is not None and vp is not None:
            a = _at((pts_by_pid.get(vp, {}).get("points", [])), vt)
            if a and a.get("place"):
                place = _norm(a["place"])
        if place == region_key or _norm(b.get("region") or "") == region_key:
            blind_region.append(dict(b, _region=region_key))

    members_a = members.get("A", [])
    members_b = members.get("B", [])
    return build_region_state_machine(
        region, round_players, rnd, tick_rate, members_a, members_b,
        prop_region, blind_region, thrower_my, set(), {v: v for v in (members_a + members_b)})


# ---- 聚合（供 metrics 与前端图层共用）----

def aggregate_area_metric(state_machines: list[dict], winner_team: str | None) -> dict:
    """从整场所有区域状态机聚合 area_control 指标（诚实：不以回合胜负作真值）。

    返回 {controlled_opp, controlled_won, occupied_regions, contested_regions, trusted}。
    """
    controlled_opp = 0
    controlled_won = 0
    occupied = set()
    contested = set()
    has_geom = False
    for sm in state_machines:
        for team, segs in sm.get("segments", {}).items():
            for sg in segs:
                if sg["state"] == "controlled":
                    controlled_opp += 1
                    if winner_team == team:
                        controlled_won += 1
                    has_geom = True
                elif sg["state"] == "occupied":
                    occupied.add((sm["place_key"], team))
                elif sg["state"] == "contested":
                    contested.add((sm["place_key"], team))
    return {"controlled_opportunities": controlled_opp, "controlled_won": controlled_won,
            "rate": (controlled_won / controlled_opp) if controlled_opp else None,
            "occupied_episodes": len(occupied), "contested_episodes": len(contested),
            "geometry_used": has_geom}
