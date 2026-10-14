"""6R8 跨 demo 字段稳定性核验：证明 yaw/pitch 与道具生命周期字段在多张真实地图上稳定。

用法: python scripts/explore_6r8_crosscheck.py <demo_path...>
输出紧凑核验表（只读）。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from demoparser2 import DemoParser

KEYS = ["smokegrenade_detonate", "smokegrenade_expired", "hegrenade_detonate",
        "flashbang_detonate", "inferno_startburn", "inferno_expire", "decoy_started", "decoy_detonate"]

for demo in sys.argv[1:]:
    p = DemoParser(demo)
    print(f"\n### {Path(demo).name}")
    hdr = p.parse_header()
    print(f"  map={hdr.get('map_name')} server={hdr.get('server_name')}")

    # 1) eye angle present + structure
    tt = p.parse_ticks(["CCSPlayerPawn.m_angEyeAngles", "health", "tick"], ticks=list(range(5000, 5200, 16)))
    ea = tt["CCSPlayerPawn.m_angEyeAngles"].dropna()
    if len(ea):
        arr = np.array(ea.tolist())
        comp0 = arr[:, 0]  # pitch
        comp1 = arr[:, 1]  # yaw
        print(f"  eyeAngles n={len(arr)} comp0(pitch) range [{comp0.min():.1f},{comp0.max():.1f}] "
              f"comp1(yaw) range [{comp1.min():.1f},{comp1.max():.1f}] comp2(roll) max|.|={np.abs(arr[:,2]).max():.1f}")
    else:
        print("  eyeAngles: NO ROWS")

    # 2) prop lifecycle events present + stable field set
    for ev in KEYS:
        try:
            df = p.parse_event(ev)
            cols = sorted(df.columns)
            ok = cols == sorted(["entityid", "tick", "user_name", "user_steamid", "x", "y", "z"])
            print(f"  event {ev}: n={len(df)} cols_match_authority={ok}")
        except Exception as e:  # noqa: BLE001
            print(f"  event {ev}: ERROR {e}")

    # 3) grenade entity lifecycle summary（类型 → 实体数 / 有坐标实体数）
    g = p.parse_grenades()
    gb = g.groupby(["grenade_type", "grenade_entity_id"])
    by_type = defaultdict(lambda: [0, 0])
    for (t, _), grp in gb:
        by_type[t][0] += 1
        if grp[["x", "y", "z"]].notna().any(axis=1).any():
            by_type[t][1] += 1
    print("  grenade types (entities, with_coords):")
    for t, (n, wc) in sorted(by_type.items()):
        print(f"    {t}: {n} / {wc}")
