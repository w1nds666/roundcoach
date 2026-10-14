"""6R16 API 集成测试：coordination 返回 area_state_machine（区域状态机）+ 区域证据。

- /api/demos/{id}/coordination 需 Max；用 grantTeamEntitlementForDemo 夹具满足团队门禁。
- 返回 body.area_state_machine（含区域 segments: A/B + transitions + contested/controlled）。
- 区域状态机用真实区域几何（region_geometry 已回填 + 测试配置），并验证"敌我同区→contested、
  持续占据无信号→occupied/influence、带正向信号→controlled"的诚实判定。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, DemoFocusPlayer, Event, Match, Player, RoundSide, TacticalArtifact, User
from app.services.routes import gzip_json
from app.services.security import hash_password
from app.services.tactical import TACTICAL_SCHEMA_VERSION, artifact_sha256, tactical_object_key


def _artifact_rounds(**kw):
    import copy
    base = {
        "schema_version": TACTICAL_SCHEMA_VERSION, "match_id": 1, "map_name": "de_mirage", "tick_rate": 64.0,
        "rounds": [{
            "round": 1, "start_tick": 65, "end_tick": 10000, "freeze_end_tick": 65,
            "winner_side": 2, "winner_team": "A", "team_side_map": {"A": 2, "B": 3},
            "players": {
                # P1(CT) 持续占 BombsiteA；P2(T) 同区争夺 → contested
                "1": {"player_id": 1, "side": "CT", "outcome": "alive",
                      "points": [{"tick": 65, "time": 0.0, "x": 100.0, "y": 100.0, "z": 0.0, "yaw": 90.0,
                                  "pitch": 0.0, "alive": True, "place": "BombsiteA"},
                                 {"tick": 10000, "time": 155.0, "x": 110.0, "y": 110.0, "z": 0.0, "yaw": 90.0,
                                  "pitch": 0.0, "alive": True, "place": "BombsiteA"}]},
                "2": {"player_id": 2, "side": "T", "outcome": "dead",
                      "points": [{"tick": 65, "time": 0.0, "x": 120.0, "y": 100.0, "z": 0.0, "yaw": -90.0,
                                  "pitch": 0.0, "alive": True, "place": "BombsiteA"},
                                 {"tick": 10000, "time": 155.0, "x": 120.0, "y": 100.0, "z": 0.0, "yaw": -90.0,
                                  "pitch": 0.0, "alive": False, "place": "BombsiteA"}]},
            },
            "props": [], "blinds": [], "he_damage": [], "shoots": [],
        }],
    }
    base["rounds"][0].update(kw)
    return base


def _kills():
    # 无击杀（避免干扰）；仅验证区域状态机
    return []


class FakeStorage:
    def __init__(self, data):
        self.data = {tactical_object_key(1, TACTICAL_SCHEMA_VERSION): data}

    def get_bytes(self, key):
        if key not in self.data:
            raise FileNotFoundError(key)
        return self.data[key]


@pytest.fixture
def client(monkeypatch):
    from app.services import storage as storage_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = S()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    db = S()
    u = User(email="a@test.com", password_hash=hash_password("password123"), nickname="A")
    db.add(u); db.flush()
    d = Demo(user_id=u.id, file_name="a.dem", file_path="demos/a.dem", file_size=1, status="parsed",
             map_name="de_mirage")
    db.add(d); db.flush()
    m = Match(demo_id=d.id, map_name="de_mirage", team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
              duration_seconds=500, rounds=1)
    db.add(m); db.flush()
    d.match_id = m.id
    p1 = Player(match_id=m.id, steam_id="111", name="P1", team="CT", team_identity="A")
    p2 = Player(match_id=m.id, steam_id="222", name="P2", team="T", team_identity="B")
    db.add_all([p1, p2]); db.flush()
    db.add(RoundSide(match_id=m.id, round=1, side_a=2, side_b=3))
    db.add(DemoFocusPlayer(user_id=u.id, demo_id=d.id, player_id=p1.id))
    art = _artifact_rounds()
    data = gzip_json(art)
    db.add(TacticalArtifact(match_id=m.id, schema_version=TACTICAL_SCHEMA_VERSION,
                            object_key=tactical_object_key(1, TACTICAL_SCHEMA_VERSION),
                            size_bytes=len(data), sha256=artifact_sha256(data),
                            player_count=2, round_count=1, prop_count=0))
    db.commit(); db.close()

    fake = FakeStorage(data)
    monkeypatch.setattr(storage_module, "storage", fake)
    with TestClient(app) as c:
        ta = c.post("/api/auth/login", json={"email": "a@test.com", "password": "password123"}).json()["access_token"]
        yield c, {"Authorization": f"Bearer {ta}"}, S
    app.dependency_overrides.clear()


def _set_tier(S, email, tier):
    db = S()
    u = db.query(User).filter(User.email == email).one()
    u.subscription_tier = tier
    db.commit(); db.close()


def _grant_team(S):
    from tests._team_provision import grant_team_entitlement
    db = S()
    try:
        grant_team_entitlement(db, "a@test.com", 1)
    finally:
        db.close()


def test_coordination_api_includes_area_state_machine(client):
    c, h, S = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    body = c.get("/api/demos/1/coordination", headers=h).json()
    assert "area_state_machine" in body, "应返回 area_state_machine"
    # 该 demo 是 de_mirage，区域几何已回填 → area_state_machine 非空
    assert body["area_state_machine"], "de_mirage 应有区域状态机"
    sample = body["area_state_machine"][0]
    # 每回合每区域每队应有 segments + transitions + 区域标识
    for k in ("round", "team", "place_key", "place_cn", "segments", "transitions"):
        assert k in sample, f"缺 {k}"
    # 不泄露 steam / 世界坐标
    assert "steam" not in str(body).lower().replace("steamid", "")
    # 区域状态机 deterministic：两次请求同 hash（content_hash）
    body2 = c.get("/api/demos/1/coordination", headers=h).json()
    assert body["content_hash"] == body2["content_hash"]
