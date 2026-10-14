"""6R16 字段勘探：demo19-23 战术 artifact v3.0 与当前 coordination 输出审计。

只读，不写库。读取 MinIO tactical/{match_id}/3.0/tactical.json.gz + DB，计算：
- 每字段 authority / 覆盖 / 缺失 / 反例（last_place_name、yaw、props、killfeed、blind、he、shoots、phase）
- 当前 coordination 引擎对 demo19-23 的逐指标机会/转化/applicable/rate/coverage/trust/confidence
- 当前结论（conclusions）与 evidence 的反例扫描（出生点/保枪/区域=回合胜利/零机会/重复）

用法：backend/.venv/Scripts/python.exe scripts/explore_6r16_fields.py
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
from app.services.coordination_thresholds import VERSION as THRESH_VERSION  # noqa: E402
from app.services.routes import gunzip_json  # noqa: E402
from app.services.places import translate_place  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCKER = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe"
# demo_id -> (match_id, map)
DEMOS = {19: (59, "de_mirage"), 20: (60, "de_anubis"), 21: (61, "de_dust2"),
         22: (62, "de_inferno"), 23: (63, "de_ancient")}
SCHEMA = "3.0"


def _psql_rows(sql: str) -> list[list[str]]:
    out = subprocess.run([DOCKER, "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo",
                          "-t", "-A", "-F", "|", "-c", sql],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [line.split("|") for line in out.stdout.splitlines() if line.strip()]


def _load(demo_id: int) -> dict:
    match_id, map_name = DEMOS[demo_id]
    client = Minio("localhost:9000", access_key="cs2minio", secret_key="cs2minio_secret", secure=False)
    key = f"tactical/{match_id}/{SCHEMA}/tactical.json.gz"
    data = client.get_object("cs2demo", key).read()
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
            "team_of": team_of, "kills": kills, "deaths": [dict(k) for k in kills], "match_id": match_id}


def _analyze_demo_fields(snap: dict) -> dict:
    art = snap["artifact"]
    rounds = art.get("rounds", [])
    tick_rate = art.get("tick_rate")
    map_name = snap["map_name"]
    seen_places: set[str] = set()
    total_points = 0
    points_with_yaw = 0
    points_with_place = 0
    props = 0
    prop_no_thrower = 0
    props_by_type = {}
    prop_authority = {}
    shoots = 0
    killfeed = 0
    blinds = 0
    he_damage = 0
    phase_counts: dict[str, int] = {}
    freeze_samples = 0
    spawn_region_points = 0
    for r in rounds:
        props += len(r.get("props", []))
        for p in r.get("props", []):
            if p.get("thrower_player_id") is None:
                prop_no_thrower += 1
            props_by_type[p.get("type")] = props_by_type.get(p.get("type"), 0) + 1
            a = p.get("authority", {})
            ak = a.get("kind", "?") + ":" + a.get("source", "?") if a else "none"
            prop_authority[ak] = prop_authority.get(ak, 0) + 1
        shoots += len(r.get("shoots", []))
        killfeed += len(r.get("killfeed", []))
        blinds += len(r.get("blinds", []))
        he_damage += len(r.get("he_damage", []))
        freeze = r.get("freeze_end_tick") or r.get("start_tick")
        end = r.get("end_tick")
        for pid, pd in r.get("players", {}).items():
            for pt in pd.get("points", []):
                total_points += 1
                if pt.get("yaw") is not None:
                    points_with_yaw += 1
                if pt.get("place"):
                    seen_places.add(str(pt["place"]))
                    points_with_place += 1
                    if translate_place(map_name, str(pt["place"])) != "未知区域":
                        pass
                # phase bounds
                tick = pt.get("tick")
                if tick_rate and freeze is not None:
                    if tick < freeze:
                        freeze_samples += 1
                        if str(pt.get("place") or "") in ("CTSpawn", "TSpawn", "CT_Spawn", "T_Spawn", "Spawn"):
                            spawn_region_points += 1
    return {"rounds": len(rounds), "total_points": total_points, "points_with_yaw": points_with_yaw,
            "yaw_frac": round(points_with_yaw / total_points, 3) if total_points else 0.0,
            "points_with_place": points_with_place, "place_frac": round(points_with_place / total_points, 3) if total_points else 0.0,
            "unmapped_places": sorted({p for p in seen_places if translate_place(map_name, p) == "未知区域"})[:30],
            "n_places": len(seen_places), "props": props, "prop_no_thrower": prop_no_thrower,
            "props_by_type": props_by_type, "prop_authority": prop_authority,
            "shoots": shoots, "killfeed": killfeed, "blinds": blinds, "he_damage": he_damage,
            "map_name": map_name, "tick_rate": tick_rate}


def _analyze_coordination(snap: dict) -> dict:
    out = C.build_coordination_analysis(
        snap["artifact"]["rounds"], snap["map_name"], snap["artifact"].get("tick_rate"),
        snap["kills"], snap["deaths"], snap["id_of"], snap["name_of"], snap["team_of"])
    return out


def main():
    report = {"thresholds_version": THRESH_VERSION, "schema": SCHEMA, "demos": {}}
    for demo_id, (match_id, map_name) in sorted(DEMOS.items()):
        snap = _load(demo_id)
        fields = _analyze_demo_fields(snap)
        coord = _analyze_coordination(snap)
        teams_metrics = {}
        for t in coord["teams"]:
            for m in t["metrics"]:
                teams_metrics.setdefault(m["metric_id"], {}).setdefault(t["team"], {
                    "opp": m["opportunities"], "conv": m["converted"], "rate": m["rate"],
                    "applicable": m["applicable"], "trust": m["trust"], "confidence": m["confidence"],
                    "coverage": m["coverage"], "human": m["human"]})
        report["demos"][demo_id] = {"match_id": match_id, "map_name": map_name,
                                    "fields": fields, "coordination": teams_metrics,
                                    "conc_count": len(coord["conclusions"]),
                                    "conc": [{"metric": c["metric_id"], "tone": c["tone"], "title": c["title"]} for c in coord["conclusions"]],
                                    "ev_count": len(coord["rounds"])}
        print(f"\n######## demo {demo_id} {map_name} ########", flush=True)
        print(json.dumps({"fields": fields}, ensure_ascii=False, indent=2), flush=True)
        print("--- coordination metrics (A/B) ---", flush=True)
        print(json.dumps(teams_metrics, ensure_ascii=False, indent=2), flush=True)
        print("--- conclusions ---", flush=True)
        for c in coord["conclusions"]:
            print(f"  [{c['tone']}] {c['metric_id']}: {c['title']}", flush=True)
    out_file = ROOT / "docs" / "phase6r16-audit-raw.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten {out_file}", flush=True)


if __name__ == "__main__":
    main()
