"""Phase 2 回归 + Phase 3 权限化后的 API 测试（SQLite 内存库 + mock MinIO）。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, DemoFocusPlayer, Event, HeatmapCache, Match, Player, PlayerStats, RoundSide, User
from app.services import storage as storage_module
from app.services.security import hash_password
from app.api import demo as demo_api


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

    fake_store = {"objects": {"heatmaps/1/kills_all.png": b"fake-png-bytes"}}

    class FakeStorage:
        def upload_bytes(self, key, data, content_type="image/png"):
            fake_store["objects"][key] = data
            return key

        def get_bytes(self, key):
            return fake_store["objects"][key]

        def stat_size(self, key):
            return len(fake_store["objects"][key])

        def iter_bytes(self, key, chunk_size=1024 * 1024):
            data = fake_store["objects"][key]
            for start in range(0, len(data), chunk_size):
                yield data[start:start + chunk_size]

        def exists(self, key):
            return key in fake_store["objects"]

    monkeypatch.setattr(storage_module, "storage", FakeStorage())
    monkeypatch.setattr(demo_api, "storage", FakeStorage())

    # 用户 + 已解析 demo（归属该用户）
    db = TestingSession()
    user = User(email="owner@test.com", password_hash=hash_password("password123"), nickname="owner")
    db.add(user)
    db.flush()
    demo = Demo(user_id=user.id, file_name="=a.dem", file_path="/demos/a.dem", file_size=1, status="parsed", map_name="de_mirage")
    db.add(demo)
    db.flush()
    match = Match(demo_id=demo.id, map_name="de_mirage", score_ct=8, score_t=2, duration_seconds=453, rounds=10)
    db.add(match)
    db.flush()
    demo.match_id = match.id
    p = Player(match_id=match.id, steam_id="steam-secret-7656119", name="P1", team="CT")
    db.add(p)
    db.flush()
    db.add(PlayerStats(match_id=match.id, player_id=p.id, kills=12, deaths=6, assists=3, hs_kills=4, hs_rate=0.33, damage=1875, adr=187.5, first_kills=2, weapon_stats={}))
    db.add(Event(match_id=match.id, tick=100, round=1, event_type="kill", actor_steam="steam-secret-7656119", victim_steam="steam-victim", weapon="ak47", x=1.0, y=2.0))
    db.add_all([
        RoundSide(match_id=match.id, round=1, side_a=2, side_b=3),
        RoundSide(match_id=match.id, round=2, side_a=0, side_b=3),
    ])
    db.add(HeatmapCache(match_id=match.id, map_name="de_mirage", mode="kills_all", heatmap_key="heatmaps/1/kills_all.png"))
    db.commit()
    db.close()

    with TestClient(app) as c:
        c.testing_session = TestingSession
        c.fake_objects = fake_store["objects"]
        # 登录拿 token
        resp = c.post("/api/auth/login", json={"email": "owner@test.com", "password": "password123"})
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        yield c, headers
    app.dependency_overrides.clear()


def test_auth_required(client):
    c, _ = client
    assert c.get("/api/demos").status_code == 401
    assert c.get("/api/demos/1/report/basic").status_code == 401


def test_api_error_details_cannot_override_base_fields():
    from app.errors import ApiError
    err = ApiError(409, "ORIGINAL_CODE", "原始消息", {"code": "BAD", "message": "BAD", "extra": 1})
    assert err.code == "ORIGINAL_CODE" and err.message == "原始消息"
    assert "code" not in err.details and "message" not in err.details
    assert err.details["extra"] == 1


def test_demo_organization_and_recycle_bin(client):
    c, h = client
    updated = c.patch("/api/demos/1", headers=h, json={"display_name": "  精彩对局  ", "tags": ["ranked", " ranked ", "练习"], "is_favorite": True, "notes": "  备注  "})
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "精彩对局" and updated.json()["tags"] == ["ranked", "练习"] and updated.json()["notes"] == "备注"
    assert c.get("/api/demos?q=精彩对局", headers=h).json()[0]["id"] == 1
    assert c.patch("/api/demos/1", headers=h, json={}).status_code == 422
    assert c.patch("/api/demos/1", headers=h, json={"tags": None}).status_code == 422
    assert c.patch("/api/demos/1", headers=h, json={"is_favorite": None}).status_code == 422
    assert c.get("/api/demos?tag=练习", headers=h).json()[0]["id"] == 1
    assert c.get("/api/demos?tag=", headers=h).status_code == 422
    assert c.patch("/api/demos/1", headers=h, json={"tags": []}).status_code == 200
    assert c.get("/api/demos?tag=练习", headers=h).json() == []
    assert c.delete("/api/demos/1", headers=h).status_code == 200
    assert c.get("/api/demos/1/status", headers=h).status_code == 404
    assert c.get("/api/demos/1/report/basic", headers=h).status_code == 404
    assert c.post("/api/demos/1/retry", headers=h).status_code == 404
    assert c.get("/api/demos?view=active", headers=h).json() == []
    assert c.get("/api/demos?view=all", headers=h).json()[0]["id"] == 1
    assert c.get("/api/demos?view=invalid", headers=h).status_code == 422
    assert c.get("/api/demos?view=trash", headers=h).json()[0]["deleted_at"] is not None
    repeated = c.delete("/api/demos/1", headers=h)
    assert repeated.status_code == 409 and repeated.json()["detail"]["code"] == "DEMO_ALREADY_DELETED"
    not_deleted = c.post("/api/demos/1/restore", headers=h)
    assert not_deleted.status_code == 200
    assert c.post("/api/demos/1/restore", headers=h).status_code == 409
    assert c.get("/api/demos/1/status", headers=h).status_code == 200


def test_list_demos(client):
    c, h = client
    resp = c.get("/api/demos", headers=h)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "parsed"
    assert rows[0]["map_name"] == "de_mirage"
    assert rows[0]["file_size"] == 1
    assert rows[0]["team_a_score"] is None  # fixture uses legacy score fields only
    assert rows[0]["rounds"] == 10


def test_library_filters_and_validation(client):
    c, h = client
    assert len(c.get("/api/demos?q=a.dem&status=parsed&map_name=de_mirage&sort=oldest", headers=h).json()) == 1
    r = c.get("/api/demos?sort=bad", headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_rounds_events_redaction_and_export_acl(client):
    c, h = client
    rounds = c.get("/api/demos/1/rounds", headers=h).json()["rounds"]
    assert rounds[0]["side_a"] == "CT" and rounds[0]["kills_count"] == 1 and rounds[0]["winner"] is None
    events = c.get("/api/demos/1/events?event_type=kill&weapon=ak47", headers=h).json()
    assert events["total"] == 1 and "steam-secret-7656119" not in str(events) and events["events"][0]["actor"]["player_id"] == 1
    free_export = c.get("/api/demos/1/export.json?sections=match,players,rounds,kills", headers=h)
    assert free_export.status_code == 200 and "grenades" not in free_export.json()
    assert c.get("/api/demos/1/export.json?sections=grenades", headers=h).status_code == 403
    assert c.get("/api/demos/1/events?event_type=bad", headers=h).status_code == 422
    assert c.get("/api/demos/1/rounds?round_no=0", headers=h).status_code == 422
    assert c.get("/api/demos/1/rounds?round_no=11", headers=h).status_code == 422
    assert c.get("/api/demos/1/rounds?side=bad", headers=h).status_code == 422
    page = c.get("/api/demos/1/events?player_id=1&tick_from=100&tick_to=100&limit=1&offset=0", headers=h).json()
    assert page["total"] == 1 and len(page["events"]) == 1
    assert "attachment" in free_export.headers["content-disposition"]


def test_round_side_unknown_is_not_fabricated(client):
    c, h = client
    # all rounds remain explicit null for unknown side values
    rounds = c.get("/api/demos/1/rounds", headers=h).json()["rounds"]
    assert rounds[1]["side_a"] is None


def test_xlsx_export_free_sections_and_acl(client):
    from io import BytesIO
    from openpyxl import load_workbook
    c, h = client
    resp = c.get("/api/demos/1/export.xlsx?sections=match,players,rounds,kills", headers=h)
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"] and resp.headers["content-disposition"].endswith('.xlsx"')
    assert resp.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(BytesIO(resp.content), read_only=True, data_only=False)
    assert set(wb.sheetnames) == {"Match", "Players", "Rounds", "Kills"}
    assert list(wb["Match"].values)[0][:3] == ("demo_id", "file_name", "map_name")
    assert list(wb["Match"].values)[1][1] == "'=a.dem"
    values = [cell.value for ws in wb.worksheets for row in ws.iter_rows() for cell in row]
    assert "steam-secret-7656119" not in values
    assert all(cell.data_type != "f" for ws in wb.worksheets for row in ws.iter_rows() for cell in row)
    assert c.get("/api/demos/1/export.xlsx?sections=grenades", headers=h).status_code == 403


def test_source_download_is_private_header_safe_and_streamed(client):
    c, h = client
    key = "demos/0123456789abcdef0123456789abcdef.dem"
    content = b"PBDEMS2" + b"demo-body" * 100
    c.fake_objects[key] = content
    db = c.testing_session()
    demo = db.get(Demo, 1)
    demo.file_path = key
    demo.file_name = "../战术复盘\r\nX-Injected: yes.dem"
    demo.file_size = len(content)
    db.commit()
    db.close()

    resp = c.get("/api/demos/1/source", headers=h)
    assert resp.status_code == 200 and resp.content == content
    assert resp.headers["content-type"].startswith("application/octet-stream")
    assert resp.headers["content-length"] == str(len(content))
    assert resp.headers["cache-control"] == "private, no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    disposition = resp.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert 'filename="X-Injected_ yes.dem"' in disposition
    assert "filename*=UTF-8''" in disposition and "%E6%88%98%E6%9C%AF" in disposition


def test_source_download_missing_invalid_deleted_and_cross_user_are_hidden(client, monkeypatch):
    c, h = client
    db = c.testing_session()
    demo = db.get(Demo, 1)
    demo.file_path = "demos/abcdefabcdefabcdefabcdefabcdefab.dem"
    db.commit()
    db.close()
    missing = c.get("/api/demos/1/source", headers=h)
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "DEMO_SOURCE_MISSING"

    db = c.testing_session()
    demo = db.get(Demo, 1)
    demo.file_path = "/demos/legacy-local-path.dem"
    db.commit()
    db.close()
    invalid = c.get("/api/demos/1/source", headers=h)
    assert invalid.status_code == 404 and invalid.json()["detail"]["code"] == "DEMO_SOURCE_MISSING"

    db = c.testing_session()
    demo = db.get(Demo, 1)
    demo.file_path = "demos/abcdefabcdefabcdefabcdefabcdefab.dem"
    db.commit()
    db.close()
    monkeypatch.setattr(demo_api.storage, "stat_size", lambda key: 1)
    def open_failed(_):
        raise RuntimeError("object disappeared")
    monkeypatch.setattr(demo_api.storage, "iter_bytes", open_failed)
    raced = c.get("/api/demos/1/source", headers=h)
    assert raced.status_code == 404 and raced.json()["detail"]["code"] == "DEMO_SOURCE_MISSING"

    registered = c.post("/api/auth/register", json={"email": "download-other@test.com", "password": "password123", "nickname": "other"})
    other = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    assert c.get("/api/demos/1/source", headers=other).status_code == 404

    assert c.delete("/api/demos/1", headers=h).status_code == 200
    assert c.get("/api/demos/1/source", headers=h).status_code == 404


def test_storage_iter_bytes_is_bounded_and_releases_connection():
    from app.services.storage import Storage

    class Response:
        def __init__(self):
            self.remaining = b"0123456789"
            self.read_sizes = []
            self.closed = False
            self.released = False

        def read(self, size):
            self.read_sizes.append(size)
            out, self.remaining = self.remaining[:size], self.remaining[size:]
            return out

        def close(self):
            self.closed = True

        def release_conn(self):
            self.released = True

    response = Response()
    instance = object.__new__(Storage)
    instance.bucket = "private"
    instance.client = type("Client", (), {"get_object": lambda self, bucket, key: response})()
    chunks = list(instance.iter_bytes("demos/a.dem", chunk_size=4))
    assert chunks == [b"0123", b"4567", b"89"]
    assert max(map(len, chunks)) <= 4 and all(size == 4 for size in response.read_sizes)
    assert response.closed and response.released

    class CloseFailure(Response):
        def close(self):
            self.closed = True
            raise RuntimeError("close failed")

    close_failure = CloseFailure()
    instance.client = type("Client", (), {"get_object": lambda self, bucket, key: close_failure})()
    with pytest.raises(RuntimeError, match="close failed"):
        list(instance.iter_bytes("demos/a.dem", chunk_size=4))
    assert close_failure.closed and close_failure.released


def test_cross_user_library_resources_are_404(client):
    c, _ = client
    r = c.post("/api/auth/register", json={"email": "other@test.com", "password": "password123", "nickname": "other"})
    assert r.status_code in (200, 201)
    token = c.post("/api/auth/login", json={"email": "other@test.com", "password": "password123"}).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    for path in ("/api/demos/1/rounds", "/api/demos/1/events", "/api/demos/1/export.json", "/api/demos/1/export.xlsx", "/api/demos/1/source"):
        assert c.get(path, headers=other).status_code == 404
    assert c.post("/api/demos/1/retry", headers=other).status_code == 404
    assert c.patch("/api/demos/1", headers=other, json={"notes": "x"}).status_code == 404
    assert c.delete("/api/demos/1", headers=other).status_code == 404
    assert c.post("/api/demos/1/restore", headers=other).status_code == 404


def test_retry_state_machine_and_queue_compensation(client, monkeypatch):
    c, h = client
    S = c.testing_session
    db = S(); d = db.get(Demo, 1); d.status = "failed"; d.file_path = "demos/source.dem"; db.commit(); db.close()
    calls = []
    monkeypatch.setattr(demo_api.storage, "exists", lambda key: True)
    monkeypatch.setattr(demo_api.process_demo, "delay", lambda demo_id: calls.append(demo_id))
    first = c.post("/api/demos/1/retry", headers=h)
    assert first.status_code == 200 and first.json()["status"] == "uploaded" and calls == [1]
    assert c.post("/api/demos/1/retry", headers=h).status_code == 409

    db = S(); d = db.get(Demo, 1); d.status = "failed"; db.commit(); db.close()
    monkeypatch.setattr(demo_api.storage, "exists", lambda key: False)
    missing = c.post("/api/demos/1/retry", headers=h)
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "DEMO_SOURCE_MISSING" and calls == [1]

    db = S(); d = db.get(Demo, 1); d.status = "failed"; db.commit(); db.close()
    monkeypatch.setattr(demo_api.storage, "exists", lambda key: True)
    def boom(_): raise RuntimeError("queue down")
    monkeypatch.setattr(demo_api.process_demo, "delay", boom)
    failed = c.post("/api/demos/1/retry", headers=h)
    assert failed.status_code == 503 and failed.json()["detail"]["code"] == "QUEUE_UNAVAILABLE"
    db = S(); d = db.get(Demo, 1); assert d.status == "failed" and "队列暂不可用" in d.error_msg; db.close()


def test_demo_status(client):
    c, h = client
    resp = c.get("/api/demos/1/status", headers=h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "parsed"
    assert c.get("/api/demos/999/status", headers=h).status_code == 404


def test_basic_report(client):
    c, h = client
    resp = c.get("/api/demos/1/report/basic", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["match"]["score_ct"] == 8
    assert body["match"]["rounds"] == 10
    assert len(body["players"]) == 1
    assert body["players"][0]["stats"]["adr"] == 187.5
    assert body["heatmaps"][0]["mode"] == "kills_all"
    assert body["heatmaps"][0]["url"] == "/api/demos/1/heatmap/kills_all"


def test_heatmap_png(client):
    c, h = client
    resp = c.get("/api/demos/1/heatmap/kills_all", headers=h)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"fake-png-bytes"


def test_heatmap_missing_or_protected(client):
    c, h = client
    # free 用户访问 route 热力图：订阅拦截优先 → 403（而非 404）
    resp = c.get("/api/demos/1/heatmap/route_all", headers=h)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"


def test_kills_nan_coordinates_json_safe():
    """NaN/Inf 坐标不导致 JSON 500（真实 demo 实测 P1：anubis 击杀坐标含 NaN）。"""
    import json

    from app.api.demo import _clean_float

    assert _clean_float(float("nan")) is None
    assert _clean_float(float("inf")) is None
    assert _clean_float(-1.23456) == -1.2
    assert _clean_float(3.0) == 3.0
    assert _clean_float(None) is None
    json.dumps({"x": _clean_float(float("nan"))})   # 不抛异常
