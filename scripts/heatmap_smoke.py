"""Phase 2 热力图验证：用真实 demo 的击杀坐标生成热力图 PNG。

用法：backend/.venv/Scripts/python.exe scripts/heatmap_smoke.py
输出：backend/tests/output/heatmap_kills_mirage.png + 网格 JSON
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.heatmap import generate_heatmap_grid, generate_heatmap_png
from app.services.parser import DemoAnalyzer

DEMO = Path(__file__).resolve().parent.parent / "backend" / "tests" / "demos" / "test_demo.dem"
OUT_DIR = Path(__file__).resolve().parent.parent / "backend" / "tests" / "output"


def main() -> None:
    analyzer = DemoAnalyzer(DEMO)
    header = analyzer.parse_header()
    map_name = header.map_name
    print(f"[map] {map_name}")

    deaths = analyzer.parse_deaths()
    # 击杀热力图：使用凶手位置（attacker_X/Y）即击杀发生点
    points = [(d["attacker_X"], d["attacker_Y"]) for d in deaths
              if d.get("attacker_X") is not None and d.get("attacker_Y") is not None]
    print(f"[points] {len(points)} kill locations (of {len(deaths)} deaths)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png = generate_heatmap_png(points, map_name, OUT_DIR / "heatmap_kills_mirage.png")
    print(f"[png] saved {png}")

    grid = generate_heatmap_grid(points, map_name)
    grid_path = OUT_DIR / "heatmap_grid_mirage.json"
    grid_path.write_text(json.dumps(grid), encoding="utf-8")
    print(f"[grid] saved {grid_path} ({len(grid['data'])} non-zero cells)")


if __name__ == "__main__":
    main()
