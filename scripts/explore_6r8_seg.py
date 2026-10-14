"""6R8 勘探（四）：验证 Projectile 实体按连续 tick 段切分可还原单次投掷与 detonate 事件对应。

用法: python scripts/explore_6r8_seg.py <demo_path>
"""
from __future__ import annotations

import sys
from collections import defaultdict

from demoparser2 import DemoParser

p = DemoParser(sys.argv[1])
PROJ = ("CSmokeGrenadeProjectile", "CFlashbangProjectile", "CHEGrenadeProjectile",
        "CMolotovProjectile", "CDecoyProjectile")
g = p.parse_grenades()
gp = g[g["grenade_type"].isin(PROJ)].copy()
print("projectile rows:", len(gp), "raw entity-ids:", gp['grenade_entity_id'].nunique())

# 事件索引（entityid -> (tick, steamid, type))
events = defaultdict(list)
for ev in ("smokegrenade_detonate", "hegrenade_detonate", "flashbang_detonate", "decoy_detonate"):
    for _, r in p.parse_event(ev).iterrows():
        events["detonate"].append((int(r["entityid"]), int(r["tick"]), str(r["user_steamid"]), ev, float(r["x"]), float(r["y"])))

# 把每个 entity-id 切成连续 tick 段（下一 tick 间隔 > 64 视为新段）
segments = []
for eid, grp in gp.groupby("grenade_entity_id"):
    grp = grp.sort_values("tick")
    ticks = grp["tick"].tolist()
    starts = [0]
    for i in range(1, len(ticks)):
        if ticks[i] - ticks[i - 1] > 64:
            starts.append(i)
    starts.append(len(ticks))
    for k in range(len(starts) - 1):
        s, e = starts[k], starts[k + 1]
        seg = grp.iloc[s:e]
        segs = seg
        sid = str(seg["steamid"].iloc[0])
        segments.append({
            "entity": eid, "type": seg["grenade_type"].iloc[0], "steamid": sid,
            "name": seg["name"].iloc[0], "t0": int(seg["tick"].min()), "t1": int(seg["tick"].max()),
            "n": len(seg),
            "uniq_steam": seg["steamid"].astype(str).nunique(),
            "has_coords": seg[["x", "y", "z"]].notna().any(axis=1).any(),
        })
print("contiguous segments:", len(segments))
print("segments with single thrower:", sum(1 for s in segments if s["uniq_steam"] == 1))
print("segments with coords:", sum(1 for s in segments if s["has_coords"]))

# 事件与段匹配：同一 thrower + 段 t0 <= detonate.tick <= t1 + 200（或 detonate 在段附近）
det_all = events["detonate"]
matched = 0
for s in segments:
    sid, t0, t1 = s["steamid"], s["t0"], s["t1"]
    hit = [d for d in det_all if d[2] == sid and (t0 - 64 <= d[1] <= t1 + 256)]
    if hit:
        matched += 1
print(f"segments matched to a detonate event by (thrower + window): {matched} / {len(segments)}")

# 展示几个样例
print("\nsample segments with match:")
shown = 0
for s in segments:
    hit = [d for d in det_all if d[2] == s["steamid"] and (s["t0"] - 64 <= d[1] <= s["t1"] + 256)]
    if hit and shown < 6:
        d = hit[0]
        print(f"  {s['type']} steam={s['steamid'][-4:]} segt[{s['t0']}-{s['t1']}] n={s['n']} coords={s['has_coords']} -> detonate ev={d[3]} tick={d[1]} x={d[4]:.0f} y={d[5]:.0f}")
        shown += 1

# detonate 里有多少能按 thrower 找到匹配段？
det_with_seg = sum(1 for d in det_all if any(s["steamid"] == d[2] and (s["t0"] - 64 <= d[1] <= s["t1"] + 256) for s in segments))
print(f"\ndetonate events with a matching trajectory segment: {det_with_seg} / {len(det_all)}")
