"""6R13 勘探 crosscheck：cross demo19-23 字段可读性 + 关键关联验证。

只读。用法: python scripts/explore_6r13_crosscheck.py <path=label>...
"""
from __future__ import annotations

import sys
from pathlib import Path

from demoparser2 import DemoParser

DEMOS = sys.argv[1:] if len(sys.argv) > 1 else []
# 若没给参数，回退到内置清单（<path> 简写为 <label>）
if not DEMOS:
    DEMOS = [
        "test demo/4614393950359818651_0/4614393950359818651_10000502.dem|demo19-mirage",
        "test demo/4615096941117908123_0/4615096941117908123_10000485.dem|demo20-anubis",
        "test demo/4626848676756337691_0/4626848676756337691_10000489.dem|demo21-dust2",
        "test demo/9209049647259770252_0/9209049647259770252_0.dem|demo22-inferno",
        "test demo/9219534615131736844_0/9219534615131736844_0.dem|demo23-ancient",
    ]


def _cols(p, ev, player=None, other=None):
    try:
        kw = {}
        if player is not None:
            kw["player"] = player
        if other is not None:
            kw["other"] = other
        df = p.parse_event(ev, **kw)
        return df, list(df.columns)
    except Exception as e:  # noqa: BLE001
        return None, [f"ERROR {e}"]


for item in DEMOS:
    if "|" in item:
        rel, label = item.split("|", 1)
    else:
        rel, label = item, Path(item).stem
    p = DemoParser(rel)
    hdr = p.parse_header()
    print(f"\n#### {label} :: {hdr.get('map_name')} ####")

    # key events exist?
    for ev in ["player_death", "player_hurt", "weapon_fire", "player_blind",
               "smokegrenade_detonate", "smokegrenade_expired", "inferno_startburn",
               "inferno_expire", "hegrenade_detonate", "flashbang_detonate"]:
        ge = p.list_game_events()
        exists = ev in (ge if isinstance(ge, list) else [])
        print(f"  event {ev}: {'YES' if exists else 'no'}")

    # player_hurt weapon 分布（HE/molotov 价值）
    dh, cols = _cols(p, "player_hurt", player=["X", "Y"],
                     other=["attacker_steamid", "weapon", "dmg_health", "tick", "user_steamid"])
    if dh is not None and len(dh):
        wc = dh["weapon"].value_counts().to_dict()
        print(f"  player_hurt n={len(dh)} weapons={wc}")

    # player_blind ↔ flashbang_detonate：entityid + tick 关联验证
    df, _ = _cols(p, "flashbang_detonate", other=["entityid", "tick"])
    db, _ = _cols(p, "player_blind", other=["entityid", "tick", "blind_duration", "user_steamid"])
    if df is not None and db is not None and len(df) and len(db):
        fmap = {}
        for _, r in df.iterrows():
            fmap.setdefault(int(r["entityid"]), []).append(int(r["tick"]))
        ok = 0
        total = 0
        for _, r in db.iterrows():
            eid = int(r["entityid"]); bt = int(r["tick"])
            cand = fmap.get(eid)
            if cand is None:
                continue
            total += 1
            # 最近 detonate tick
            nearest = min(cand, key=lambda t: abs(t - bt))
            if abs(nearest - bt) <= 256:
                ok += 1
        print(f"  player_blind n={len(db)}; linked-to-flashby-eid+window: {ok}/{total}")
    else:
        print(f"  player_blind or flashbang_detonate unavailable (death={df is not None}, blind={db is not None})")

    # player_death weapon 前缀样本
    dd, _ = _cols(p, "player_death", player=["X", "Y"],
                  other=["attacker_steamid", "user_steamid", "weapon", "headshot",
                         "thrusmoke", "penetrated", "assister_steamid"])
    if dd is not None and len(dd):
        wc = dd["weapon"].value_counts().head(12).to_dict()
        print(f"  player_death n={len(dd)} top weapons={wc}")
