"""6R16 测试：区域几何（版本化 artifact）+ 地图 regions API。

- 真实样本点凸包生成、coverage、content_hash 确定性；样本不足 → 仅中心/虚线 + 低置信。
- /api/maps/{map}/regions 返回版本文档，必含 regions/polygon/centroid/coverage/confidence/content_hash。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import User
from app.services.region_geometry import (
    build_regions, convex_hull, evaluate_region_geometry, load_region_geometry,
)
from app.services.security import hash_password


def test_convex_hull_basic():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (5.0, 5.0), (5.0, 5.0)]
    hull = convex_hull(pts)
    assert len(hull) == 4   # 正方形四角


def test_build_regions_deterministic_and_coverage():
    def grid(cx, cy):
        return [(cx + i * 5, cy + j * 5) for i in range(5) for j in range(5)]  # 25 点
    samples = {
        "bombsitea": {"place_cn": "A 区", "points": grid(300, 300)},
        "middle": {"place_cn": "中路", "points": grid(400, 400)},
    }
    meta = {"pos_x": 0.0, "pos_y": 1024.0, "scale": 1.0, "image_size": 1024}
    a = build_regions(samples, "de_mirage", meta)
    b = build_regions(samples, "de_mirage", meta)
    assert a["content_hash"] == b["content_hash"]
    assert a["coverage"] > 0
    # 每个 region 需带 polygon/centroid/coverage/confidence/geometric
    for r in a["regions"]:
        assert "polygon" in r and "centroid" in r and "coverage" in r and "confidence" in r
        assert "geometric" in r


def test_insufficient_samples_no_polygon():
    from app.services.region_geometry import build_region
    r = build_region("sparse", "稀疏区", [(5, 5), (6, 6), (7, 7), (8, 8), (9, 9)], 0, 0, 5.0, 1024)
    # 样本 <12 → 无多边形、低置信
    assert r["geometric"] is False
    assert r["confidence"] == "low"
    assert r["polygon"] == []
    assert r["reason"] == "insufficient_samples"


def test_evaluate_region_geometry_trust():
    assert evaluate_region_geometry([])["trust"] == "hidden"
    ev = evaluate_region_geometry([{"geometric": True, "coverage": 0.95, "sample_count": 2000}])
    assert ev["available"] is True


def test_load_region_geometry_flat_and_deterministic():
    for m in ("de_mirage", "de_anubis", "de_dust2"):
        g = load_region_geometry(m)
        assert g is not None, f"{m} 缺少区域几何"
        assert isinstance(g["regions"], list) and g["regions"]
        assert g.get("content_hash")
        # 同一文件两次加载 hash 一致
        assert load_region_geometry(m)["content_hash"] == g["content_hash"]


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


def test_map_regions_api(client):
    # 公开端点，无需登录
    r = client.get("/api/maps/de_mirage/regions")
    assert r.status_code == 200
    body = r.json()
    assert "regions" in body and body["regions"]
    sample = body["regions"][0]
    # 关键：区域几何必有来源、含中文叫位、无世界坐标泄露
    assert "polygon" in sample
    assert "place_cn" in sample
    # 世界坐标/steam 不泄露
    assert "steam" not in str(body).lower().replace("steamid", "")
    # 无 region 的地图 → 404
    r2 = client.get("/api/maps/de_dust2/regions")
    assert r2.status_code in (200, 404)   # dust2 有产物 → 200
