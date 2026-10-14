"""F5 read-only gate: rebuild demo19-23 tactical artifacts twice and compare hashes.

The verifier never uploads artifacts or updates manifests.  It compares both
fresh builds with the bytes currently stored in MinIO and the database
manifest, then exits non-zero on any mismatch.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from minio import Minio


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.parser import DemoAnalyzer  # noqa: E402
from app.services.routes import gzip_json  # noqa: E402
from app.services.tactical import (  # noqa: E402
    TACTICAL_SCHEMA_VERSION,
    artifact_sha256,
    build_tactical_artifact,
)


TARGETS = {
    19: ("test demo/4614393950359818651_0/4614393950359818651_10000502.dem", "de_mirage"),
    20: ("test demo/4615096941117908123_0/4615096941117908123_10000485.dem", "de_anubis"),
    21: ("test demo/4626848676756337691_0/4626848676756337691_10000489.dem", "de_dust2"),
    22: ("test demo/9209049647259770252_0/9209049647259770252_0.dem", "de_inferno"),
    23: ("test demo/9219534615131736844_0/9219534615131736844_0.dem", "de_ancient"),
}


def _psql(sql: str) -> str:
    result = subprocess.run(
        [
            "docker", "exec", "cs2demo-postgres-1", "psql", "-U", "cs2",
            "-d", "cs2demo", "-t", "-A", "-c", sql,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "psql failed")
    return result.stdout.strip()


def _pairs(sql: str) -> dict[str, int]:
    pairs: dict[str, int] = {}
    for line in _psql(sql).splitlines():
        row_id, steam_id = line.split("|", 1)
        pairs[steam_id] = int(row_id)
    return pairs


def _manifest(demo_id: int) -> dict[str, object]:
    row = _psql(
        "SELECT d.match_id, ta.schema_version, ta.object_key, ta.size_bytes, "
        "ta.sha256, ta.player_count, ta.prop_count, ta.round_count "
        "FROM demos d JOIN tactical_artifacts ta ON ta.match_id=d.match_id "
        f"WHERE d.id={demo_id};"
    )
    if not row:
        raise RuntimeError(f"demo {demo_id}: tactical artifact manifest missing")
    parts = row.split("|")
    return {
        "match_id": int(parts[0]),
        "schema": parts[1],
        "object_key": parts[2],
        "size": int(parts[3]),
        "sha256": parts[4],
        "players": int(parts[5]),
        "props": int(parts[6]),
        "rounds": int(parts[7]),
    }


def _fresh_bytes(demo_id: int, match_id: int) -> tuple[bytes, dict[str, object]]:
    rel_path, expected_map = TARGETS[demo_id]
    demo_path = ROOT / rel_path
    if not demo_path.is_file():
        raise RuntimeError(f"demo {demo_id}: source file missing")

    analyzer = DemoAnalyzer(str(demo_path))
    header = analyzer.parse_header()
    rounds = analyzer.parse_rounds()
    players = analyzer.parse_player_info()
    deaths = analyzer.parse_deaths()
    grenades = analyzer.parse_grenades()
    if header.map_name != expected_map:
        raise RuntimeError(
            f"demo {demo_id}: map mismatch {header.map_name!r} != {expected_map!r}"
        )

    player_id_of = _pairs(
        f"SELECT id, steam_id FROM players WHERE match_id={match_id};"
    )
    identity_of: dict[str, str] = {}
    if rounds:
        identity_of.update(analyzer._build_roster(rounds[0]["start_tick"]))

    artifact = build_tactical_artifact(
        analyzer,
        match_id,
        header.map_name,
        int(header.playout_ticks or 0),
        rounds,
        players,
        identity_of,
        player_id_of,
        deaths,
        grenades,
        tick_rate=header.tick_rate,
    )
    return gzip_json(artifact), {
        "map": header.map_name,
        "rounds": len(rounds),
        "players": len(player_id_of),
        "props": int(artifact.get("prop_count", 0)),
    }


def main() -> int:
    minio = Minio(
        "localhost:9000",
        access_key="cs2minio",
        secret_key="cs2minio_secret",
        secure=False,
    )
    rows: list[dict[str, object]] = []
    all_ok = True

    for demo_id in TARGETS:
        manifest = _manifest(demo_id)
        match_id = int(manifest["match_id"])
        first, meta = _fresh_bytes(demo_id, match_id)
        second, _ = _fresh_bytes(demo_id, match_id)
        stored_response = minio.get_object("cs2demo", str(manifest["object_key"]))
        try:
            stored = stored_response.read()
        finally:
            stored_response.close()
            stored_response.release_conn()

        hashes = {
            "fresh_1": artifact_sha256(first),
            "fresh_2": artifact_sha256(second),
            "stored": artifact_sha256(stored),
            "manifest": str(manifest["sha256"]),
        }
        sizes = {
            "fresh_1": len(first),
            "fresh_2": len(second),
            "stored": len(stored),
            "manifest": int(manifest["size"]),
        }
        ok = (
            len(set(hashes.values())) == 1
            and len(set(sizes.values())) == 1
            and manifest["schema"] == TACTICAL_SCHEMA_VERSION
            and manifest["rounds"] == meta["rounds"]
            and manifest["players"] == meta["players"]
            and manifest["props"] == meta["props"]
        )
        all_ok = all_ok and ok
        rows.append(
            {
                "demo": demo_id,
                "match": match_id,
                **meta,
                "schema": manifest["schema"],
                "sha256": hashes["fresh_1"],
                "size_bytes": sizes["fresh_1"],
                "ok": ok,
            }
        )

    print(json.dumps({"all_deterministic": all_ok, "demos": rows}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
