"""6R9 标定：对 demo 19-23 真实数据做阈值敏感性分析，记录选定理由（写 docs/phase6r9-calibration.md）。

- 数据：经 MinIO（战术 artifact v2.0）+ DB（docker psql，players/events），不重复解析 .dem。
- 对每个指标的可调阈值做「变体 sweep」，观察机会数/率/时长如何变化，记录稳定性与合理性。
- 只读，不改数据库、不写 artifact；结果用于文档论证阈值选择。
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from minio import Minio  # noqa: E402

import app.services.coordination as C  # noqa: E402
from app.services.routes import gunzip_json  # noqa: E402
from app.services.coordination_thresholds import VERSION as THRESH_VERSION  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCKER = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe"
DEMOS = [19, 20, 21, 22, 23]
EXPECT_MAP = {19: "de_mirage", 20: "de_anubis", 21: "de_dust2", 22: "de_inferno", 23: "de_ancient"}


def _psql_rows(sql: str) -> list[list[str]]:
    out = subprocess.run([DOCKER, "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo", "-t", "-A", "-F", "|", "-c", sql],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = []
    for line in out.stdout.splitlines():
        if line.strip():
            rows.append(line.split("|"))
    return rows


def _load_demo_data(demo_id: int) -> dict | None:
    mid = _psql_rows(f"SELECT match_id FROM demos WHERE id={demo_id};")
    if not mid:
        return None
    match_id = int(mid[0][0])
    key = _psql_rows(f"SELECT object_key FROM tactical_artifacts WHERE match_id={match_id} AND schema_version='2.0';")
    if not key:
        return None
    client = Minio("localhost:9000", access_key="cs2minio", secret_key="cs2minio_secret", secure=False)
    obj = client.get_object("cs2demo", key[0][0])
    data = obj.read()
    artifact = gunzip_json(data)

    players = _psql_rows(f"SELECT id, steam_id, name, team_identity FROM players WHERE match_id={match_id};")
    id_of = {r[1]: int(r[0]) for r in players}
    name_of = {int(r[0]): r[2] for r in players}
    team_of = {int(r[0]): r[3] for r in players}
    kills = []
    for r in _psql_rows(f"SELECT round, tick, actor_steam, victim_steam, actor_x, actor_y, victim_x, victim_y FROM events WHERE match_id={match_id} AND event_type='kill';"):
        def _f(x):
            try:
                return float(x) if x not in ("", "None", None) else None
            except Exception:
                return None
        kills.append({"round": int(r[0]), "tick": int(r[1]), "actor_steam": r[2], "victim_steam": r[3],
                      "actor_x": _f(r[4]), "actor_y": _f(r[5]), "victim_x": _f(r[6]), "victim_y": _f(r[7]),
                      "actor_name": name_of.get(id_of.get(r[2], 0), r[2][:8]),
                      "victim_name": name_of.get(id_of.get(r[3] or "", 0), (r[3] or "")[:8])})
    deaths = [dict(k) for k in kills]
    return {"artifact": artifact, "map": EXPECT_MAP.get(demo_id), "id_of": id_of,
            "name_of": name_of, "team_of": team_of, "kills": kills, "deaths": deaths, "match_id": match_id}


def _run(demo_snapshot, **overrides):
    # 重置到默认再覆盖（monkeypatch coordination 模块全局，运行时解析）
    _reset_defaults()
    old = {}
    for name, val in overrides.items():
        old[name] = getattr(C, name)
        setattr(C, name, val)
    try:
        d = demo_snapshot
        return C.build_coordination_analysis(
            d["artifact"]["rounds"], d["map"], d["artifact"].get("tick_rate"),
            d["kills"], d["deaths"], d["id_of"], d["name_of"], d["team_of"], focus_player_id=None)
    finally:
        for name, val in old.items():
            setattr(C, name, val)


_DEFAULTS = {
    "TRADE_WINDOW_SEC": 5.0, "SIMULT_WINDOW_SEC": 3.0, "CLOSE_SPACING_WORLD": 600.0,
    "CROSSFIRE_ANGLE_DEG": 40.0, "CROSSFIRE_RANGE_WORLD": 1400.0, "UTILITY_WINDOW_SEC": 6.0,
    "UTILITY_ENTRY_RANGE_WORLD": 1400.0, "ISOLATED_SUPPORT_DIST": 900.0, "AREA_MIN_OCCUPANCY_SEC": 10.0,
}


def _reset_defaults():
    for name, val in _DEFAULTS.items():
        setattr(C, name, val)


def _rate_for(out, team, metric):
    t = next((x for x in out["teams"] if x["team"] == team), None)
    if not t:
        return None
    m = next((x for x in t["metrics"] if x["metric_id"] == metric), None)
    return m


def _sweep(label, metric, team, value_set, run):
    rows = []
    for v in value_set:
        out = run(v)
        m = _rate_for(out, team, metric)
        rows.append((v, m["opportunities"], m["converted"], m["rate"], m["confidence"]))
    return {"label": label, "metric": metric, "team": team, "rows": rows}


def summarize(row):
    return {"value": row[0], "opp": row[1], "conv": row[2], "rate": row[3], "conf": row[4]}


def main():
    snapshots = {}
    for d in DEMOS:
        snap = _load_demo_data(d)
        if snap:
            snapshots[d] = snap
        else:
            print(f"demo {d}: 无数据（跳过）")
    if not snapshots:
        print("无可用 demo，标定失败")
        sys.exit(1)

    demo = DEMOS[0]
    snap = snapshots[demo]
    report = {"thresholds_version": THRESH_VERSION, "demo_used": demo, "map": snap["map"], "sweeps": []}

    def _mkrun(name, values):
        return lambda v: _run(snap, **{name: v})

    # trade / simult / utility 时间窗
    report["sweeps"].append(_sweep("trade 窗口(s)", "trade", "A", [2, 3, 5, 8], _mkrun("TRADE_WINDOW_SEC", [2, 3, 5, 8])))
    report["sweeps"].append(_sweep("simult 窗口(s)", "simultaneous_contact", "A", [2, 3, 5], _mkrun("SIMULT_WINDOW_SEC", [2, 3, 5])))
    report["sweeps"].append(_sweep("utility 窗口(s)", "utility_assisted_entry", "A", [3, 6, 9], _mkrun("UTILITY_WINDOW_SEC", [3, 6, 9])))
    # 距离类
    report["sweeps"].append(_sweep("spacing 距离", "spacing", "A", [400, 600, 750, 900], _mkrun("CLOSE_SPACING_WORLD", [400, 600, 750, 900])))
    report["sweeps"].append(_sweep("isolated 距离", "isolated_death", "A", [600, 750, 900], _mkrun("ISOLATED_SUPPORT_DIST", [600, 750, 900])))
    report["sweeps"].append(_sweep("crossfire 夹角(°)", "crossfire_candidate", "A", [30, 40, 50], _mkrun("CROSSFIRE_ANGLE_DEG", [30, 40, 50])))
    report["sweeps"].append(_sweep("area 最小控区(s)", "area_control", "A", [5, 10, 15], _mkrun("AREA_MIN_OCCUPANCY_SEC", [5, 10, 15])))

    # 五场默认基线汇总
    summary = []
    for d, snap in snapshots.items():
        out = _run(snap)
        row = {"demo": d, "map": snap["map"]}
        for team in ("A", "B"):
            for metric in ("trade", "isolated_death", "spacing", "utility_assisted_entry", "area_control", "crossfire_candidate"):
                m = _rate_for(out, team, metric)
                row[f"{team}.{metric}"] = m["rate"]
        summary.append(row)
    report["default_baseline"] = summary

    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 落盘
    out_file = ROOT / "docs" / "phase6r9-calibration-data.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten {out_file}")


if __name__ == "__main__":
    main()
