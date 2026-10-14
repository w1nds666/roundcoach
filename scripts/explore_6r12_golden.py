"""6R12 勘探：建立射击/击杀真值 golden set，并校验 yaw → 雷达方向候选变换。

只读勘探，不写库。三阶段：
  I. 分类：deaths → 枪械击杀 / 排除(手雷·近战·自杀·无攻击者) / 分离(穿墙 penetrated·烟后 thrusmoke)。
  II. 精确采样：对枪械击杀，用 weapon_fire 关联"开火 tick"（同攻击者+同武器+死亡前窗口内），
      在该 tick 采样攻击者 eyeAngles(yaw/pitch)+位置，得到"可信射击方向"。
  III. 门禁：对直接可见枪械击杀，算 attacker→victim 方向与各候选 yaw 变换的角误差；
      验证"无 90°/180° 系统偏差"并给出 median/p95/frac。

用法: python scripts/explore_6r12_golden.py <demo_path...>
"""
from __future__ import annotations

import bisect
import math
import sys
from pathlib import Path

import numpy as np
from demoparser2 import DemoParser

FIRE_WINDOW_TICKS = 64

# 枪械基础武器（CS2 raw weapon id，含 *_vip/_ace_vip 皮肤后缀）
FIREARM_BASES = {
    "ak47", "m4a1", "m4a1_silencer", "awp", "galilar", "famas", "mp9", "mp7",
    "mp5sd", "mac10", "p90", "ump45", "fiveseven", "glock", "usp_silencer",
    "deagle", "p250", "tec9", "ssg08", "aug", "sg556", "nova", "xm1014",
    "mag7", "sawedoff", "p2000", "elite", "g3sg1", "scar20", "m249", "negev",
}


def is_firearm(w: str) -> bool:
    return any(w.startswith(b) for b in FIREARM_BASES)


def candidates_yaw(yaw_deg: float) -> dict[str, tuple[float, float]]:
    r = math.radians(yaw_deg)
    s, c = math.sin(r), math.cos(r)
    return {
        "A(-sin,cos)": (-s, c), "B(sin,cos)": (s, c), "C(cos,-sin)": (c, -s),
        "D(-cos,sin)": (-c, s), "E(sin,-cos)": (s, -c), "F(-sin,-cos)": (-s, -c),
        "G(cos,sin)": (c, s), "H(-cos,-sin)": (-c, -s),
    }


def _deg_err(a, b):
    d = (a - b) % 360
    return min(d, 360 - d)


def analyze(demo: str):
    p = DemoParser(demo)
    label = Path(demo).name
    deaths = p.parse_event(
        "player_death", player=["X", "Y"],
        other=["attacker_steamid", "attacker_X", "attacker_Y", "user_steamid",
               "weapon", "headshot", "tick", "penetrated", "thrusmoke"],
    )
    wf = p.parse_event("weapon_fire")
    wf_by = {}
    for r in wf.to_dict(orient="records"):
        wf_by.setdefault((str(r.get("user_steamid")), str(r.get("weapon"))), []).append(int(r["tick"]))
    for k in wf_by:
        wf_by[k].sort()

    firearm, excluded, separated = [], [], []
    for d in deaths.to_dict(orient="records"):
        weapon = str(d.get("weapon") or "")
        if not is_firearm(weapon):
            excluded.append("grenade/knife")
            continue
        att = str(d.get("attacker_steamid") or "")
        vic = str(d.get("user_steamid") or "")
        if not att or att == vic:
            excluded.append("suicide/no-attacker")
            continue
        ay, ax = d.get("attacker_Y"), d.get("attacker_X")
        vy, vx = d.get("user_Y"), d.get("user_X")
        if ay is None or ax is None or vy is None or vx is None:
            excluded.append("missing-pos")
            continue
        if bool(d.get("penetrated")) or bool(d.get("thrusmoke")):
            separated.append(d)
        else:
            firearm.append(d)

    # 精确 fire tick
    fire_ticks = set()
    rows = []
    for d in firearm:
        att = str(d["attacker_steamid"])
        weapon = str(d["weapon"])
        death_tick = int(d["tick"])
        ticks = wf_by.get((att, weapon), [])
        i = bisect.bisect_right(ticks, death_tick) - 1
        fire = None
        if i >= 0 and death_tick - ticks[i] <= FIRE_WINDOW_TICKS:
            fire = ticks[i]
        fire = fire or death_tick
        fire_ticks.add(fire)
        rows.append((d, att, weapon, fire, death_tick))

    eye_rows = []
    if fire_ticks:
        tt = p.parse_ticks(["CCSPlayerPawn.m_angEyeAngles", "X", "Y", "steamid", "tick"],
                           ticks=sorted(fire_ticks))
        eye_rows = [(int(r["tick"]), str(r["steamid"])) for r in tt.to_dict(orient="records")]
        eye_map = {(int(r["tick"]), str(r["steamid"])): r for r in tt.to_dict(orient="records")}
    else:
        eye_map = {}

    entries = []  # (demo, weapon, yaw, pitch, true_angle, dist, headshot, penetrated, thrusmoke)
    for (d, att, weapon, fire, death_tick) in rows:
        rec = eye_map.get((fire, att))
        if rec is None:
            continue
        eye = rec.get("CCSPlayerPawn.m_angEyeAngles")
        if not isinstance(eye, (list, tuple)) or len(eye) < 2:
            continue
        pitch, yaw = eye[0], eye[1]
        fx, fy = rec.get("X"), rec.get("Y")
        if fx is None or fy is None:
            fx, fy = d.get("attacker_X"), d.get("attacker_Y")
        true_angle = math.degrees(math.atan2(d["user_Y"] - fy, d["user_X"] - fx))
        dist = math.hypot(d["user_X"] - fx, d["user_Y"] - fy)
        entries.append((label, weapon, yaw, pitch, true_angle, dist,
                        bool(d.get("headshot")), bool(d.get("penetrated")), bool(d.get("thrusmoke"))))
    return label, p.parse_header().get("map_name"), firearm, excluded, separated, entries


