"""Standard CS2 defusal-map asset gate: official local extraction, fail-closed API.

Map geometry and radar assets do not imply a real demo replay exists for every map.
"""
import hashlib
import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.navmesh import load_manifest, load_navmesh_artifact

BACKEND = Path(__file__).resolve().parent.parent
MAPS = (
    "de_ancient", "de_anubis", "de_cache", "de_dust2", "de_inferno",
    "de_mirage", "de_nuke", "de_overpass", "de_train", "de_vertigo",
)
LAYERED = {"de_nuke": -495.0, "de_train": -50.0, "de_vertigo": 11700.0}


@pytest.mark.parametrize("map_name", MAPS)
def test_official_navmesh_matches_manifest_and_public_route(map_name):
    manifest = load_manifest()
    assert manifest is not None
    entry = manifest["maps"][map_name]
    artifact = load_navmesh_artifact(map_name)
    assert artifact is not None
    assert hashlib.sha256(artifact["bytes"]).hexdigest() == entry["artifact_sha256"]
    body = json.loads(artifact["bytes"])
    assert body["source"]["map"] == map_name
    assert body["source"]["sha256"] == entry["nav_sha256"]
    assert body["stats"]["deterministicHash"] == entry["stats"]["deterministic_hash"]
    with TestClient(app) as client:
        response = client.get(f"/api/maps/{map_name}/navmesh")
    assert response.status_code == 200
    assert response.content == artifact["bytes"]


@pytest.mark.parametrize("map_name", MAPS)
def test_radar_and_transform_are_real_and_complete(map_name):
    config = json.loads((BACKEND / "config" / "maps.json").read_text(encoding="utf-8"))["maps"]
    assert all(key in config[map_name] for key in ("pos_x", "pos_y", "scale"))
    with TestClient(app) as client:
        response = client.get(f"/api/maps/{map_name}/radar")
        meta = client.get(f"/api/maps/{map_name}")
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", response.content[16:24])
    assert width == height and width >= 1024
    assert meta.status_code == 200
    assert meta.json()["lower_altitude_below"] == LAYERED.get(map_name)
    bounds = json.loads((BACKEND / "config" / "navmesh" / f"{map_name}.json").read_text(encoding="utf-8"))["bounds"]
    transform = config[map_name]
    normalized = (
        (bounds["minX"] - transform["pos_x"]) / transform["scale"] / 1024,
        (bounds["maxX"] - transform["pos_x"]) / transform["scale"] / 1024,
        (transform["pos_y"] - bounds["maxY"]) / transform["scale"] / 1024,
        (transform["pos_y"] - bounds["minY"]) / transform["scale"] / 1024,
    )
    assert all(0 <= coordinate <= 1 for coordinate in normalized), normalized


@pytest.mark.parametrize("map_name,threshold", LAYERED.items())
def test_layered_map_has_matching_lower_radar(map_name, threshold):
    with TestClient(app) as client:
        upper = client.get(f"/api/maps/{map_name}/radar")
        lower = client.get(f"/api/maps/{map_name}/radar?layer=lower")
        meta = client.get(f"/api/maps/{map_name}")
    assert upper.status_code == lower.status_code == 200
    assert upper.content != lower.content
    assert meta.json()["lower_altitude_below"] == threshold


def test_unknown_map_and_invalid_layer_fail_closed():
    with TestClient(app) as client:
        assert client.get("/api/maps/de_boulder/navmesh").status_code == 404
        assert client.get("/api/maps/de_cache/radar?layer=lower").status_code == 404
        assert client.get("/api/maps/de_nuke/radar?layer=../../etc").status_code == 404
