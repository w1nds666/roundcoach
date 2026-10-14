"""6R12 勘探：量化"16-tick 采样 + 插值"在击杀时刻的残余角误差（与精确 fire-tick 采样对比）。

只读。对每个直接枪械击杀：
  - 用 16-tick 采样序列在死亡 tick 处做 yaw 插值（前端 playerState 的方式）。
  - 候选 G 变换 → 与 attacker→victim 真值方向比较。
  - 同时给出"精确 fire-tick 采样"的对照误差。
用于判断：门禁是否必须要求击杀窗口补 exact/高密度采样。

用法: python scripts/explore_6r12_sampling.py <demo_path...>
"""
from __future__ import annotations

import bisect
import math
import sys
from pathlib import Path

import numpy as np
from demoparser2 import DemoParser

FIRE_BASES = {
    "ak47", "m4a1", "m4a1_silencer", "awp", "galilar", "famas", "mp9", "mp7",
    "mp5sd", "mac10", "p90", "ump45", "fiveseven", "glock", "usp_silencer",
    "deagle", "p250", "tec9", "ssg08", "aug", "sg556", "nova", "xm1014",
    "mag7", "sawedoff", "p2000", "elite", "g3sg1", "scar20", "m249", "negev",
}
STEP = 16


def is_firearm(w):
    return any(w.startswith(b) for b in FIRE_BASES)


def deg_err(a, b):
    d = (a - b) % 360
    return min(d, 360 - d)


def lerp_yaw(a, b, f):
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    diff = b - a
    while diff > 180:
        diff -= 360
    while diff < -180:
        diff += 360
    return a + diff * f


