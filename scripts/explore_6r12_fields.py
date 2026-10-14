"""6R12 勘探：weapon_fire 事件字段、与 death 事件关联、以及 eye-yaw → 击杀方向候选变换。

只读勘探，不写库。用法:
  python scripts/explore_6r12_fields.py <demo_path> [sample_tick]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from demoparser2 import DemoParser


def main(demo: str, sample_tick: int = 6000):
    p = DemoParser(demo)
    print("=" * 72)
    print(f"######## {Path(demo).name} ########")
    hdr = p.parse_header()
    print(f"map={hdr.get('map_name')}  server={hdr.get('server_name')}")

    # 1) 找出 weapon 相关事件名
    ge = p.list_game_events()
    if isinstance(ge, list):
        wf_events = [g for g in ge if "weapon" in str(g).lower() or "shot" in str(g).lower() or "fire" in str(g).lower()]
        print("\n=== weapon/event list ===")
        for g in wf_events:
            print("  ", g)

    # 2) parse weapon_fire（若存在）看字段
    try:
        wf = p.parse_event("weapon_fire")
        print(f"\n=== weapon_fire: n={len(wf)} columns={list(wf.columns)} ===")
        if len(wf):
            print(wf.head(5).to_string())
            # weapon 去重
            if "weapon" in wf.columns:
                print("\nweapon values:", wf["weapon"].value_counts().head(20).to_dict())
    except Exception as e:  # noqa: BLE001
        print(f"\nweapon_fire parse ERROR: {e}")

    # 3) parse player_death 看字段（含位置/tick）
    try:
        deaths = p.parse_event(
            "player_death",
            player=["X", "Y"],
            other=["attacker_steamid", "attacker_name", "attacker_X", "attacker_Y",
                   "user_steamid", "user_name", "weapon", "headshot", "distance",
                   "hitgroup", "tick", "thrusmoke", "penetrated", "noscope"],
        )
        print(f"\n=== player_death: n={len(deaths)} ===")
        if len(deaths):
            print(deaths.head(6).to_string())
    except Exception as e:  # noqa: BLE001
        print(f"\nplayer_death parse ERROR: {e}")

    # 4) eye angle sample at a tick slice (structure + range)
    tt = p.parse_ticks(["CCSPlayerPawn.m_angEyeAngles", "health", "X", "Y", "tick"],
                       ticks=list(range(sample_tick, sample_tick + 512, 16)))
    ea = tt["CCSPlayerPawn.m_angEyeAngles"].dropna()
    print(f"\n=== eyeAngles n={len(ea)} ===")
    if len(ea):
        arr = np.array(ea.tolist())
        print(f"  [pitch] range [{arr[:, 0].min():.1f},{arr[:, 0].max():.1f}]")
        print(f"  [yaw]   range [{arr[:, 1].min():.1f},{arr[:, 1].max():.1f}]")
        print(f"  [roll]  max|.|={np.abs(arr[:, 2]).max():.1f}")
        print(tt.head(5).to_string())


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 6000)
