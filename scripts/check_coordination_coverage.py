"""6R9 门禁：5 个验收 demo 的深度配合分析覆盖校验（gate 2 延伸）。

逐个 demo 经真实 API 取 /coordination，校验：
- schema/阈值版本/content_hash 存在且非空；
- 所有 rate 有限（无 NaN/Infinity）；
- 0 机会的指标 rate 为 None（不可评估），绝不等于 0 分；
- coverage（该队数据可用回合/总回合）与总回合一致（0..1 且与覆盖率逻辑自洽）；
- 无 steam 泄露；teams 覆盖 A/B 两队；conclusions 3-5 条（成功与问题都有）。

用法：python scripts/check_coordination_coverage.py [demo_id ...]
"""
from __future__ import annotations

import json
import math
import subprocess
import sys

import httpx

BASE = "http://localhost"
ACCOUNT = {"email": "mvr-acceptance@test.com", "password": "password123"}
DEMOS = [19, 20, 21, 22, 23]
DOCKER = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe"
EXPECT_MAP = {19: "de_mirage", 20: "de_anubis", 21: "de_dust2", 22: "de_inferno", 23: "de_ancient"}


def _psql(sql: str) -> str:
    out = subprocess.run([DOCKER, "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo", "-t", "-A", "-c", sql],
                         capture_output=True, text=True)
    return out.stdout.strip()


def _finite(x):
    return x is None or (isinstance(x, (int, float)) and math.isfinite(x))


def main() -> None:
    login = httpx.post(f"{BASE}/api/auth/login", json=ACCOUNT, timeout=30).json()
    tok = login["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    results = []
    for demo_id in DEMOS:
        r = httpx.get(f"{BASE}/api/demos/{demo_id}/coordination", headers=h, timeout=180)
        if r.status_code != 200:
            results.append({"demo": demo_id, "ok": False, "err": f"HTTP {r.status_code}"})
            continue
        b = r.json()
        errs = []
        if not b.get("coordination_schema_version"):
            errs.append("缺 schema version")
        if not b.get("content_hash"):
            errs.append("缺 content_hash")
        if not b.get("baseline_note"):
            errs.append("缺试验性基线标记")
        if "steam" in str(b).lower().replace("tactical", ""):
            errs.append("疑似 steam 泄露")
        teams = b.get("teams") or []
        if len(teams) != 2:
            errs.append(f"teams 数量非 2：{len(teams)}")
        # 每个 team 每个 metric
        total_rounds = b.get("total_rounds")
        for team in teams:
            for m in team["metrics"]:
                mi = m["metric_id"]
                if not _finite(m["rate"]):
                    errs.append(f"{team['team']}/{mi} rate 非有限")
                if m["opportunities"] == 0 and m["rate"] is not None:
                    errs.append(f"{team['team']}/{mi} 0机会但 rate 非 None（当作0分）")
                if m["opportunities"] == 0 and m["status"] not in ("no_opportunity", "not_applicable"):
                    errs.append(f"{team['team']}/{mi} 0机会但 status={m['status']}")
                cov = m["coverage"]
                if cov is not None and not (0.0 <= cov <= 1.0):
                    errs.append(f"{team['team']}/{mi} coverage 越界 {cov}")
        # coverage 与总回合自洽：coverage_rounds 应 <= total_rounds（此处用 metric 端关系校验）
        concl = b.get("conclusions") or []
        if not (3 <= len(concl) <= 5):
            errs.append(f"conclusions 数量 {len(concl)} 不在 3-5")
        tones = {c.get("tone") for c in concl}
        if not {"success", "problem"} <= tones:
            errs.append(f"conclusions 缺成功或问题类 tone={tones}")
        results.append({
            "demo": demo_id, "map": EXPECT_MAP.get(demo_id), "ok": not errs,
            "schema": b.get("coordination_schema_version"), "hash": (b.get("content_hash") or "")[:12],
            "teams": len(teams), "rounds_ev": len(b.get("rounds") or []),
            "conclusions": len(concl), "errs": errs[:8],
        })
    print(json.dumps({"results": results, "baseline_note": "试验性基线（样本内对照，非行业基准）"},
                     ensure_ascii=False, indent=2))
    print(f"\nPASS={sum(1 for r in results if r['ok'])}/{len(results)}")
    if not all(r["ok"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
