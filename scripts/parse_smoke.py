"""Phase 1 冒烟验证：用官方测试 demo 跑通 demoparser2 全链路解析。

用法：backend/.venv/Scripts/python.exe scripts/parse_smoke.py
预期：打印 header / 玩家数 / 击杀事件数 / 伤害事件数 / 投掷物数 / tick 坐标采样
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.parser import DemoAnalyzer

DEMO = Path(__file__).resolve().parent.parent / "backend" / "tests" / "demos" / "test_demo.dem"


def main() -> None:
    if not DEMO.exists():
        print(f"demo not found: {DEMO}")
        sys.exit(1)

    t0 = time.time()
    analyzer = DemoAnalyzer(DEMO)

    header = analyzer.parse_header()
    print(f"[header] map={header.map_name} players={header.player_count} "
          f"ticks={header.playout_ticks} tick_rate={header.tick_rate} "
          f"duration={header.duration_seconds:.0f}s ({time.time()-t0:.1f}s)")

    players = analyzer.parse_player_info()
    print(f"[players] {len(players)} rows; sample: "
          f"{players[0] if players else 'N/A'}")

    deaths = analyzer.parse_deaths()
    print(f"[deaths] {len(deaths)} rows; sample keys: "
          f"{[k for k in deaths[0].keys() if k in ('user_X','user_Y','attacker_X','attacker_Y','weapon','headshot','tick')] if deaths else 'N/A'}")
    if deaths:
        d = deaths[0]
        print(f"  e.g. {d.get('weapon')} victim at ({d.get('user_X')}, {d.get('user_Y')}) "
              f"attacker at ({d.get('attacker_X')}, {d.get('attacker_Y')}) "
              f"headshot={d.get('headshot')} tick={d.get('tick')}")

    hurts = analyzer.parse_hurts()
    print(f"[hurts] {len(hurts)} rows; sample: "
          f"{list(hurts[0].keys())[:8] if hurts else 'N/A'}")

    grenades = analyzer.parse_grenades()
    print(f"[grenades] {len(grenades)} rows; sample keys: "
          f"{list(grenades[0].keys())[:12] if grenades else 'N/A'}")
    if grenades:
        print(f"  e.g. {grenades[0]}")

    # tick 坐标采样：只取前 2000 行验证字段（全量 23 万行入库在 Celery 任务里做）
    t1 = time.time()
    df = analyzer._parser.parse_ticks(
        ["X", "Y", "last_place_name", "money", "health", "round", "team"],
        ticks=range(0, 2000, 100),
    )
    rows = df.to_dict(orient="records")
    print(f"[ticks sample] {len(rows)} rows (every 100th tick of first 2000); "
          f"first: {rows[0] if rows else 'N/A'} ({time.time()-t1:.1f}s)")

    print(f"\nTOTAL: {(time.time()-t0):.1f}s — demo parsing smoke test PASSED")


if __name__ == "__main__":
    main()
