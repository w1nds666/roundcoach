"""6R21F-E6B-NAVMESH-PRODUCT-GATE：受控后端产品路由 /api/maps/{map}/navmesh（红测先行）。

目标：消除 MapControlReplay/Report 对 Vite dev-only `/__dev__/vrf-navmesh` 路由与 repo 外
环境变量（VRF_NAVMESH_JSON）的产品依赖，改为由真实 nginx:80 产品链路经后端稳定取得
Valve 官方提取、版本化、可审计的 Mirage NavMesh。

覆盖（既有安全/可审计要求）：
- MIME（application/json）、Content-Length、ETag/sha256、缓存头、版本头。
- 404（未知地图 / 无 artifact）、非法 map（含路径分隔符或“..”片段、空 / 超额段）、路径穿越（fail-closed）。
- 未授权策略：端点公开（static 几何），未认证亦 200；响应不含任何演示玩家 / Steam / VRF 引擎信息外的敏感数据。

注意：这些测试先写（red）——`app.services.navmesh` 模块与 `/api/maps/{map}/navmesh` 端点尚不存在，
`config/navmesh/de_mirage.json` 版本化 artifact 亦未加入仓库，故导入即红。
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


# ---------------------------------------------------------------------------
# 1) 服务级：加载 / 校验 / fail-closed
# ---------------------------------------------------------------------------

def import_navmesh():
    # 目标模块尚不存在时让测试干净地红（导入错误），实现后转绿。
    from app.services import navmesh as nm
    return nm


def test_is_allowed_map_allowlist_only():
    nm = import_navmesh()
    # 当前产品提供官方提取、版本化、可审计的 Mirage / Dust2 / Inferno NavMesh（6R21F-E6C 新增 Inferno，E6E 新增 Dust2）。
    assert nm.is_allowed_map("de_mirage") is True
    assert nm.is_allowed_map("de_inferno") is True
    assert nm.is_allowed_map("de_dust2") is True
    # 已由本机官方 VPK/NavMesh 扩展的标准对战地图。
    for m in ("de_ancient", "de_anubis", "de_cache", "de_nuke", "de_overpass", "de_train", "de_vertigo"):
        assert nm.is_allowed_map(m) is True
    assert nm.is_allowed_map("de_boulder") is False


@pytest.mark.parametrize("bad", [
    "../etc/passwd",
    "..",
    "de_mirage/../de_mirage",
    "de_mirage\\..\\secrets",
    "/etc/passwd",
    "C:\\secrets\\x",
    "..%2f..%2fetc",
    "de_mirage.json",
    "",
])
def test_is_allowed_map_rejects_traversal_and_illegal(bad):
    nm = import_navmesh()
    assert nm.is_allowed_map(bad) is False, f"{bad!r} 必须被白名单/分隔符拒绝"


def test_navmesh_path_resolves_inside_dir():
    nm = import_navmesh()
    p = nm.navmesh_path("de_mirage")
    assert p.is_absolute()
    assert str(p).startswith(str(NAVMESH_DIR.resolve()))


def test_load_navmesh_artifact_fail_closed_on_bad_map():
    nm = import_navmesh()
    # 白名单外的地图 / 穿越名 → None（fail-closed，绝不读任意路径）。
    for m in ("de_boulder", "../etc/passwd", "de_mirage/../../secrets"):
        assert nm.load_navmesh_artifact(m) is None


def test_load_navmesh_artifact_dust2_is_versioned_and_auditable():
    nm = import_navmesh()
    art = nm.load_navmesh_artifact("de_dust2")
    assert art is not None, "de_dust2 版本化 artifact 必须存在"
    raw = art["bytes"]
    assert art["schema"] == "cs2-navmesh-v1"
    assert art["nav_version"] == 36
    assert art["source_hash"] and len(art["source_hash"]) == 64
    assert art["tool"] and art["tool"].get("license") == "MIT"
    parsed = json.loads(raw)
    assert parsed["source"]["map"] == "de_dust2"
    assert parsed["stats"]["areaCount"] == 2242
    assert parsed["stats"]["ladderCount"] == 0


def test_load_navmesh_artifact_is_versioned_and_auditable():
    nm = import_navmesh()
    art = nm.load_navmesh_artifact("de_mirage")
    assert art is not None, "de_mirage 版本化 artifact 必须存在"
    raw = art["bytes"]
    # 内嵌 provenance（数据来源/许可证/提取版本记录）保留在 artifact 内，本服务透出供审计。
    assert art["schema"] == "cs2-navmesh-v1"
    assert art["nav_version"] == 36
    assert art["source_hash"] and len(art["source_hash"]) == 64
    assert art["tool"] and art["tool"].get("license") == "MIT"
    assert art["tool"].get("name") == "ValveResourceFormat"
    # 大小 / 哈希必须是真实文件值（读取自仓库内单份 artifact）。
    assert art["size"] == len(raw)
    assert art["sha256"] == hashlib.sha256(raw).hexdigest()
    # 内容与请求 map 一致（审计一致性）。
    parsed = json.loads(raw)
    assert parsed["source"]["map"] == "de_mirage"
    assert parsed["stats"]["areaCount"] == 2544


# ---------------------------------------------------------------------------
# 2) API 集成：/api/maps/{map}/navmesh
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


def test_navmesh_api_returns_versioned_artifact(client):
    # 公开端点（static 几何），无需登录。
    r = client.get("/api/maps/de_mirage/navmesh")
    assert r.status_code == 200
    raw = r.content
    # MIME
    assert r.headers["content-type"].startswith("application/json")
    # size：Content-Length 与真实字节数一致
    assert int(r.headers["content-length"]) == len(raw)
    # hash：ETag 与 X-VRF-NavMesh-SHA256 等于内容 sha256
    sha = hashlib.sha256(raw).hexdigest()
    assert r.headers["etag"].strip('"') == sha
    assert r.headers["x-vrf-navmesh-sha256"] == sha
    # 缓存头 + 版本头
    assert "cache-control" in r.headers and "max-age" in r.headers["cache-control"].lower()
    assert r.headers["x-vrf-navmesh-version"]
    # 内容为 cs2-navmesh-v1（与请求 map 一致）
    body = r.json()
    assert body["schema"] == "cs2-navmesh-v1"
    assert body["source"]["map"] == "de_mirage"
    assert body["stats"]["areaCount"] == 2544
    # 未授权策略：公开静态几何，响应绝不含演示玩家 / Steam / 个人标识
    low = raw.decode("utf-8", "replace").lower()
    assert "steam" not in low
    assert "player_id" not in low and "uid" not in low and "email" not in low


def test_navmesh_api_unknown_and_unprovisioned_map_returns_404(client):
    # 白名单外（社区/未知）地图 → 404，绝不返回内容。
    for m in ("de_boulder", "de_fachwerk", "de_not_a_map"):
        r = client.get(f"/api/maps/{m}/navmesh")
        assert r.status_code == 404, f"{m} 必须 404（未接入）"
        assert "navmesh" in r.text.lower() or "nav" in r.text.lower() or "detail" in r.text.lower()


def test_navmesh_api_dust2_returns_versioned_artifact(client):
    # de_dust2 已接入（E6E）：公开端点返回版本化 cs2-navmesh-v1 artifact。
    r = client.get("/api/maps/de_dust2/navmesh")
    assert r.status_code == 200
    raw = r.content
    assert r.headers["content-type"].startswith("application/json")
    sha = hashlib.sha256(raw).hexdigest()
    assert r.headers["etag"].strip('"') == sha
    assert r.headers["x-vrf-navmesh-sha256"] == sha
    body = r.json()
    assert body["schema"] == "cs2-navmesh-v1"
    assert body["source"]["map"] == "de_dust2"
    assert body["stats"]["areaCount"] == 2242
    low = raw.decode("utf-8", "replace").lower()
    assert "steam" not in low
    assert "player_id" not in low and "uid" not in low and "email" not in low


def test_navmesh_api_rejects_illegal_map_names(client):
    # 非法 map（含路径分隔符/穿越片段）→ 404（fail-closed），绝不读任意文件。
    for path in (
        "/api/maps/../admin/navmesh",
        "/api/maps/de_mirage%2f..%2fnavmesh",
        "/api/maps/..%2f..%2fetc%2fpasswd/navmesh",
        "/api/maps/de_mirage.json/navmesh",
    ):
        r = client.get(path)
        assert r.status_code == 404, f"{path} 必须 404"
        # 绝不把容器内任意文件内容回显（如 passwd/shadow）
        low = r.text.lower()
        assert "root:" not in low and "shadow" not in low


def test_navmesh_api_unauth_policy_is_public_without_user_data(client):
    # 未授权策略：static 几何端点公开（未认证 200），且不泄露任何鉴权后的用户数据。
    r = client.get("/api/maps/de_mirage/navmesh")
    assert r.status_code == 200
    # 控制组：敏感资源仍需鉴权（证明公开 navmesh 不削弱既有门禁）。
    r2 = client.get("/api/demos/19/coordination")
    assert r2.status_code == 401, "未认证请求敏感资源必须 401"
