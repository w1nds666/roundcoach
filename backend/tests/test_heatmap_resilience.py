"""Phase 5B P1 回归：热力图失败隔离 + 5 图底图键名一致性。

- 未知/缺底图地图：解析仍 parsed，热力图警告记录，不整场 failed
- 已支持地图（mirage 等）：四种热力图正常生成（不回归）
- maps.json 键名与底图文件一一对应
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Demo, Match, Player, PlayerStats, User
from app.services.security import hash_password


def _build_scenario(monkeypatch, map_name: str, with_radar_file: bool = True):
    """构造最小任务场景（demo/matches/players/stats + 任务 session patch + fake storage）。"""
    from pathlib import Path

    from app.services import storage as storage_module
    from app.tasks import parse_tasks as tasks_mod

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()
    u = User(email="x@y.com", password_hash=hash_password("password123"), nickname="x")
    db.add(u)
    db.flush()
    d = Demo(user_id=u.id, file_name="a.dem", file_path="demos/a.dem", file_size=1,
             status="uploaded", map_name=map_name)   # match_id 留空：由任务创建
    db.add(d)
    db.commit()
    demo_id = d.id
    db.close()

    # fake storage（热力图上传不落 MinIO）
    class FakeStorage:
        def upload_bytes(self, key, data, content_type="image/png"):
            return key

        def exists(self, key):
            return True

    monkeypatch.setattr(storage_module, "storage", FakeStorage())
    monkeypatch.setattr(tasks_mod, "storage", FakeStorage())
    monkeypatch.setattr(tasks_mod, "SessionLocal", S)

    # 用真实 demo 文件（必须存在；test_demo.dem 已有）替换 file_path
    demo_real = Path(__file__).resolve().parent / "demos" / "test_demo.dem"
    assert demo_real.exists(), "需要 test_demo.dem 作为解析输入"
    db = S()
    d2 = db.get(Demo, demo_id)
    d2.file_path = str(demo_real)
    d2.status = "uploaded"
    db.commit()
    db.close()
    return demo_id, S


def _run_parse(monkeypatch, demo_id, S):
    from app.tasks import parse_tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "SessionLocal", S)
    from app.tasks.parse_tasks import process_demo

    return process_demo(demo_id)


def test_unknown_map_heatmap_failure_does_not_fail_parse(monkeypatch):
    """热力图生成失败（模拟未知地图/配置缺失）：解析仍 parsed + 4 条警告记录。"""
    from app.tasks import parse_tasks as tasks_mod

    demo_id, S = _build_scenario(monkeypatch, map_name="de_mirage")

    def boom(db, match, map_name, mode, points):
        raise KeyError(f"maps.json 缺少地图 {map_name}")

    monkeypatch.setattr(tasks_mod, "_save_heatmap", boom)
    result = _run_parse(monkeypatch, demo_id, S)

    assert result["demo_id"] == demo_id
    assert len(result["heatmap_warnings"]) == 4   # 四种热力图全部失败并隔离
    assert all("KeyError" in w or "缺少地图" in w for w in result["heatmap_warnings"])

    db = S()
    d = db.get(Demo, demo_id)
    assert d.status == "parsed"
    assert d.heatmap_warnings is not None
    assert "kills_all" in d.heatmap_warnings
    db.close()


def test_missing_radar_file_failure_does_not_fail_parse(monkeypatch):
    """有 maps.json 配置但缺底图文件：解析仍 parsed + 警告。"""
    import tempfile
    from pathlib import Path

    import app.services.heatmap as heatmap_mod
    from app.services.heatmap import RADAR_DIR

    demo_id, S = _build_scenario(monkeypatch, map_name="de_mirage")
    # 临时把底图目录指向空目录（模拟缺底图），再恢复
    empty = Path(tempfile.mkdtemp(prefix="radars_empty_"))
    original_dir = RADAR_DIR
    monkeypatch.setattr(heatmap_mod, "RADAR_DIR", empty)

    try:
        result = _run_parse(monkeypatch, demo_id, S)
    finally:
        monkeypatch.setattr(heatmap_mod, "RADAR_DIR", original_dir)

    assert result["demo_id"] == demo_id
    assert len(result["heatmap_warnings"]) == 4


def test_supported_map_generates_all_heatmaps(monkeypatch):
    """已支持地图（de_mirage，有底图）：四种热力图全部生成，无警告。"""
    from app.models import HeatmapCache

    demo_id, S = _build_scenario(monkeypatch, map_name="de_mirage")
    result = _run_parse(monkeypatch, demo_id, S)

    assert result["demo_id"] == demo_id
    assert result["heatmap_warnings"] == []

    db = S()
    d = db.get(Demo, demo_id)
    assert d.heatmap_warnings is None
    cache = db.query(HeatmapCache).filter(HeatmapCache.match_id == d.match_id).all()
    assert {c.mode for c in cache} == {"kills_all", "route_all", "route_ct", "route_t"}
    db.close()


def test_maps_config_keys_match_radar_files():
    """maps.json 的 5 张 MVR 目标地图键名与底图文件完全一致。"""
    from pathlib import Path

    import json

    import yaml

    root = Path(__file__).resolve().parent.parent.parent
    cfg = json.loads((root / "backend" / "config" / "maps.json").read_text(encoding="utf-8"))
    radar_dir = root / "backend" / "config" / "radars"
    for m in ["de_mirage", "de_anubis", "de_dust2", "de_inferno", "de_ancient"]:
        assert m in cfg["maps"], f"maps.json 缺少 {m}"
        assert (radar_dir / f"{m}.png").exists(), f"缺少底图 {m}.png"
        assert (radar_dir / f"{m}.png").read_bytes()[:4] == b"\x89PNG", f"{m}.png 非 PNG"
