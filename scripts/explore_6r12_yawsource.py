"""6R12 探针：隔离"yaw 来源"对门禁的影响。
- 同一"真值方向"（用精确 fire-tick 攻击者位置 + victim 位置，候选 G 世界变换）。
- 对比三种 yaw 来源的角误差：
   (a) exact fire-tick 采样（parse_ticks(ticks=fire_ticks)）—— 权威；
   (b) 全量 16-tick 序列 + 在 fire tick 处线性插值 —— 前端回放当前做法；
   (c) 全量 16-tick 序列 + 取最近样本（不插值）。
用法: python scripts/explore_6r12_yawsource.py <demo_path...>
"""
from __future__ import annotations

import bisect
import math
import sys
from pathlib import Path

import numpy as np
from demoparser2 import DemoParser

FIRES = {"ak47", "m4a1", "m4a1_silencer", "awp", "galilar", "famas", "mp9", "mp7",
         "mp5sd", "mac10", "p90", "ump45", "fiveseven", "glock", "usp_silencer",
         "deagle", "p250", "tec9", "ssg08", "aug", "sg556", "nova", "xm1014",
         "mag7", "sawedoff", "p2000", "elite", "g3sg1", "scar20", "m249", "negev"}
STEP = 16


def is_firearm(w):
    return any(w.startswith(b) for b in FIRES)


def _playout(demo):
    import struct
    try:
        with open(demo, "rb") as f:
            head = f.read(12)
            if head[:8] != b"PBDEMS2\x00":
                return 0
            off = struct.unpack("<I", head[8:12])[0]
            f.seek(off); data = f.read(1 << 20)
        def vv(b, i):
            r = 0; s = 0
            while i < len(b):
                x = b[i]; i += 1; r |= (x & 0x7F) << s
                if not (x & 0x80): return r, i
                s += 7
                if s > 63: break
            return r, i
        _c, i = vv(data, 0); _t, i = vv(data, i); size, i = vv(data, i)
        payload = data[i:i + size] if i + size <= len(data) else b""
        j = 0; val = 0
        while j < len(payload):
            key, j = vv(payload, j); fid = key >> 3; wire = key & 7
            if wire == 0:
                v, j = vv(payload, j)
                if fid == 2: val = int(v)
            elif wire == 5:
                if j + 4 <= len(payload): j += 4
            elif wire == 2:
                ln, j = vv(payload, j); j += ln
            else:
                break
        return val
    except Exception:
        return 0


def deg_err(a, b):
    d = (a - b) % 360
    return min(d, 360 - d)


def lerp(a, b, f):
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    d = b - a
    while d > 180:
        d -= 360
    while d < -180:
        d += 360
    return a + d * f


