"""Phase 3 权限测试：上传校验/免费额度/跨用户越权/订阅鉴权/过期降级。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, Event, HeatmapCache, Match, Player, PlayerStats, User
from app.services import storage as storage_module
from app.services.security import hash_password
from app.api import demo as demo_api

# 合法 demo 文件头（PBDEMS2 魔数）
FAKE_DEMO = b"PBDEMS2" + b"\x00" + b"fake-demo-content" * 100


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    fake_store = {
        "objects": {
            "h/1/kills_all.png": b"png-kills",
            "h/1/route_all.png": b"png-route",
            "h/1/kills_all.json": b'{"map":"de_mirage","buckets":256,"size":1024,"data":[[10,10,0.5]]}',
            "h/1/route_all.json": b'{"map":"de_mirage","buckets":256,"size":1024,"data":[[20,20,0.8]]}',
        }
    }

    class FakeStorage:
        def upload_bytes(self, key, data, content_type="image/png"):
            fake_store["objects"][key] = data
            return key

        def upload_file(self, key, path, content_type="application/octet-stream"):
            from pathlib import Path

            fake_store["objects"][key] = Path(path).read_bytes()
            return key

        def get_bytes(self, key):
            return fake_store["objects"][key]

        def exists(self, key):
            return key in fake_store["objects"]

        def delete(self, key):
            fake_store["objects"].pop(key, None)

    monkeypatch.setattr(storage_module, "storage", FakeStorage())
    monkeypatch.setattr(demo_api, "storage", FakeStorage())

    # 测试环境无 Redis：mock 掉 Celery 投递（上传成功路径）
    monkeypatch.setattr(demo_api, "process_demo", type("T", (), {"delay": staticmethod(lambda demo_id: None)})())

    db = TestingSession()
    user_a = User(email="a@test.com", password_hash=hash_password("password123"), nickname="A")
    user_b = User(email="b@test.com", password_hash=hash_password("password123"), nickname="B")
    db.add_all([user_a, user_b])
    db.flush()
    # 历史 demo：created_at 设为 2 天前，避免占用"今日上传"额度
    demo = Demo(user_id=user_a.id, file_name="a.dem", file_path="demos/abc.dem", file_size=100,
                status="parsed", map_name="de_mirage",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2))
    db.add(demo)
    db.flush()
    match = Match(demo_id=demo.id, map_name="de_mirage", score_ct=8, score_t=2, duration_seconds=453, rounds=10)
    db.add(match)
    db.flush()
    demo.match_id = match.id
    p = Player(match_id=match.id, steam_id="1", name="P1", team="CT")
    db.add(p)
    db.flush()
    db.add(PlayerStats(match_id=match.id, player_id=p.id, kills=1, deaths=1, assists=0, hs_kills=0, hs_rate=0, damage=100, adr=10.0, first_kills=0, weapon_stats={}))
    db.add(HeatmapCache(match_id=match.id, map_name="de_mirage", mode="kills_all", heatmap_key="h/1/kills_all.png"))
    db.add(HeatmapCache(match_id=match.id, map_name="de_mirage", mode="route_all", heatmap_key="h/1/route_all.png"))
    db.add(Event(match_id=match.id, tick=100, round=1, event_type="kill", actor_steam="1", victim_steam="2", weapon="ak47", x=1.0, y=2.0))
    db.add(Event(match_id=match.id, tick=200, round=1, event_type="kill", actor_steam="2", victim_steam="1", weapon="awp", x=3.0, y=4.0))
    db.add(Event(match_id=match.id, tick=300, round=2, event_type="grenade", actor_steam="1", weapon=None, x=5.0, y=6.0, grenade_type="smoke"))
    db.add(Event(match_id=match.id, tick=400, round=2, event_type="grenade", actor_steam="2", weapon=None, x=7.0, y=8.0, grenade_type="flash"))
    db.commit()
    db.close()

    with TestClient(app) as c:
        token_a = c.post("/api/auth/login", json={"email": "a@test.com", "password": "password123"}).json()["access_token"]
        token_b = c.post("/api/auth/login", json={"email": "b@test.com", "password": "password123"}).json()["access_token"]
        yield c, {"Authorization": f"Bearer {token_a}"}, {"Authorization": f"Bearer {token_b}"}, TestingSession
    app.dependency_overrides.clear()


def _set_subscription(TestingSession, email, tier, expire_days):
    """测试辅助：直接改库设置订阅（对应 Phase 6 前的管理端开通方式）。"""
    db = TestingSession()
    u = db.query(User).filter(User.email == email).one()
    u.subscription_tier = tier
    u.subscription_expire = datetime.now(timezone.utc) + timedelta(days=expire_days) if expire_days else None
    db.commit()
    db.close()


# ---------- 上传 ----------

def test_upload_requires_auth(client):
    c, _, _, _ = client
    assert c.post("/api/demos/upload", files={"file": ("x.dem", FAKE_DEMO, "application/octet-stream")}).status_code == 401


def test_upload_wrong_extension(client):
    c, h, _, _ = client
    resp = c.post("/api/demos/upload", headers=h, files={"file": ("x.exe", FAKE_DEMO, "application/octet-stream")})
    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "FILE_TYPE_INVALID"


def test_upload_bad_content(client):
    c, h, _, _ = client
    resp = c.post("/api/demos/upload", headers=h, files={"file": ("x.dem", b"not-a-demo-at-all", "application/octet-stream")})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "FILE_CONTENT_INVALID"


def test_upload_too_large(client, monkeypatch):
    """超限立即终止：用 mock 小上限避免测试构造 100MB 内存（P1-1）。"""
    import app.api.demo as demo_api

    c, h, _, _ = client
    monkeypatch.setattr(demo_api, "MAX_DEMO_SIZE", 1024)
    big = FAKE_DEMO + b"\x00" * 2048  # > 1024
    resp = c.post("/api/demos/upload", headers=h, files={"file": ("big.dem", big, "application/octet-stream")})
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "FILE_TOO_LARGE"


def test_upload_size_boundary(client, monkeypatch):
    """边界：恰好 1024 字节可接受，1025 字节 413（用 B 用户避免额度干扰）。"""
    import app.api.demo as demo_api

    c, h_a, h_b, _ = client
    monkeypatch.setattr(demo_api, "MAX_DEMO_SIZE", 1024)
    ok = FAKE_DEMO[:1024]  # 恰好 1024 字节（含魔数）
    assert len(ok) == 1024
    resp = c.post("/api/demos/upload", headers=h_a, files={"file": ("exact.dem", ok, "application/octet-stream")})
    assert resp.status_code == 200
    over = ok + b"\x00"
    resp2 = c.post("/api/demos/upload", headers=h_b, files={"file": ("over.dem", over, "application/octet-stream")})
    assert resp2.status_code == 413
    assert resp2.json()["detail"]["code"] == "FILE_TOO_LARGE"


def test_max_demo_size_config_default():
    """MVR：上传上限为配置项，默认 300MiB（不再硬编码 100MiB）。"""
    from app.api import demo as demo_api
    from app.config import settings

    assert settings.max_demo_size_mb == 300
    assert demo_api.MAX_DEMO_SIZE == 300 * 1024 * 1024
    assert demo_api.MAX_DEMO_SIZE > 100 * 1024 * 1024   # >100MiB 回归


def test_upload_over_100MiB_rejected_with_config_message(client, monkeypatch):
    """>100MiB 回归：配置上限下超限文件返回 413 且错误消息含配置值。"""
    import app.api.demo as demo_api

    c, h_a, _, _ = client
    # 构造一个超过 100MiB 上限的小文件场景：mock 上限为 150MiB，上传 150MiB+1 不可行（内存），
    # 改为断言 413 消息使用配置值（300MiB）而非硬编码 100MiB
    monkeypatch.setattr(demo_api, "MAX_DEMO_SIZE", 1024)
    resp = c.post("/api/demos/upload", headers=h_a,
                  files={"file": ("big.dem", FAKE_DEMO + b"\x00" * 2048, "application/octet-stream")})
    assert resp.status_code == 413
    assert "300MiB" in resp.json()["detail"]["message"]


def test_upload_ok_and_returns_demo_id(client):
    c, h, _, _ = client
    resp = c.post("/api/demos/upload", headers=h, files={"file": ("match.dem", FAKE_DEMO, "application/octet-stream")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["demo_id"] == 2
    assert body["status"] == "uploaded"


def test_duplicate_content_same_user_conflicts_cross_user_allowed(client):
    c, h_a, h_b, TestingSession = client
    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=30)
    payload = FAKE_DEMO + b"duplicate-check"
    assert c.post("/api/demos/upload", headers=h_a, files={"file": ("first.dem", payload, "application/octet-stream")}).status_code == 200
    duplicate = c.post("/api/demos/upload", headers=h_a, files={"file": ("second.dem", payload, "application/octet-stream")})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "DUPLICATE_DEMO"
    assert duplicate.json()["detail"]["existing_demo_id"] == 2
    listed = c.get("/api/demos", headers=h_a).json()
    uploaded = next(row for row in listed if row["id"] == 2)
    assert "content_sha256" not in str(listed) and uploaded["source"] == "upload"
    status = c.get("/api/demos/2/status", headers=h_a).json()
    assert "content_sha256" not in status and status["source"] == "upload"
    assert c.post("/api/demos/upload", headers=h_b, files={"file": ("other.dem", payload, "application/octet-stream")}).status_code == 200


def test_batch_upload_mixed_order_and_limit(client):
    c, h_a, _, TestingSession = client
    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=30)
    payload = FAKE_DEMO + b"batch-one"
    resp = c.post("/api/demos/upload/batch", headers=h_a, files=[
        ("files", ("one.dem", payload, "application/octet-stream")),
        ("files", ("duplicate.dem", payload, "application/octet-stream")),
        ("files", ("bad.exe", payload, "application/octet-stream")),
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3 and [r["index"] for r in body["results"]] == [0, 1, 2]
    assert body["succeeded"] == 1 and body["failed"] == 2
    assert body["results"][0]["ok"] is True
    assert body["results"][1]["error"]["code"] == "DUPLICATE_DEMO"
    assert body["results"][1]["error"]["existing_demo_id"] == body["results"][0]["demo_id"]
    assert body["results"][2]["error"]["code"] == "FILE_TYPE_INVALID"
    too_many = c.post("/api/demos/upload/batch", headers=h_a, files=[("files", (f"{i}.dem", FAKE_DEMO + bytes([i]), "application/octet-stream")) for i in range(21)])
    assert too_many.status_code == 422 and too_many.json()["detail"]["code"] == "BATCH_SIZE_INVALID"


def test_batch_quota_exhaustion_is_per_item(client):
    c, h_a, _, _ = client
    first = FAKE_DEMO + b"quota-first"
    second = FAKE_DEMO + b"quota-second"
    resp = c.post("/api/demos/upload/batch", headers=h_a, files=[
        ("files", ("first.dem", first, "application/octet-stream")),
        ("files", ("second.dem", second, "application/octet-stream")),
    ])
    body = resp.json()
    assert resp.status_code == 200 and body["results"][0]["ok"] is True
    assert body["results"][1]["error"]["code"] == "FREE_DAILY_LIMIT"


def test_batch_unknown_exception_is_safe(client, monkeypatch):
    c, h_a, _, _ = client
    real_upload_one = demo_api._upload_one
    async def boom(*args, **kwargs):
        if args[0].filename == "explode.dem":
            raise RuntimeError("secret internal details")
        return await real_upload_one(*args, **kwargs)
    monkeypatch.setattr(demo_api, "_upload_one", boom)
    resp = c.post("/api/demos/upload/batch", headers=h_a, files=[
        ("files", ("explode.dem", FAKE_DEMO, "application/octet-stream")),
        ("files", ("survive.dem", FAKE_DEMO + b"survive", "application/octet-stream")),
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["error"] == {"code": "UPLOAD_FAILED", "message": "上传失败，请稍后重试"}
    assert body["results"][1]["ok"] is True and body["results"][1]["index"] == 1
    assert "secret internal" not in resp.text


def test_batch_without_files_has_stable_error(client):
    c, h_a, _, _ = client
    resp = c.post("/api/demos/upload/batch", headers=h_a)
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "BATCH_SIZE_INVALID"


def test_same_user_null_content_hashes_are_allowed(client):
    _, _, _, TestingSession = client
    db = TestingSession()
    db.add_all([
        Demo(user_id=2, file_name="legacy-a.dem", file_path="legacy/a.dem", file_size=1, content_sha256=None),
        Demo(user_id=2, file_name="legacy-b.dem", file_path="legacy/b.dem", file_size=1, content_sha256=None),
    ])
    db.commit()
    assert db.query(Demo).filter(Demo.user_id == 2, Demo.content_sha256.is_(None)).count() == 2
    db.close()


def test_free_daily_limit(client):
    c, h, _, _ = client
    for i in range(2):
        resp = c.post("/api/demos/upload", headers=h, files={"file": (f"m{i}.dem", FAKE_DEMO + bytes([i]), "application/octet-stream")})
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "FREE_DAILY_LIMIT"


def test_valid_pro_bypasses_daily_limit(client):
    """P0-3：有效 pro 不受免费日额度限制，可连续上传。"""
    c, h, _, TestingSession = client
    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=30)
    for i in range(2):
        resp = c.post("/api/demos/upload", headers=h, files={"file": (f"pro{i}.dem", FAKE_DEMO + bytes([10 + i]), "application/octet-stream")})
        assert resp.status_code == 200, f"第 {i+1} 次上传应成功：{resp.text}"


def test_expired_pro_still_limited(client):
    """过期 pro 按 free 处理：第二次上传 429。"""
    c, h, _, TestingSession = client
    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=-1)
    assert c.post("/api/demos/upload", headers=h, files={"file": ("e1.dem", FAKE_DEMO + b"1", "application/octet-stream")}).status_code == 200
    resp = c.post("/api/demos/upload", headers=h, files={"file": ("e2.dem", FAKE_DEMO + b"2", "application/octet-stream")})
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "FREE_DAILY_LIMIT"


def test_queue_dispatch_failure_compensates(client, monkeypatch):
    """P1-3：Celery 投递失败 → 503 + demo 置 failed，不谎称已开始解析。"""
    import app.api.demo as demo_api

    c, h, _, TestingSession = client

    def _boom(demo_id):
        raise RuntimeError("broker down")

    monkeypatch.setattr(demo_api, "process_demo", type("T", (), {"delay": staticmethod(_boom)})())
    resp = c.post("/api/demos/upload", headers=h, files={"file": ("q.dem", FAKE_DEMO, "application/octet-stream")})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "QUEUE_UNAVAILABLE"

    # demo 记录已存在但状态为 failed + 稳定错误信息
    db = TestingSession()
    d = db.query(Demo).filter(Demo.file_name == "q.dem").one()
    assert d.status == "failed"
    assert "队列" in d.error_msg
    db.close()


def test_upload_tempfile_cleaned_on_failure(client, monkeypatch):
    """小尾巴：异常路径（魔数失败）临时文件必须清理（先 close 再 unlink，Windows 安全）。"""
    import os
    import tempfile

    import app.api.demo as demo_api

    c, h, _, _ = client
    real_ntf = tempfile.NamedTemporaryFile
    workdir = tempfile.mkdtemp(prefix="cs2tmp_")
    created = {"path": None}

    def fake_ntf(*a, **kw):
        kw["dir"] = workdir
        f = real_ntf(*a, **kw)
        created["path"] = f.name
        return f

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fake_ntf)
    try:
        resp = c.post("/api/demos/upload", headers=h, files={"file": ("bad.dem", b"not-a-demo", "application/octet-stream")})
        assert resp.status_code == 422
        # Windows 上 unlink 打开的文件会失败：若未先 close，文件将残留 → 断言不存在即验证了 close→unlink 顺序
        assert created["path"] is not None
        assert not os.path.exists(created["path"]), "临时文件未清理"
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)


# ---------- 越权 ----------

def test_cross_user_demo_404(client):
    c, h_a, h_b, _ = client
    assert c.get("/api/demos/1/report/basic", headers=h_a).status_code == 200
    assert c.get("/api/demos/1/report/basic", headers=h_b).status_code == 404
    assert c.get("/api/demos/1/status", headers=h_b).status_code == 404
    assert c.get("/api/demos/1/heatmap/kills_all", headers=h_b).status_code == 404
    rows = c.get("/api/demos", headers=h_b).json()
    assert all(r["id"] != 1 for r in rows)


# ---------- 订阅 ----------

def test_free_advanced_report_403(client):
    c, h_a, _, _ = client
    resp = c.get("/api/demos/1/report/advanced", headers=h_a)
    assert resp.status_code == 403
    body = resp.json()["detail"]
    assert body["code"] == "SUBSCRIPTION_REQUIRED"
    assert "进阶版" in body["message"]


def test_free_route_heatmap_403_kills_ok(client):
    c, h_a, _, _ = client
    resp = c.get("/api/demos/1/heatmap/route_all", headers=h_a)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"
    assert c.get("/api/demos/1/heatmap/kills_all", headers=h_a).status_code == 200


def test_valid_pro_allowed(client):
    c, h_a, _, TestingSession = client
    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=30)
    assert c.get("/api/demos/1/report/advanced", headers=h_a).status_code == 200
    assert c.get("/api/demos/1/heatmap/route_all", headers=h_a).status_code == 200


def test_expired_pro_treated_as_free(client):
    c, h_a, _, TestingSession = client
    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=-1)
    resp = c.get("/api/demos/1/report/advanced", headers=h_a)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"
    assert "过期" in resp.json()["detail"]["message"]
    resp2 = c.get("/api/demos/1/heatmap/route_all", headers=h_a)
    assert resp2.status_code == 403


# ---------- Phase 4B 报告聚合 API ----------

def test_kills_timeline_free_ok(client):
    """击杀时间线：free 可看，内容与事件数据一致。"""
    c, h_a, _, _ = client
    resp = c.get("/api/demos/1/kills", headers=h_a)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2  # fixture 2 条 kill
    assert rows[0]["weapon"] == "ak47"
    assert rows[0]["round"] == 1
    assert rows[0]["killer"] == "P1"  # steam_id 1 → name P1 安全映射
    assert rows[1]["weapon"] == "awp"
    # tick 升序
    assert rows[0]["tick"] < rows[1]["tick"]


def test_kills_timeline_cross_user(client):
    c, _, h_b, _ = client
    assert c.get("/api/demos/1/kills", headers=h_b).status_code == 404


def test_grenades_free_403_pro_ok(client):
    """道具统计：free 403，pro 200 且聚合正确。"""
    c, h_a, _, TestingSession = client
    resp = c.get("/api/demos/1/grenades", headers=h_a)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"

    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=30)
    resp = c.get("/api/demos/1/grenades", headers=h_a)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    types = {t["type"]: t["count"] for t in body["by_type"]}
    assert types == {"smoke": 1, "flash": 1}
    assert len(body["by_round"]) == 1 and body["by_round"][0]["round"] == 2
    assert body["events"][0]["label"] == "烟雾弹"


def test_team_stats_free_403_pro_403_max_ok(client):
    """团队汇总：free 403，pro 403（Max-only），max 200 且合计与 stats 一致。"""
    c, h_a, _, TestingSession = client
    assert c.get("/api/demos/1/team-stats", headers=h_a).status_code == 403
    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=30)
    assert c.get("/api/demos/1/team-stats", headers=h_a).status_code == 403   # Pro 越权（Max-only）
    _set_subscription(TestingSession, "a@test.com", "max", expire_days=30)
    from tests._team_provision import grant_team_entitlement

    db = TestingSession()
    try:
        grant_team_entitlement(db, "a@test.com", 1)
    finally:
        db.close()
    resp = c.get("/api/demos/1/team-stats", headers=h_a)
    assert resp.status_code == 200
    body = resp.json()
    ct = next(t for t in body["teams"] if t["team"] == "CT")
    # fixture：P1 CT，kills=1 damage=100 adr=10
    assert ct["kills"] == 1
    assert ct["damage"] == 100
    assert ct["team_adr"] == 10.0


def test_density_free_pro_gating(client):
    """密度 JSON：kills_all 免费 200；route_all free 403、pro 200。"""
    c, h_a, _, TestingSession = client
    resp = c.get("/api/demos/1/density/kills_all", headers=h_a)
    assert resp.status_code == 200
    assert resp.json()["data"][0][2] == 0.5  # 与 mock 数据一致

    assert c.get("/api/demos/1/density/route_all", headers=h_a).status_code == 403
    _set_subscription(TestingSession, "a@test.com", "pro", expire_days=30)
    resp = c.get("/api/demos/1/density/route_all", headers=h_a)
    assert resp.status_code == 200
    assert resp.json()["mode"] == "route_all"