def analyze(demo):
    p = DemoParser(demo)
    label = Path(demo).name
    header = p.parse_header()
    playout = int(header.get("playout_ticks") or 0)
    # rounds
    starts = sorted(p.parse_event("round_start", other=["tick"]).to_dict(orient="records"), key=lambda r: r["tick"])
    freezes = sorted(p.parse_event("round_freeze_end", other=["tick"]).to_dict(orient="records"), key=lambda r: r["tick"])
    ends = sorted(p.parse_event("round_end", other=["tick", "winner"]).to_dict(orient="records"), key=lambda r: r["tick"])
    # 简化：直接用全量 16-tick 采样（playerState 以点 tick 为准，非回合作业窗）
    tt = p.parse_ticks(["CCSPlayerPawn.m_angEyeAngles", "X", "Y", "health", "steamid", "tick"],
                       ticks=range(0, playout + 1, STEP))
    series: dict[tuple[int, str], list[dict]] = {}
    for r in tt.to_dict(orient="records"):
        tick = int(r["tick"])
        sid = str(r["steamid"])
        eye = r.get("CCSPlayerPawn.m_angEyeAngles")
        yaw = eye[1] if isinstance(eye, (list, tuple)) and len(eye) >= 2 else None
        series.setdefault((tick // 1, sid), [])
    # 按 (player) 全局序列
    by_player: dict[str, list[dict]] = {}
    for r in tt.to_dict(orient="records"):
        sid = str(r["steamid"])
        tick = int(r["tick"])
        eye = r.get("CCSPlayerPawn.m_angEyeAngles")
        yaw = eye[1] if isinstance(eye, (list, tuple)) and len(eye) >= 2 else None
        by_player.setdefault(sid, []).append({"tick": tick, "x": r.get("X"), "y": r.get("Y"),
                                              "yaw": yaw, "alive": (r.get("health") or 0) > 0})
    for sid in by_player:
        by_player[sid].sort(key=lambda d: d["tick"])

    def state_at(sid, tick):
        pts = by_player.get(sid, [])
        if not pts:
            return None
        if tick <= pts[0]["tick"]:
            return pts[0]
        if tick >= pts[-1]["tick"]:
            return pts[-1]
        idx = bisect.bisect_right([d["tick"] for d in pts], tick) - 1
        a = pts[idx]
        b = pts[min(idx + 1, len(pts) - 1)]
        if a["tick"] == b["tick"]:
            return a
        f = (tick - a["tick"]) / (b["tick"] - a["tick"])
        return {"tick": tick, "x": a["x"] + (b["x"] - a["x"]) * f, "y": a["y"] + (b["y"] - a["y"]) * f,
                "yaw": lerp_yaw(a["yaw"], b["yaw"], f), "alive": a["alive"]}

    deaths = p.parse_event("player_death", player=["X", "Y"],
                           other=["attacker_steamid", "attacker_X", "attacker_Y", "user_steamid",
                                  "weapon", "tick", "penetrated", "thrusmoke"])
    wf_by = {}
    for r in p.parse_event("weapon_fire").to_dict(orient="records"):
        wf_by.setdefault((str(r.get("user_steamid")), str(r.get("weapon"))), []).append(int(r["tick"]))
    for k in wf_by:
        wf_by[k].sort()

    exact_err, interp_err, nearest_err = [], [], []
    for d in deaths.to_dict(orient="records"):
        weapon = str(d.get("weapon") or "")
        if not is_firearm(weapon):
            continue
        att = str(d.get("attacker_steamid") or "")
        vic = str(d.get("user_steamid") or "")
        if not att or att == vic:
            continue
        if bool(d.get("penetrated")) or bool(d.get("thrusmoke")):
            continue
        if d.get("attacker_X") is None or d.get("user_X") is None:
            continue
        death_tick = int(d["tick"])
        true_angle = math.degrees(math.atan2(d["user_Y"] - d["attacker_Y"], d["user_X"] - d["attacker_X"]))
        # 精确 fire tick
        ticks = wf_by.get((att, weapon), [])
        i = bisect.bisect_right(ticks, death_tick) - 1
        fire = ticks[i] if (i >= 0 and death_tick - ticks[i] <= 64) else death_tick
        ex = state_at(att, fire)
        if ex and ex["yaw"] is not None:
            fx, fy = math.cos(math.radians(ex["yaw"])), math.sin(math.radians(ex["yaw"]))
            pred = math.degrees(math.atan2(fy, fx))
            exact_err.append(deg_err(pred, true_angle))
        # 插值于死亡 tick
        iv = state_at(att, death_tick)
        if iv and iv["yaw"] is not None:
            fx, fy = math.cos(math.radians(iv["yaw"])), math.sin(math.radians(iv["yaw"]))
            pred = math.degrees(math.atan2(fy, fx))
            interp_err.append(deg_err(pred, true_angle))

    return label, np.array(exact_err), np.array(interp_err)


def main(demos):
    labs, ex_all, ip_all = [], [], []
    for demo in demos:
        label, ex, ip = analyze(demo)
        labs.append(label)
        ex_all.append(ex)
        ip_all.append(ip)
        print(f"{label}: exact N={len(ex)} median={np.median(ex) if len(ex) else float('nan'):.2f} "
              f"p95={np.percentile(ex,95) if len(ex) else float('nan'):.2f} | "
              f"interp16 N={len(ip)} median={np.median(ip) if len(ip) else float('nan'):.2f} "
              f"p95={np.percentile(ip,95) if len(ip) else float('nan'):.2f}")
    ex_all = np.concatenate(ex_all)
    ip_all = np.concatenate(ip_all)
    print(f"\n==== 汇总 ====")
    print(f"exact 采样:  N={len(ex_all)}  median={np.median(ex_all):.3f}  p95={np.percentile(ex_all,95):.3f}  "
          f"frac<=10={np.mean(ex_all<=10):.3f}")
    print(f"16tick 插值: N={len(ip_all)}  median={np.median(ip_all):.3f}  p95={np.percentile(ip_all,95):.3f}  "
          f"frac<=10={np.mean(ip_all<=10):.3f}  frac<=25={np.mean(ip_all<=25):.3f}")


if __name__ == "__main__":
    main(sys.argv[1:])
