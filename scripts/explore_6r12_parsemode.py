"""6R12 探针：同一 (tick, player) 下 parse_ticks 不同调用形式返回的 eyeAngles 是否一致。

读只。用法: python scripts/explore_6r12_parsemode.py <demo_path> <tick> <steamid>
"""
from __future__ import annotations

import sys

import numpy as np
from demoparser2 import DemoParser

demo, tick, sid = sys.argv[1], int(sys.argv[2]), sys.argv[3]
p = DemoParser(demo)
header = p.parse_header()
playout = int(header.get("playout_ticks") or 0)
print(f"map={header.get('map_name')} playout={playout} target_tick={tick} sid={sid}")

FIELDS = ["CCSPlayerPawn.m_angEyeAngles", "X", "Y", "Z", "health", "steamid", "tick"]

# 1) 单个明确 tick 列表（精度最高）—— 像 golden 一样
d1 = p.parse_ticks(FIELDS, ticks=[tick])
r1 = [r for r in d1.to_dict(orient="records") if str(r.get("steamid")) == sid]
print("\n[list exact] parse_ticks(ticks=[T]) 该玩家行:")
for r in r1:
    print("   tick=", int(r["tick"]), " eye=", r.get("CCSPlayerPawn.m_angEyeAngles"), " pos=", (r.get("X"), r.get("Y")))

# 2) range 全量（16 步），取最近 tick 的该玩家行
d2 = p.parse_ticks(FIELDS, ticks=range(0, playout + 1, 16))
near = min(d2.to_dict(orient="records"), key=lambda r: (abs(int(r.get("tick")) - tick), str(r.get("steamid")) != sid))
print("\n[range 16] 最近行（可能是别的时间/玩家）:")
print("   tick=", int(near.get("tick")), " steamid=", near.get("steamid"), " eye=", near.get("CCSPlayerPawn.m_angEyeAngles"))
# 该玩家在 range 里 tick 附近的行
for r in d2.to_dict(orient="records"):
    if abs(int(r.get("tick")) - tick) <= 32 and str(r.get("steamid")) == sid:
        print(f"   ~rank sid match tick={int(r.get('tick'))} eye={r.get('CCSPlayerPawn.m_angEyeAngles')}")

# 3) 明确的一个"含 T"的列表（观察与 range 是否同值）
d3 = p.parse_ticks(FIELDS, ticks=[tick, max(0, tick - 16), tick - 32, tick + 16])
print("\n[list multi] 该玩家行:")
for r in d3.to_dict(orient="records"):
    if str(r.get("steamid")) == sid:
        print("   tick=", int(r["tick"]), " eye=", r.get("CCSPlayerPawn.m_angEyeAngles"))
