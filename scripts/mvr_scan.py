"""MVR 验收准备：只读扫描 test demo 内真实 .dem（轻量 parse_header，不触发全量解析）。

- 使用 demoparser2 原生 parse_header（不调用 DemoAnalyzer.parse_header——那会 parse_ticks 全量）
- 单文件失败不中断总扫描（记录错误继续）
- 输出脱敏清单：文件名/大小/map_name/server_name/可读性；不写玩家身份或 SteamID

用法：backend/.venv/Scripts/python.exe scripts/mvr_scan.py
输出：docs/mvr-demo-inventory.md
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "test demo"
OUT = ROOT / "docs" / "mvr-demo-inventory.md"

# 每个文件只读 header（轻量；不解析事件/ticks）
def read_header_only(path: Path) -> dict:
    from demoparser2 import DemoParser

    parser = DemoParser(str(path))
    header = parser.parse_header()
    return {
        "map_name": header.get("map_name", ""),
        "server_name": header.get("server_name", ""),
        "demo_file_stamp": header.get("demo_file_stamp", ""),
    }


def main() -> int:
    demos = sorted(
        [p for p in DEMO_DIR.rglob("*.dem") if p.is_file()],
        key=lambda p: p.name,
    )
    print(f"发现 {len(demos)} 个 .dem 文件于 {DEMO_DIR}")

    rows = []
    ok = 0
    failed = 0
    for p in demos:
        size_mib = p.stat().st_size / (1024 * 1024)
        entry = {
            "file": p.name,
            "size_mib": round(size_mib, 1),
            "map_name": None,
            "server_name": None,
            "readable": False,
            "error": None,
        }
        try:
            h = read_header_only(p)
            entry["map_name"] = h["map_name"] or None
            entry["server_name"] = h["server_name"] or None
            entry["readable"] = True
            ok += 1
            print(f"  OK   {p.name}  {size_mib:7.1f} MiB  map={entry['map_name']}")
        except Exception as exc:  # noqa: BLE001  单文件失败不中断
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            failed += 1
            print(f"  FAIL {p.name}  {size_mib:7.1f} MiB  {entry['error']}")
        rows.append(entry)

    # 地图分布
    from collections import Counter

    map_counter = Counter(r["map_name"] for r in rows if r["readable"])

    lines = [
        "# MVR Demo 清单（脱敏）",
        "",
        f"> 生成时间：{datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}",
        "> 说明：仅记录文件名/大小/地图/服务器（均为非敏感元数据），**不含玩家身份、SteamID、昵称**。",
        "",
        "## 汇总",
        "",
        f"- 扫描文件数：{len(rows)}",
        f"- 可读（parse_header 成功）：{ok}",
        f"- 不可读（单文件失败，未中断扫描）：{failed}",
        f"- 地图分布：{dict(map_counter)}",
        "",
        "## 明细",
        "",
        "| # | 文件名 | 大小 (MiB) | 地图 | 服务器 | 可读 | 备注 |",
        "|---|--------|-----------:|------|--------|------|------|",
    ]
    for i, r in enumerate(rows, start=1):
        lines.append(
            f"| {i} | {r['file']} | {r['size_mib']:.1f} | {r['map_name'] or '—'} | "
            f"{(r['server_name'] or '—')[:40]} | {'✅' if r['readable'] else '❌'} | {r['error'] or ''} |"
        )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n清单已写入 {OUT}（可读 {ok} / 失败 {failed}）")
    return 0 if failed == 0 else 2  # 有失败也返回 2（供调用方知晓，不阻断）


if __name__ == "__main__":
    sys.exit(main())
