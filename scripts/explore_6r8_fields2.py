"""6R8 字段勘探（二）：确认 yaw/pitch 真实来源 + 道具生命周期事件字段。

用法: python scripts/explore_6r8_fields2.py <demo_path> <label>
只读勘探。
"""
from __future__ import annotations

import sys
from pathlib import Path

from demoparser2 import DemoParser

demo = sys.argv[1]
label = sys.argv[2]
p = DemoParser(demo)
print(f"\n##### {label} #####")

# --- 1. 验证 m_angEyeAngles 的真实来源/值 ---
print("\n=== [1] m_angEyeAngles & related (parse_ticks) ===")
tt = p.parse_ticks(
    ["X", "Y", "Z", "CCSPlayerPawn.m_angEyeAngles",
     "CCSPlayerPawn.m_aimPunchAngle", "CCSPlayerPawn.m_qDeathEyeAngles",
     "CCSPlayerPawn.CCSPlayer_CameraServices.m_vecCsViewPunchAngle",
     "CCSPlayerPawn.m_iTeamNum", "steamid", "tick", "health", "last_place_name"],
    ticks=list(range(5000, 5200, 16)),
)
print(" columns:", list(tt.columns))
print(" dtypes eyeAngles:", [str(x) for x in tt["CCSPlayerPawn.m_angEyeAngles"].tolist()[:8]])
for _, r in tt.head(3).iterrows():
    print("  tick", r["tick"], "steam", str(r["steamid"])[-4:], "eyeAngles", r["CCSPlayerPawn.m_angEyeAngles"],
          "punch", r["CCSPlayerPawn.m_aimPunchAngle"], "deathEye", r["CCSPlayerPawn.m_qDeathEyeAngles"])
# 结构化：eye angles 是列表/数组还是标量？
sample = tt["CCSPlayerPawn.m_angEyeAngles"].iloc[0]
print("  type of a single eyeAngles value:", type(sample).__name__, "->", sample)

# --- 2. 道具生命周期事件字段 ---
EVENTS = [
    "smokegrenade_detonate", "smokegrenade_expired",
    "hegrenade_detonate", "flashbang_detonate",
    "inferno_startburn", "inferno_expire",
    "decoy_started", "decoy_detonate",
]
print("\n=== [2] prop life-cycle events (parse_event fields) ===")
for ev in EVENTS:
    try:
        df = p.parse_event(ev)
        cols = list(df.columns)
        print(f"\n  --- {ev}: {len(df)} rows ---")
        print("    columns:", cols)
        if len(df) > 0 and not df.empty:
            print("    sample:", df.iloc[0].to_dict())
    except Exception as e:  # noqa: BLE001
        print(f"  --- {ev}: ERROR {e}")

# --- 3. grenade 实体生命周期（parse_grenades 轨迹 span） ---
print("\n=== [3] grenade entity lifecycle span (parse_grenades) ===")
g = p.parse_grenades()
print("  total rows:", len(g))
# 聚合：每 entity 首末 tick、类型、投掷者、首个有坐标点
gb = g.groupby(["grenade_type", "grenade_entity_id"])
recs = []
for (gtype, eid), grp in gb:
    thr = grp.iloc[0]
    coord = grp.dropna(subset=["x", "y", "z"])
    recs.append({
        "type": gtype, "entity": int(eid), "steamid": str(grp["steamid"].iloc[0]),
        "name": grp["name"].iloc[0], "first_tick": int(grp["tick"].min()),
        "last_tick": int(grp["tick"].max()), "n": len(grp),
        "has_coords": len(coord) > 0,
        "first_coord_tick": int(coord["tick"].min()) if len(coord) else None,
        "first_coord": [coord["x"].iloc[0], coord["y"].iloc[0]] if len(coord) else None,
    })
print("  dedup entities:", len(recs))
for r in recs[:8]:
    print("   ", r)
print("  ...")
# 每类型的实体数与 tick span 范围
import collections
by_type = collections.defaultdict(list)
for r in recs:
    by_type[r["type"]].append(r["last_tick"] - r["first_tick"])
for t, spans in by_type.items():
    print(f"   type {t}: n={len(spans)} span_min={min(spans)} span_max={max(spans)}")

# --- 4. 是否有 grenade_thrown / 投掷事件？ ---
print("\n=== [4] search for throw / bounce events ===")
all_ev = p.list_game_events()
for e in all_ev:
    if any(k in e.lower() for k in ("grenade", "throw", "bounce", "decoy", "smoke", "flash", "he_", "inferno", "molotov", "fire")):
        print("   candidate event:", e)
