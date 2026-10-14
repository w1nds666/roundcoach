"""6R12：生成 golden-set 真值门禁证据（只读）。

对 demos 19-23 运行 gaze.build_shoot_events，汇总：
  - 每个 demo 的 class 计数、direct 击杀数、门禁统计（median/p95/bias/passed）
  - 全量 direct 角误差列表（写 JSON 供回归测试加载）
输出：
  - docs/phase6r12-golden-set.json（全量角误差 + 每 demo 统计）
  - 控制台汇总
只读，不改库。用法: python scripts/explore_6r12_gate.py [demo_id...|all]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.parser import DemoAnalyzer  # noqa: E402
from app.services.gaze import build_shoot_events  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    19: ("test demo/4614393950359818651_0/4614393950359818651_10000502.dem", "de_mirage"),
    20: ("test demo/4615096941117908123_0/4615096941117908123_10000485.dem", "de_anubis"),
    21: ("test demo/4626848676756337691_0/4626848676756337691_10000489.dem", "de_dust2"),
    22: ("test demo/9209049647259770252_0/9209049647259770252_0.dem", "de_inferno"),
    23: ("test demo/9219534615131736844_0/9219534615131736844_0.dem", "de_ancient"),
}

summary = []
all_errors_all_demos = []
per_demo_errors = {}


def run(demo_id: int) -> None:
    rel, expect_map = TARGETS[demo_id]
    p = ROOT / rel
    if not p.exists():
        print(f"demo {demo_id}: 文件缺失 {rel}")
        return
    a = DemoAnalyzer(str(p))
    header = a.parse_header()
    rounds = a.parse_rounds()
    players = a.parse_player_info()
    deaths = a.parse_deaths()
    player_id_of = {str(x["steamid"]): i for i, x in enumerate(players, start=1)}
    gaze = build_shoot_events(a, rounds, deaths, player_id_of, header.tick_rate)
    gate = gaze["gate"]
    errors = gaze["errors"]
    summary.append({"demo": demo_id, "map": header.map_name, "counts": gaze["counts"],
                    "gate": gate, "errors": errors,
                    "shoots_in_rounds": sum(len(v) for v in gaze["per_round"].values())})
    print(f"demo {demo_id} {header.map_name} gate_n={gate['n']} median={gate.get('median')} "
          f"p95={gate.get('p95')} passed={gate.get('passed')} counts={gaze['counts']}")


def main():
    ids = [int(x) for x in sys.argv[1:]] if sys.argv[1:] and sys.argv[1] != "all" else list(TARGETS)
    for did in ids:
        run(did)
    # 汇总到 JSON（docs + tests fixture）
    docs_dir = ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)
    with open(docs_dir / "phase6r12-golden-set.json", "w", encoding="utf-8") as f:
        json.dump({"demos": summary}, f, ensure_ascii=False, indent=2)
    fixt_dir = ROOT / "backend" / "tests" / "fixtures"
    fixt_dir.mkdir(parents=True, exist_ok=True)
    with open(fixt_dir / "phase6r12-golden.json", "w", encoding="utf-8") as f:
        json.dump({"demos": summary}, f, ensure_ascii=False, indent=2)
    print("\nWrote docs/phase6r12-golden-set.json + backend/tests/fixtures/phase6r12-golden.json")


if __name__ == "__main__":
    main()
