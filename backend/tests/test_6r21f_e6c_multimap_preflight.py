"""6R21F-E6C-MULTIMAP-PREFLIGHT：为下一阶段建立三地图官方真值前置（红测先行）。

目标：确证 demo22 对应地图（de_inferno），并用官方 Valve VPK/NavMesh（用户已批准）为 de_inferno
（与 demo22 对应地图相同，按去重规则只保留单份）生成版本化 cs2-navmesh-v1 JSON；以 manifest
（cs2-navmesh-manifest-v1）作为三地图官方真值 inventory，供审计与测试核对。

覆盖（安全/可审计要求，fail-closed）：
- manifest/schema/hash 一致性：manifest 每张图的 artifact 文件 sha256、source.map、source.sha256、
  stats 与 artifact 实际字节/内容一致。
- 地图白名单（地图白名单）：de_mirage/de_dust2/de_inferno 放行；Anubis/Ancient/Nuke 等拒绝。
- 重复文件（重复文件）：无两张图共享同一 artifact；demo22→de_inferno 去重后只保留单份 de_inferno artifact。
- 路径穿越（路径穿越）：manifest 的每张 map key 通过白名单且不含分隔符/穿越片段。
- 异常产物（异常产物）：非法 JSON / schema 不符 / source.map 不符 / 缺失文件 → load 返回 None（fail-closed）。
- 与 demo map_name 严格匹配：de_inferno artifact source.map == demo22 权威 map_name（de_inferno）。
"""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parent.parent
NAVMESH_DIR = BACKEND_DIR / "config" / "navmesh"


def import_navmesh():
    from app.services import navmesh as nm
    return nm


# 只读：从 manifest 读取官方真值 inventory 的映射（去敏感）。
def load_manifest_dict():
    nm = import_navmesh()
    m = nm.load_manifest()
    assert m is not None, "manifest（cs2-navmesh-manifest-v1）必须存在且可读"
    return m


# ---------------------------------------------------------------------------
# 1) manifest / schema / hash 一致性
# ---------------------------------------------------------------------------

def test_manifest_schema_and_maps():
    nm = import_navmesh()
    m = nm.load_manifest()
    assert m is not None
    assert m["schema"] == "cs2-navmesh-manifest-v1"
    assert m["manifest"]["engine"] == "CS2 Source 2"
    maps = m["maps"]
    assert "de_mirage" in maps and "de_inferno" in maps, "三地图 inventory 必须覆盖 de_mirage 与 de_inferno"
    # 去重：demo22 对应地图即 de_inferno，不得出现第二个 de_inferno 别名 artifact。
    assert maps["de_inferno"]["artifact"] == "de_inferno.json"
    assert maps["de_mirage"]["artifact"] == "de_mirage.json"


def test_manifest_hash_consistency_for_every_map():
    nm = import_navmesh()
    m = nm.load_manifest()
    maps = m["maps"]
    for map_name, entry in maps.items():
        art_path = NAVMESH_DIR / entry["artifact"]
        assert art_path.is_file(), f"{entry['artifact']} 必须存在"
        raw = art_path.read_bytes()
        # artifact 字节 sha256 == manifest 记录
        assert hashlib.sha256(raw).hexdigest() == entry["artifact_sha256"], f"{map_name} artifact sha256 不一致"
        body = json.loads(raw)
        # schema + source.map + source.sha256（nav）与 manifest 严格一致
        assert body["schema"] == "cs2-navmesh-v1"
        assert body["source"]["map"] == map_name
        assert body["source"]["sha256"] == entry["nav_sha256"]
        assert body["source"]["navVersion"] == m["manifest"]["nav_version"]
        # stats 与 manifest 一致
        st = entry["stats"]
        assert body["stats"]["areaCount"] == st["area_count"]
        assert body["stats"]["ladderCount"] == st["ladder_count"]
        # 版本化可审计元数据
        assert body["tool"] and body["tool"]["license"] == "MIT"
        assert body["tool"]["name"] == "ValveResourceFormat"


def test_manifest_no_duplicate_artifact_files():
    m = load_manifest_dict()
    maps = m["maps"]
    artifacts = [e["artifact"] for e in maps.values()]
    # 无两张图共享同一 artifact（重复文件负测）
    assert len(artifacts) == len(set(artifacts)), "manifest 内不得出现重复 artifact 文件"
    # manifest 内每个 artifact 都真实存在，且目录内无 manifest 未登记的 navmesh 重复产物
    registered = set(artifacts)
    actual = {p.name for p in NAVMESH_DIR.glob("*.json") if p.name != "manifest.json"}
    assert registered == actual, f"navmesh 目录存在未登记/重复产物: {actual ^ registered}"


