"""6R8 勘探（三）：确认抛射体实体(Projectile) 与 detonate 事件的 entityid 关联方式。

用法: python scripts/explore_6r8_link.py <demo_path>
只读。
"""
from __future__ import annotations

import sys

from demoparser2 import DemoParser

p = DemoParser(sys.argv[1])

det = p.parse_event("smokegrenade_detonate")
exp = p.parse_event("smokegrenade_expired")
g = p.parse_grenades()

print("smoke detonate entityids:", sorted(det["entityid"].unique().tolist()))
print("smoke expire   entityids:", sorted(exp["entityid"].unique().tolist()))
print("detonate set == expire set?", set(det["entityid"]) == set(exp["entityid"]))

# parse_grenades 里各实体的 id 是否与事件 entityid 相同？
gp = g[g["grenade_type"].str.contains("Smoke")]
print("\nparse_grenades Smoke entity ids sample:", sorted(gp["grenade_entity_id"].unique().tolist())[:40])
print("intersection event_entityid ∩ parse_grenades_entityid:",
      set(det["entityid"]) & set(gp["grenade_entity_id"]))
# 一个具体例子
if len(det):
    e = det.iloc[0]
    eid = int(e["entityid"])
    sub = gp[gp["grenade_entity_id"] == eid]
    print(f"\nexample event entityid={eid} thrower={e['user_steamid']} tick={e['tick']} pos=({e['x']:.0f},{e['y']:.0f},{e['z']:.0f})")
    if len(sub):
        print("  parse_grenades rows for this entityid:", len(sub),
              "tick range", int(sub['tick'].min()), "-", int(sub['tick'].max()),
              "steamid set", set(sub['steamid'].astype(str)))
        print("  has_coords:", sub[['x','y','z']].notna().any(axis=1).any())
    else:
        print("  no parse_grenades rows with this entityid; look for same thrower+window:")
        win = g[(g['tick'].between(int(e['tick'])-200, int(e['tick'])+400)) &
                (g['steamid'].astype(str) == str(e['user_steamid'])) &
                (g['grenade_type'].str.contains('Smoke'))]
        print("  candidate rows by thrower+window:", len(win), win['grenade_type'].unique().tolist())
