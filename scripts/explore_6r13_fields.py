"""6R13 字段勘探：击杀播报 / 投掷归属 / 烟 火 生命周期 / 真实闪光致盲 / HE 伤害 真实来源。

只读勘探，不写库。用法（用 backend/.venv 解释器）:
  python scripts/explore_6r13_fields.py <demo_path> <label>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from demoparser2 import DemoParser

demo = sys.argv[1]
label = sys.argv[2] if len(sys.argv) > 2 else Path(demo).stem

p = DemoParser(demo)
print(f"\n##### {label}: {Path(demo).name} #####")
hdr = p.parse_header()
print(f"map={hdr.get('map_name')} server={hdr.get('server_name')}")

ge = p.list_game_events()
print("\n=== game_events 全量 ===")
if isinstance(ge, list):
    for g in ge:
        print("  ", g)


def _probe(ev, player=None, other=None, label_=None, n=5, extra_all=False):
    print(f"\n### parse_event('{ev}') ###")
    kw = {}
    if player is not None:
        kw["player"] = player
    if other is not None:
        kw["other"] = other
    try:
        df = p.parse_event(ev, **kw)
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {e}")
        return {}
    print(f"  n={len(df)} columns={list(df.columns)}")
    if len(df):
        # 缺失率
        print("  missing rate (top):")
        miss = df.isna().mean().sort_values(ascending=False)
        for col, m in list(miss.head(20).items()):
            if m > 0:
                print(f"    {col}: {m:.1%}")
        print("  sample:")
        print(df.head(n).to_string())
    return df


# ---- 1. 击杀播报：player_death（含助攻/穿烟/穿墙/爆头）----
death_kw = {
    "player": ["X", "Y"],
    "other": ["attacker_steamid", "attacker_name", "attacker_X", "attacker_Y",
              "user_steamid", "user_name", "weapon", "headshot", "distance",
              "hitgroup", "tick", "thrusmoke", "penetrated", "noscope",
              "assister_steamid", "assister_name", "assistedflash"],
}
d = _probe("player_death", **death_kw)

# ---- 2. player_hurt（HE 伤害关联候选：damage/位置/受击）----
hurt_kw = {
    "player": ["X", "Y"],
    "other": ["attacker_steamid", "attacker_name", "attacker_X", "attacker_Y",
              "user_steamid", "user_name", "weapon", "dmg_health", "dmg_armor",
              "health", "armor", "hitgroup", "tick", "all_p", 'all_type', 'all_value'],
}
h = _probe("player_hurt", **hurt_kw)

# ---- 3. weapon_fire（已确认存在，看 weapon 值/缺失）----
try:
    wf = p.parse_event("weapon_fire", other=["tick", "weapon", "attacker_steamid", "attacker_name"])
    print(f"\n### weapon_fire: n={len(wf)} columns={list(wf.columns)}")
    if len(wf):
        print("  weapon values:", wf["weapon"].value_counts().head(20).to_dict())
        print(wf.head(5).to_string())
except Exception as e:  # noqa: BLE001
    print(f"\nweapon_fire ERROR: {e}")

# ---- 4. 投掷事件（真实来源/字段）----
for ev in ["smokegrenade_detonate", "smokegrenade_expired", "inferno_startburn", "inferno_expire",
           "hegrenade_detonate", "flashbang_detonate"]:
    _probe(ev, other=["entityid", "tick", "user_steamid", "user_name", "user_X", "user_Y", "user_Z"])
    try:
        r = p.parse_event(ev, other=["entityid", "tick", "user_steamid"])
        if len(r):
            print("  value_counts entityid:", r["entityid"].value_counts().head(5).to_dict())
    except Exception:  # noqa: BLE001
        pass

# ---- 5. player_blind（真实闪光致盲关键）----
bl = _probe("player_blind", other=["tick", "user_steamid", "user_name", "attacker_steamid",
                                   "attacker_name", "duration", "startpos", "user_X", "user_Y"])
if bl:
    for col in ["user_steamid", "attacker_steamid", "duration", "hitgroup"]:
        if col in bl.columns:
            print(f"  unique {col}: {bl[col].nunique()}")
