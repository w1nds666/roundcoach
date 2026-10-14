"""6R16：对 demo19-23 用新引擎重算 coordination + 区域状态机，验证 content_hash 确定性。

- 读 MinIO tactical v3.0 + DB（players/events），两次运行 build_coordination_analysis，比对 content_hash。
- 输出：spacing 分母（应为 episode 段数而非采样点）、area_control 机会（带正向信号 controlled 段）、
  isolated 分母（排除最后存活/保枪）、ev_count（应显著低于 40）、conclusions 分布。
- 写 docs/phase6r16-recompute-raw.json（证据）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from minio import Minio  # noqa: E402

import app.services.coordination as C  # noqa: E402
from app.services.routes import gunzip_json  # noqa: E402
from app.services.region_geometry import load_region_geometry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCKER = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe"
DEMOS = {19: (59, "de_mirage"), 20: (60, "de_anubis"), 21: (61, "de_dust2"),
         22: (62, "de_inferno"), 23: (63, "de_ancient")}
SCHEMA = "3.0"


def _psql_rows(sql: str) -> list[list[str]]:
    out = subprocess.run([DOCKER, "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo",
                          "-t", "-A", "-F", "|", "-c", sql],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [line.split("|") for line in out.stdout.splitlines() if line.strip()]


def _load(demo_id: int) -> dict | None:
    match_id, map_name = DEMOS[demo_id]
    client = Minio("localhost:9000", access_key="cs2minio", secret_key="cs2minio_secret", secure=False)
    try:
        data = client.get_object("cs2demo", f"tactical/{match_id}/{SCHEMA}/tactical.json.gz").read()
    except Exception as e:  # noqa: BLE001
        print(f"  demo {demo_id}: 读 artifact 失败 {e}", flush=True)
        return None
    artifact = gunzip_json(data)
    players = _psql_rows(f"SELECT id, steam_id, name, team_identity FROM players WHERE match_id={match_id};")
    id_of = {r[1]: int(r[0]) for r in players}
    name_of = {int(r[0]): r[2] for r in players}
    team_of = {int(r[0]): r[3] for r in players}
    kills = []
    for r in _psql_rows(f"SELECT round, tick, actor_steam, victim_steam, actor_x, actor_y, victim_x, victim_y "
                        f"FROM events WHERE match_id={match_id} AND event_type='kill';"):
        def _f(x):
            try:
                return float(x) if x not in ("", "None", None) else None
            except Exception:
                return None
        kills.append({"round": int(r[0]), "tick": int(r[1]), "actor_steam": r[2], "victim_steam": r[3],
                      "actor_x": _f(r[4]), "actor_y": _f(r[5]), "victim_x": _f(r[6]), "victim_y": _f(r[7]),
                      "actor_name": name_of.get(id_of.get(r[2], 0), (r[2] or "")[:8]),
                      "victim_name": name_of.get(id_of.get(r[3] or "", 0), (r[3] or "")[:8])})
    return {"artifact": artifact, "map_name": map_name, "id_of": id_of, "name_of": name_of,
            "team_of": team_of, "kills": kills, "deaths": [dict(k) for k in kills]}


def _run(snap):
    return C.build_coordination_analysis(
        snap["artifact"]["rounds"], snap["map_name"], snap["artifact"].get("tick_rate"),
        snap["kills"], snap["deaths"], snap["id_of"], snap["name_of"], snap["team_of"],
        region_geometry={snap["map_name"]: load_region_geometry(snap["map_name"])})


def main():
    report = {"schema_version": C.COORDINATION_SCHEMA_VERSION, "demos": {}}
    ok_all = True
    for demo_id, (match_id, map_name) in sorted(DEMOS.items()):
        snap = _load(demo_id)
        if not snap:
            ok_all = False
            continue
        a = _run(snap)
        b = _run(snap)
        same = a["content_hash"] == b["content_hash"]
        if not same:
            ok_all = False
        tech = {}
        for t in a["teams"]:
            for m in t["metrics"]:
                if m["metric_id"] in ("spacing", "area_control", "isolated_death", "utility_assisted_entry", "trade"):
                    tech.setdefault(m["metric_id"], {})[t["team"]] = {
                        "opp": m["opportunities"], "conv": m["converted"],
                        "rate": m["rate"], "confidence": m["confidence"], "applicable": m["applicable"]}
        report["demos"][demo_id] = {
            "map": map_name, "hash_deterministic": same, "content_hash": a["content_hash"],
            "ev_count": len(a["rounds"]), "conc_count": len(a["conclusions"]),
            "metrics": tech,
            "conc_tones": {c["metric_id"]: c["tone"] for c in a["conclusions"]},
            "conc_titles": [c["title"] for c in a["conclusions"]],
            "area_state_rounds": len(a.get("area_state_machine", [])),
        }
        print(f"\n### demo {demo_id} {map_name} hash_same={same} ev={len(a['rounds'])} "
              f"area_state_rounds={len(a.get('area_state_machine', []))}", flush=True)
        print(json.dumps(tech, ensure_ascii=False, indent=2), flush=True)
        for c in a["conclusions"]:
            print(f"  [{c['tone']}] {c['metric_id']}: {c['title']}", flush=True)
    report["all_deterministic"] = ok_all
    out = ROOT / "docs" / "phase6r16-recompute-raw.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nall_deterministic={ok_all}\nwritten {out}", flush=True)


if __name__ == "__main__":
    main()
