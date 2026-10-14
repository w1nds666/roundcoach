"""生成 demo19_round_boundary.json fixture（真实 demo 19 数据）。

从 demo 19 重新解析出：turn rounds 窗口、首回合 start/freeze/end、
备战期早期击杀（tick < 首回合 start）与首回合窗口内击杀。
只提取非敏感字段（tick/weapon/round window），不落玩家身份/坐标。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.parser import DemoAnalyzer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "test demo" / "4614393950359818651_0" / "4614393950359818651_10000502.dem"
OUT = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures" / "demo19_round_boundary.json"


def build() -> None:
    a = DemoAnalyzer(str(DEMO))
    rounds = a.parse_rounds()
    deaths = a.parse_deaths()
    r1 = rounds[0]
    start = r1["start_tick"]
    end = r1["end_tick"]
    freeze = r1["freeze_end_tick"]
    mapper = DemoAnalyzer.round_mapper(rounds)

    early = [{"tick": int(d["tick"]), "weapon": str(d.get("weapon") or "")}
             for d in deaths if int(d["tick"]) < start]
    r1kills = [{"tick": int(d["tick"]), "weapon": str(d.get("weapon") or "")}
               for d in deaths if start <= int(d["tick"]) <= end]
    rounds_out = [{"round": r["round"], "start_tick": r["start_tick"],
                   "end_tick": r["end_tick"], "freeze_end_tick": r["freeze_end_tick"]}
                  for r in rounds]

    payload = {
        "demo_id": 19,
        "map": "de_mirage",
        "tick_rate": 64.0,
        "rounds": rounds_out,
        "round1": {"round": r1["round"], "start_tick": start, "freeze_end_tick": freeze,
                   "end_tick": end, "winner_team": r1.get("winner_team")},
        "early_kills": early,
        "round1_kills": r1kills,
        # 先按当前（错误）mapper 标出早期击杀归属，供审计对照（应全为 None 修复后）
        "old_attribution_of_early": [
            {"tick": int(d["tick"]), "old_round": a.round_mapper(rounds)(int(d["tick"]))}
            for d in deaths if int(d["tick"]) < start
        ],
        "note": "真实 demo 19 首回合边界样本；early_kills 为备战期击杀（不得归属任何正式回合）",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"  rounds={len(rounds_out)} early_kills={len(early)} round1_kills={len(r1kills)}")
    print(f"  round1 start={start} freeze={freeze} end={end}")
    print(f"  early ticks={[k['tick'] for k in early]}")


if __name__ == "__main__":
    build()