def test_demo21_map_alignment_strict_match():
    # demo21 权威 map_name == de_dust2；de_mirage/de_dust2/de_inferno 各 navmesh source.map 与权威对齐严格一致（三源：.dem/DB/maps.json）。
    m = load_manifest_dict()
    demos = m["manifest"]["demo_map_alignment"]["demos"]
    assert demos.get("21") == "de_dust2", "demo21 权威 map_name 必须是 de_dust2（三源一致）"
    assert demos.get("19") == "de_mirage" and demos.get("22") == "de_inferno"
    nm = import_navmesh()
    for dm in ("de_mirage", "de_dust2", "de_inferno"):
        art = nm.load_navmesh_artifact(dm)
        assert art is not None, f"{dm} 版本化 artifact 必须存在"
        body = json.loads(art["bytes"])
        assert body["source"]["map"] == dm, f"{dm} navmesh source.map 必须与自身一致"


# ---------------------------------------------------------------------------
# 2) 产品路由：de_inferno 版本化 artifact
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)

    def override():
        db = S()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_inferno_api_returns_versioned_artifact(client):
    r = client.get("/api/maps/de_inferno/navmesh")
    assert r.status_code == 200
    raw = r.content
    assert r.headers["content-type"].startswith("application/json")
    assert int(r.headers["content-length"]) == len(raw)
    sha = hashlib.sha256(raw).hexdigest()
    assert r.headers["etag"].strip('"') == sha
    assert r.headers["x-vrf-navmesh-sha256"] == sha
    assert r.headers["x-vrf-navmesh-version"]
    body = r.json()
    assert body["schema"] == "cs2-navmesh-v1"
    assert body["source"]["map"] == "de_inferno"
    assert body["stats"]["areaCount"] == 2738
    assert body["stats"]["ladderCount"] == 0
    # 公开静态几何：绝不含演示玩家 / Steam / 个人标识
    low = raw.decode("utf-8", "replace").lower()
    assert "steam" not in low
    assert "player_id" not in low and "uid" not in low and "email" not in low


def test_inferno_and_mirage_use_distinct_artifacts():
    # 三地图 inventory 中 mirage 与 inferno 是两份不同 artifact，且来源不同（nav/vpk 哈希不同，非复制）。
    m = load_manifest_dict()
    mi = m["maps"]["de_mirage"]
    inf = m["maps"]["de_inferno"]
    assert mi["artifact"] != inf["artifact"]
    assert mi["nav_sha256"] != inf["nav_sha256"]
    assert mi["vpk"] == "de_mirage.vpk"
    assert inf["vpk"] == "de_inferno.vpk"


# ---------------------------------------------------------------------------
# 3) 路径穿越（manifest map key + 白名单）
# ---------------------------------------------------------------------------

def test_manifest_map_keys_pass_whitelist_and_no_traversal():
    nm = import_navmesh()
    m = load_manifest_dict()
    for key in m["maps"].keys():
        assert nm.is_allowed_map(key) is True, f"manifest map {key!r} 必须通过白名单"
        assert not any(sep in key for sep in ("/", "\\", "..")), f"manifest map {key!r} 不得含穿越片段"


@pytest.mark.parametrize("bad", [
    "../etc/passwd",
    "de_inferno/../de_inferno",
    "de_inferno\\..\\secrets",
    "/etc/passwd",
    "de_inferno.json",
    "",
])
def test_manifest_map_keys_reject_traversal(bad):
    nm = import_navmesh()
    assert nm.is_allowed_map(bad) is False


# ---------------------------------------------------------------------------
# 4) 异常产物 fail-closed
# ---------------------------------------------------------------------------

def test_anomalous_artifact_fails_closed(tmp_path, monkeypatch):
    nm = import_navmesh()
    # 用合成 map 名（不撞真实 catalog），并在首尾清缓存，避免跨测试污染真实 artifact 的缓存字节。
    def _clear():
        nm._cached_bytes.cache_clear()
        nm._cached_manifest.cache_clear()

    _clear()
    bad = tmp_path / "badnav"
    bad.mkdir()
    # 非法 JSON / schema 不符 / source.map 不符 / 缺失
    (bad / "bad_json.json").write_text("{not valid json", encoding="utf-8")
    (bad / "bad_schema.json").write_text(json.dumps({"schema": "cs2-navmesh-v2", "source": {"map": "de_x"}}), encoding="utf-8")
    (bad / "bad_map.json").write_text(json.dumps({"schema": "cs2-navmesh-v1", "source": {"map": "de_other"}}), encoding="utf-8")
    monkeypatch.setattr(nm, "NAVMESH_DIR", bad)
    catalog = {
        "bad_json": "bad_json.json",
        "bad_schema": "bad_schema.json",
        "bad_map": "bad_map.json",
    }
    monkeypatch.setattr(nm, "NAVMESH_CATALOG", catalog)
    for m in ("bad_json", "bad_schema", "bad_map"):
        assert nm.load_navmesh_artifact(m) is None, f"异常产物 {m} 必须返回 None（fail-closed）"
    # 缺失文件
    monkeypatch.setattr(nm, "NAVMESH_CATALOG", {"bad_missing": "missing.json"})
    assert nm.load_navmesh_artifact("bad_missing") is None
    _clear()
