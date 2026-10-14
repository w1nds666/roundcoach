"""6R8：为已解析 demo 回填战术回放 artifact v2.0（真实朝向 yaw/pitch + 道具生命周期）。

- 只写 MinIO 的 tactical/{match_id}/{schema}/tactical.json.gz + tactical_artifacts manifest
- 输入为 demo 对应本地原文件，由宿主机 .venv 解析（不重复入库/不重建其他表）
- 用法：python scripts/backfill_tactical_artifacts.py [demo_id ...]（默认回填 19-23）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.parser import DemoAnalyzer  # noqa: E402
from app.services.tactical import (  # noqa: E402
    TACTICAL_SCHEMA_VERSION,
    artifact_sha256,
    build_tactical_artifact,
    tactical_object_key,
)
from app.services.routes import gzip_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    19: ("test demo/4614393950359818651_0/4614393950359818651_10000502.dem", "de_mirage"),
    20: ("test demo/4615096941117908123_0/4615096941117908123_10000485.dem", "de_anubis"),
    21: ("test demo/4626848676756337691_0/4626848676756337691_10000489.dem", "de_dust2"),
    22: ("test demo/9209049647259770252_0/9209049647259770252_0.dem", "de_inferno"),
    23: ("test demo/9219534615131736844_0/9219534615131736844_0.dem", "de_ancient"),
}


def _psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo", "-t", "-A", "-c", sql],
        capture_output=True, text=True,
    )
    return out.stdout.strip()


def _upload(key: str, data: bytes, content_type: str) -> None:
    import io

    from minio import Minio

    client = Minio("localhost:9000", access_key="cs2minio", secret_key="cs2minio_secret", secure=False)
    client.put_object("cs2demo", key, io.BytesIO(data), length=len(data), content_type=content_type)


def _get_psql_pairs(sql: str) -> dict[str, int]:
    out = {}
    for line in _psql(sql).splitlines():
        parts = line.split("|")
        if len(parts) == 2:
            out[parts[1]] = int(parts[0])
    return out


def backfill(demo_id: int) -> None:
    item = TARGETS.get(demo_id)
    if item is None:
        print(f"demo {demo_id} 不在已知清单（跳过）", flush=True)
        return
    rel, expect_map = item
    p = ROOT / rel
    if not p.exists():
        print(f"  demo {demo_id} 本地原文件缺失：{rel}", flush=True)
        return
    print(f"=== demo {demo_id} {expect_map} ===", flush=True)

    match_id = _psql(f"SELECT match_id FROM demos WHERE id={demo_id};")
    if not match_id or match_id == "None":
        print(f"  demo {demo_id} 无 match_id（未解析）跳过", flush=True)
        return
    match_id = int(match_id)

    player_id_of = _get_psql_pairs(f"SELECT id, steam_id FROM players WHERE match_id={match_id};")

    a = DemoAnalyzer(str(p))
    header = a.parse_header()
    rounds = a.parse_rounds()
    players = a.parse_player_info()
    deaths = a.parse_deaths()
    grenades = a.parse_grenades()

    identity_of: dict[str, str] = {}
    if rounds:
        for sid, team in a._build_roster(rounds[0]["start_tick"]).items():
            identity_of[sid] = team

    if header.map_name != expect_map:
        print(f"  WARN map mismatch: {header.map_name} != {expect_map}", flush=True)

    artifact = build_tactical_artifact(
        a, match_id, header.map_name, int(header.playout_ticks or 0),
        rounds, players, identity_of, player_id_of, deaths, grenades,
        tick_rate=header.tick_rate,
    )
    data = gzip_json(artifact)
    key = tactical_object_key(match_id)
    _upload(key, data, "application/gzip")
    sha = artifact_sha256(data)

    _psql(
        "INSERT INTO tactical_artifacts (match_id, schema_version, object_key, size_bytes, sha256, player_count, prop_count, round_count) "
        f"VALUES ({match_id}, '{TACTICAL_SCHEMA_VERSION}', '{key}', {len(data)}, '{sha}', {len(player_id_of)}, {artifact.get('prop_count', 0)}, {len(rounds)}) "
        "ON CONFLICT (match_id) DO UPDATE SET schema_version=EXCLUDED.schema_version, "
        "object_key=EXCLUDED.object_key, size_bytes=EXCLUDED.size_bytes, sha256=EXCLUDED.sha256, "
        "player_count=EXCLUDED.player_count, prop_count=EXCLUDED.prop_count, round_count=EXCLUDED.round_count;"
    )
    print(f"  OK match={match_id} rounds={len(rounds)} players={len(player_id_of)} "
          f"props={artifact.get('prop_count', 0)} key={key} sha={sha[:12]}", flush=True)


def main() -> None:
    targets = [int(x) for x in sys.argv[1:]] or [19, 20, 21, 22, 23]
    for demo_id in targets:
        backfill(demo_id)
    print("done")


if __name__ == "__main__":
    main()