def main(demos):
    all_labels = []
    all_entries = []
    for demo in demos:
        label, map_name, firearm, excluded, separated, entries = analyze(demo)
        all_labels.append((label, map_name))
        all_entries.extend(entries)
        print(f"\n{'='*70}\n{label}  ({map_name})")
        print(f"  firearm={len(firearm)}  excluded={len(excluded)}  separated(wallbang/thrusmoke)={len(separated)}  "
              f"with-exact-eye={len(entries)}")

    if not all_entries:
        print("\nNO ENTRIES")
        return

    def _err_vec(entries, name):
        out = []
        for (label, weapon, yaw, pitch, true_angle, dist, hs, pen, smoke) in entries:
            fx, fy = candidates_yaw(yaw)[name]
            pred = math.degrees(math.atan2(fy, fx))
            out.append(_deg_err(pred, true_angle))
        return np.array(out)

    print(f"\n#### TOTAL（直接可见枪械击杀，精确 fire-tick 采样）= {len(all_entries)} ####")
    print("map breakdown:", {m: sum(1 for e in all_entries if e[0].startswith(m[:1]) and False) for m in [""]})
    for (label, mp) in all_labels:
        cnt = sum(1 for e in all_entries if e[0] == label)
        print(f"  {label} ({mp}): {cnt}")

    print("\n--- 候选变换角误差（全部直接枪械击杀）---")
    best = None
    for name in candidates_yaw(0):
        e = _err_vec(all_entries, name)
        print(f"  {name}: median={np.median(e):.2f} p95={np.percentile(e,95):.2f} "
              f"mean={e.mean():.2f} frac<10={np.mean(e<10):.3f} frac<25={np.mean(e<25):.3f}")
        if best is None or np.median(e) < best[1]:
            best = (name, np.median(e))

    # 门禁：G（正确候选）在直杀上的表现
    print("\n--- 门禁（候选 G: world forward=(cos yaw, sin yaw)）---")
    eG = _err_vec(all_entries, "G(cos,sin)")
    print(f"  N={len(eG)}  median={np.median(eG):.3f}°  p95={np.percentile(eG,95):.3f}°  "
          f"mean={eG.mean():.3f}°  max={eG.max():.3f}°")
    print(f"  frac<=10°={np.mean(eG<=10):.3f}  frac<=25°={np.mean(eG<=25):.3f}")
    n90 = np.mean((eG > 60) & (eG < 120))
    n180 = np.mean(eG > 150)
    print(f"  90°±30° 系统偏差比例={n90:.4f}   180° 系统偏差比例={n180:.4f}")

    # 分武器 top errors
    print("\n--- 角误差最大 12 条（供分类/异常识别）---")
    idx = np.argsort(eG)[-12:][::-1]
    for i in idx:
        la, weapon, yaw, pitch, ta, dist, hs, pen, smoke = all_entries[int(i)]
        print(f"  {la} {weapon!s:18s} yaw={yaw:7.1f} true={ta:7.1f} err={eG[int(i)]:6.1f} dist={dist:7.1f} headshot={hs}")


if __name__ == "__main__":
    main(sys.argv[1:])
