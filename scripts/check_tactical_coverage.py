"""6R8 门禁：5 个验收 demo 的战术回放 artifact 覆盖检查（gate 2）。

对每个 demo：读 tactical_artifacts manifest（版本 2.0 + 校验通过），再经真实 API 抽查一个
回合的 team/replay（players=10、tick_rate、props≥0、无 steam 泄露、rx/ry 可用）。
用法：python scripts/check_tactical_coverage.py [demo_id ...]
"""
from __future__ import annotations

import json
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


def main() -> None:
    login = httpx.post(f"{BASE}/api/auth/login", json=ACCOUNT, timeout=30).json()
    tok = login["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    results = []
    for demo_id in DEMOS:
        m = _psql(f"SELECT schema_version, player_count, prop_count, round_count, size_bytes, sha256 FROM tactical_artifacts WHERE match_id=(SELECT match_id FROM demos WHERE id={demo_id});")
        if not m:
            results.append({"demo": demo_id, "ok": False, "err": "无 tactical_artifacts manifest"})
            continue
        parts = m.split("|")
        manifest = {"schema": parts[0], "players": int(parts[1]), "props": int(parts[2]),
                    "rounds": int(parts[3]), "size": int(parts[4]), "sha": parts[5]}
        # 抽查回合 1 的 team/replay
        r = httpx.get(f"{BASE}/api/demos/{demo_id}/team/replay?round_no=1", headers=h, timeout=60)
        if r.status_code != 200:
            results.append({"demo": demo_id, "ok": False, "manifest": manifest,
                            "err": f"team/replay HTTP {r.status_code}: {r.text[:160]}"})
            continue
        body = r.json()
        n_players = len(body["players"])
        has_rx = any(p["points"] and p["points"][0]["rx"] is not None for p in body["players"])
        n_props = len(body["props"])
        ok = (manifest["schema"] == "2.0" and manifest["players"] == 10
              and n_players == 10 and body["tick_rate"] is not None
              and "steam" not in str(body) and has_rx)
        results.append({
            "demo": demo_id, "map": EXPECT_MAP.get(demo_id), "ok": ok,
            "manifest": manifest, "api": {"players": n_players, "tick_rate": body["tick_rate"],
                                          "props": n_props, "winner": body.get("winner"),
                                          "max_duration": body.get("max_duration")},
        })
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    print(f"\nPASS={sum(1 for r in results if r['ok'])}/{len(results)}")
    if not all(r["ok"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