def analyze(demo):
    p = DemoParser(demo)
    label = Path(demo).name
    playout = _playout(demo)

    # 16-tick 全量序列（按 steamid）
    tt = p.parse_ticks(["CCSPlayerPawn.m_angEyeAngles", "X", "Y", "steamid", "tick"],
                       ticks=range(0, playout + 1, STEP))
    by_player: dict[str, list[dict]] = {}
    for r in tt.to_dict(orient="records"):
        sid = str(r["steamid"])
        eye = r.get("CCSPlayerPawn.m_angEyeAngles")
        yaw = eye[1] if isinstance(eye, (list, tuple)) and len(eye) >= 2 else None
        by_player.setdefault(sid, []).append({"tick": int(r["tick"]), "x": r.get("X"), "y": r.get("Y"), "yaw": yaw})
    for sid in by_player:
        by_player[sid].sort(key=lambda d: d["tick"])

    def state(sid, tick):
        pts = by_player.get(sid, [])
        if not pts:
            return None, None
        if tick <= pts[0]["tick"]:
            return pts[0]["x"], pts[0]["y"], pts[0]["yaw"]
        if tick >= pts[-1]["tick"]:
            return pts[-1]["x"], pts[-1]["y"], pts[-1]["yaw"]
        idx = bisect.bisect_right([d["tick"] for d in pts], tick) - 1
        a = pts[idx]
        b = pts[min(idx + 1, len(pts) - 1)]
        if a["tick"] == b["tick"]:
            return a["x"], a["y"], a["yaw"]
        f = (tick - a["tick"]) / (b["tick"] - a["tick"])
        return (a["x"] + (b["x"] - a["x"]) * f, a["y"] + (b["y"] - a["y"]) * f,
                lerp(a["yaw"], b["yaw"], f))

    deaths = p.parse_event("player_death", player=["X", "Y"],
                           other=["attacker_steamid", "attacker_X", "attacker_Y", "user_steamid",
                                  "weapon", "tick", "penetrated", "thrusmoke"])
    wf_by = {}
    for r in p.parse_event("weapon_fire").to_dict(orient="records"):
        wf_by.setdefault((str(r.get("user_steamid")), str(r.get("weapon"))), []).append(int(r["tick"]))
    for k in wf_by:
        wf_by[k].sort()

    # exact fire-tick parse: 收集所有 fire tick
    fire_tick_of = []
    for d in deaths.to_dict(orient="records"):
        w = str(d.get("weapon") or "")
        if not is_firearm(w):
            continue
        att = str(d.get("attacker_steamid") or "")
        vic = str(d.get("user_steamid") or "")
        if not att or att == vic:
            continue
        if bool(d.get("penetrated")) or bool(d.get("thrusmoke")):
            continue
        if d.get("attacker_X") is None or d.get("user_X") is None:
            continue
        dt = int(d["tick"])
        ticks = wf_by.get((att, w), [])
        i = bisect.bisect_right(ticks, dt) - 1
        fire = ticks[i] if (i >= 0 and dt - ticks[i] <= 64) else dt
        fire_tick_of.append((d, att, w, fire))

    fire_unique = sorted({r[3] for r in fire_tick_of})
    exact_map = {}
    if fire_unique:
        te = p.parse_ticks(["CCSPlayerPawn.m_angEyeAngles", "X", "Y", "steamid", "tick"], ticks=fire_unique)
        exact_map = {(int(r["tick"]), str(r["steamid"])): r for r in te.to_dict(orient="records")}

    # 真值方向：用精确 fire-tick 攻击者位置 + victim 位置
    exact_err, interp_err, nearest_err = [], [], []
    for (d, att, w, fire) in fire_tick_of:
        er = exact_map.get((fire, att))
        if er is None:
            continue
        eye = er.get("CCSPlayerPawn.m_angEyeAngles")
        if not isinstance(eye, (list, tuple)) or len(eye) < 2:
            continue
        yaw_exact = eye[1]
        ax, ay = er.get("X"), er.get("Y")
        if ax is None or ay is None:
            ax, ay = d.get("attacker_X"), d.get("attacker_Y")
        true_angle = math.degrees(math.atan2(d["user_Y"] - ay, d["user_X"] - ax))
        # exact
        pr = math.degrees(math.atan2(math.sin(math.radians(yaw_exact)), math.cos(math.radians(yaw_exact))))
        exact_err.append(deg_err(pr, true_angle))
        # interp 于 fire tick
        ix, iy, iyaw = state(att, fire)
        if iyaw is not None:
            pr2 = math.degrees(math.atan2(math.sin(math.radians(iyaw)), math.cos(math.radians(iyaw))))
            interp_err.append(deg_err(pr2, true_angle))
        # nearest sample yaw
        pts = by_player.get(att, [])
        near = min(pts, key=lambda q: abs(q["tick"] - fire))
        if near and near["yaw"] is not None:
            pr3 = math.degrees(math.atan2(math.sin(math.radians(near["yaw"])), math.cos(math.radians(near["yaw"]))))
            nearest_err.append(deg_err(pr3, true_angle))

    return label, np.array(exact_err), np.array(interp_err), np.array(nearest_err)


def main(demos):
    E, I, N = [], [], []
    for demo in demos:
        lab, e, i, n = analyze(demo)
        E.append(e); I.append(i); N.append(n)
        print(f"{lab}: exact N={len(e)} med={np.median(e) if len(e) else float('nan'):.2f} "
              f"p95={np.percentile(e,95) if len(e) else float('nan'):.2f} | "
              f"interp16 N={len(i)} med={np.median(i) if len(i) else float('nan'):.2f} "
              f"p95={np.percentile(i,95) if len(i) else float('nan'):.2f} | "
              f"nearest16 N={len(n)} med={np.median(n) if len(n) else float('nan'):.2f}")
    E, I, N = np.concatenate(E), np.concatenate(I), np.concatenate(N)
    print("\n==== 汇总（候选 G 世界变换；真值方向同源）====")
    print(f"exact   : N={len(E)} med={np.median(E):.3f} p95={np.percentile(E,95):.3f} frac<=10={np.mean(E<=10):.3f}")
    print(f"interp16: N={len(I)} med={np.median(I):.3f} p95={np.percentile(I,95):.3f} frac<=10={np.mean(I<=10):.3f}")
    print(f"nearest : N={len(N)} med={np.median(N):.3f} p95={np.percentile(N,95):.3f} frac<=10={np.mean(N<=10):.3f}")


if __name__ == "__main__":
    main(sys.argv[1:])
