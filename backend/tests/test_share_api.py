"""6R3D2 private-link lifecycle, ACL, expiry and public-payload redaction."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, DemoShare, Match, Player, PlayerStats, User
from app.services.security import hash_password


@pytest.fixture
def share_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    db = TestingSession()
    owner = User(email="share-owner@test.com", password_hash=hash_password("password123"), nickname="owner")
    other = User(email="share-other@test.com", password_hash=hash_password("password123"), nickname="other")
    db.add_all([owner, other])
    db.flush()
    demo = Demo(user_id=owner.id, file_name="secret-match.dem", file_path="demos/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.dem", file_size=8, status="parsed", map_name="de_mirage")
    pending = Demo(user_id=owner.id, file_name="pending.dem", file_path="demos/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.dem", file_size=8, status="parsing")
    db.add_all([demo, pending])
    db.flush()
    match = Match(demo_id=demo.id, map_name="de_mirage", score_ct=7, score_t=6, team_a_score=13, team_b_score=11, team_a_name="Secret A", team_b_name="Secret B", duration_seconds=2400, rounds=24)
    db.add(match)
    db.flush()
    demo.match_id = match.id
    player = Player(match_id=match.id, steam_id="76561198000000001", name="SecretPlayer", team="CT", team_identity="A")
    db.add(player)
    db.flush()
    db.add(PlayerStats(match_id=match.id, player_id=player.id, kills=24, deaths=17, assists=6, hs_kills=12, hs_rate=0.5, damage=2500, adr=104.2, first_kills=3, weapon_stats={"ak47": {"kills": 10}}))
    db.commit()
    ids = {"demo": demo.id, "pending": pending.id}
    db.close()

    with TestClient(app) as client:
        client.testing_session = TestingSession
        client.ids = ids
        owner_token = client.post("/api/auth/login", json={"email": "share-owner@test.com", "password": "password123"}).json()["access_token"]
        other_token = client.post("/api/auth/login", json={"email": "share-other@test.com", "password": "password123"}).json()["access_token"]
        yield client, {"Authorization": f"Bearer {owner_token}"}, {"Authorization": f"Bearer {other_token}"}
    app.dependency_overrides.clear()


def create_link(client, headers, demo_id, hours=24):
    response = client.post(f"/api/demos/{demo_id}/share", headers=headers, json={"expires_in_hours": hours})
    assert response.status_code == 200
    return response.json()["token"]


def assert_missing(response):
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "SHARE_NOT_FOUND", "message": "分享不存在或已失效"}}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_create_resolve_and_owner_metadata_are_redacted(share_client):
    client, owner, _ = share_client
    demo_id = client.ids["demo"]
    token = create_link(client, owner, demo_id, 48)
    assert len(token) == 43

    db = client.testing_session()
    row = db.query(DemoShare).filter_by(demo_id=demo_id).one()
    assert row.token_hash == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert token not in json.dumps({k: str(v) for k, v in row.__dict__.items()})
    db.close()

    metadata = client.get(f"/api/demos/{demo_id}/share", headers=owner)
    assert metadata.status_code == 200 and metadata.json()["share"]["status"] == "active"
    assert token not in metadata.text and "token_hash" not in metadata.text

    public = client.post("/api/share/resolve", json={"token": token})
    assert public.status_code == 200
    assert public.headers["cache-control"] == "private, no-store"
    assert public.headers["referrer-policy"] == "no-referrer"
    body = public.json()
    assert body["match"] == {"map_name": "de_mirage", "team_a_score": 13, "team_b_score": 11, "rounds": 24, "duration_seconds": 2400}
    # 6R10B：匿名玩家带 rating_pro 主指标（无 raw → unavailable，不当作 0 分）
    assert len(body["players"]) == 1
    p = body["players"][0]
    assert p["player_key"] == "p1"
    assert p["stats"] == {"kills": 24, "deaths": 17, "assists": 6, "hs_rate": 0.5, "damage": 2500, "adr": 104.2, "first_kills": 3}
    assert p["rating_pro"]["status"] == "unavailable"
    assert p["rating_pro"]["rating"] is None
    assert p["rating_pro"]["version"] == "1.0"
    serialized = json.dumps(body, ensure_ascii=False)
    for secret in ("SecretPlayer", "76561198000000001", "secret-match.dem", "Secret A", "Secret B", "demos/"):
        assert secret not in serialized
    for forbidden_key in ("demo_id", "player_id", "user_id", "file_name", "file_path", "steam_id", "heatmaps", "events"):
        assert f'"{forbidden_key}"' not in serialized


def test_rotation_expiry_revoke_and_delete_never_reactivate(share_client):
    client, owner, _ = share_client
    demo_id = client.ids["demo"]
    first = create_link(client, owner, demo_id)
    second = create_link(client, owner, demo_id)
    assert first != second
    assert_missing(client.post("/api/share/resolve", json={"token": first}))
    assert client.post("/api/share/resolve", json={"token": second}).status_code == 200

    db = client.testing_session()
    share = db.query(DemoShare).filter_by(demo_id=demo_id).one()
    share.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    db.commit()
    db.close()
    assert_missing(client.post("/api/share/resolve", json={"token": second}))

    third = create_link(client, owner, demo_id)
    assert client.delete(f"/api/demos/{demo_id}/share", headers=owner).status_code == 200
    assert_missing(client.post("/api/share/resolve", json={"token": third}))
    assert client.delete(f"/api/demos/{demo_id}/share", headers=owner).status_code == 404

    fourth = create_link(client, owner, demo_id)
    assert client.delete(f"/api/demos/{demo_id}", headers=owner).status_code == 200
    assert_missing(client.post("/api/share/resolve", json={"token": fourth}))
    assert client.post(f"/api/demos/{demo_id}/restore", headers=owner).status_code == 200
    assert_missing(client.post("/api/share/resolve", json={"token": fourth}))


def test_share_acl_state_and_validation(share_client):
    client, owner, other = share_client
    demo_id, pending_id = client.ids["demo"], client.ids["pending"]
    assert client.post(f"/api/demos/{demo_id}/share", json={"expires_in_hours": 24}).status_code == 401
    assert client.post(f"/api/demos/{demo_id}/share", headers=other, json={"expires_in_hours": 24}).status_code == 404
    assert client.get(f"/api/demos/{demo_id}/share", headers=other).status_code == 404
    assert client.delete(f"/api/demos/{demo_id}/share", headers=other).status_code == 404
    pending = client.post(f"/api/demos/{pending_id}/share", headers=owner, json={"expires_in_hours": 24})
    assert pending.status_code == 409 and pending.json()["detail"]["code"] == "DEMO_NOT_PARSED"
    for hours in (0, 169):
        assert client.post(f"/api/demos/{demo_id}/share", headers=owner, json={"expires_in_hours": hours}).status_code == 422
    assert_missing(client.post("/api/share/resolve", json={"token": "bad"}))
    assert client.get(f"/api/demos/{demo_id}/share", headers=owner).json() == {"share": None}
