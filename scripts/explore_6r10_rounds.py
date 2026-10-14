"""6R10A 探索：真实 demo 19/20/21 的回合边界与早期击杀归属原始对照。

不修改任何数据；直接用 demoparser2 轻量重读事件与回合，输出：
  - round_start / round_freeze_end / round_end 原始 tick
  - parse_rounds 结果（start/end/freeze/winner）
  - 每场的"早期击杀"（tick 落在首回合 start 之前的击杀）原始归属
  - parser.round_mapper 与 routes._round_mapper 的归属差异
"""
from __future__ import annotations

import bisect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.parser import DemoAnalyzer  # noqa: E402
from app.services.routes import _round_mapper as routes_round_mapper  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

TARGETS = {
    19: ("test demo/4614393950359818651_0/4614393950359818651_10000502.dem", "de_mirage"),
    20: ("test demo/4615096941117908123_0/4615096941117908123_10000485.dem", "de_anubis"),
    21: ("test demo/4626848676756337691_0/4626848676756337691_10000489.dem", "de_dust2"),
}


def analyze(demo_id: int) -> None:
    rel, expect_map = TARGETS[demo_id]
    p = ROOT / rel
    if not p.exists():
        print(f"demo {demo_id}: 文件缺失 {rel}")
        return
    print("=" * 70)
    print(f"demo {demo_id}  {expect_map}  ({p.name})")
    a = DemoAnalyzer(str(p))
    header = a.parse_header()
    tick_rate = header.tick_rate
    print(f"  tick_rate={tick_rate}  playout_ticks={header.playout_ticks}")

    starts = a._parser.parse_event("round_start", other=["tick"]).to_dict(orient="records")
    freezes = a._parser.parse_event("round_freeze_end", other=["tick"]).to_dict(orient="records")
    ends = a._parser.parse_event("round_end", other=["tick", "winner"]).to_dict(orient="records")
    starts.sort(key=lambda r: r["tick"])
    freezes.sort(key=lambda r: r["tick"])
    ends.sort(key=lambda r: r["tick"])
    print(f"  raw events: round_start={len(starts)}  round_freeze_end={len(freezes)}  round_end={len(ends)}")
    print("  raw round_start ticks (first 12):", [int(r["tick"]) for r in starts[:12]])
    print("  raw round_freeze_end ticks (first 12):", [int(r["tick"]) for r in freezes[:12]])
    print("  raw round_end ticks (first 12):", [int(r["tick"]) for r in ends[:12]])

    rounds = a.parse_rounds()
    print(f"  parse_rounds -> {len(rounds)} 正式回合")
    for r in rounds[:4]:
        print(f"    R{r['round']}: start={r['start_tick']} freeze={r['freeze_end_tick']} "
              f"end={r['end_tick']} winner_side={r['winner_side']} winner_team={r['winner_team']}")

    if not rounds:
        print("  无正式回合，跳过击杀对照")
        return

    first_start = rounds[0]["start_tick"]
    first_freeze = rounds[0]["freeze_end_tick"]
    first_end = rounds[0]["end_tick"]

    deaths = a.parse_deaths()
    deaths.sort(key=lambda r: int(r["tick"]))
    # 击杀在首回合 start 之前的（"早期"）
    early = [d for d in deaths if int(d["tick"]) < first_start]
    print(f"\n  deaths 总数={len(deaths)}  首回合 start 之前的击杀数={len(early)}")
    for d in early[:12]:
        print(f"    tick={int(d['tick'])} attacker={str(d.get('attacker_name'))[:14]!r} "
              f"victim={str(d.get('user_name'))[:14]!r} weapon={d.get('weapon')}")

    # 两种 mapper 对 tick 的归属
    p_mapper = a.round_mapper(rounds)
    r_mapper = routes_round_mapper(rounds)
    print("\n  击杀归属对照（tick < 首回合 start 的样本，及首回合窗口内样本）:")
    shown = 0
    for d in deaths:
        tk = int(d["tick"])
        pm = p_mapper(tk)
        rm = r_mapper(tk)
        if tk < first_start and shown < 8:
            print(f"    tick={tk} round_start_window? te={d.get('weapon')}  "
                  f"[parser.round_mapper={pm}]  [routes._round_mapper={rm}]")
            shown += 1
    # 首回合内 kill 的 sec（相对 freeze_end）
    print(f"\n  首回合（start={first_start} freeze={first_freeze} end={first_end}）:")
    r1_kills = [d for d in deaths if first_start <= int(d["tick"]) <= first_end]
    for d in r1_kills[:15]:
        tk = int(d["tick"])
        sec = round((tk - first_freeze) / tick_rate, 2) if tick_rate else None
        print(f"    tick={tk} sec={sec}  attacker={str(d.get('attacker_name'))[:12]!r} "
              f"victim={str(d.get('user_name'))[:12]!r} weapon={d.get('weapon')}")


if __name__ == "__main__":
    for did in (sys.argv[1:] or ["19", "20", "21"]):
        analyze(int(did))
