"""Phase 1 数据管线验证：解析 → 回合映射 → 事件标注 round → 基础统计。

用法：backend/.venv/Scripts/python.exe scripts/stats_smoke.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.parser import DemoAnalyzer
from app.services.stats import compute_basic_stats, compute_match_score

DEMO = Path(__file__).resolve().parent.parent / "backend" / "tests" / "demos" / "test_demo.dem"


def main() -> None:
    analyzer = DemoAnalyzer(DEMO)

    header = analyzer.parse_header()
    rounds = analyzer.parse_rounds()
    print(f"[match] {header.map_name} rounds={len(rounds)} "
          f"duration={header.duration_seconds}s")
    print(f"  score CT {compute_match_score(rounds)[0]} : T {compute_match_score(rounds)[1]}")

    players = analyzer.parse_player_info()
    steamids = [str(p["steamid"]) for p in players]
    mapper = analyzer.round_mapper(rounds)

    deaths = analyzer.parse_deaths()
    for d in deaths:
        d["round"] = mapper(d["tick"])  # 注入回合号
    hurts = analyzer.parse_hurts()
    for h in hurts:
        h["round"] = mapper(h["tick"])

    stats = compute_basic_stats(deaths, hurts, steamids, rounds)
    print("\n[player_stats] (top by ADR)")
    name_of = {str(p["steamid"]): p["name"] for p in players}
    for sid in sorted(stats, key=lambda s: -stats[s]["adr"]):
        s = stats[sid]
        print(f"  {name_of.get(sid, sid)[:16]:<18} K {s['kills']:>2} D {s['deaths']:>2} "
              f"A {s['assists']:>2} HS {s['hs_rate']:.0%} DMG {s['damage']:>4} "
              f"ADR {s['adr']:>5.1f} FK {s['first_kills']}")

    print("\n[first round] rounds[0] =", rounds[0])
    print("\nPhase 1 data pipeline smoke test PASSED")


if __name__ == "__main__":
    main()
