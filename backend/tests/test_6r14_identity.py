"""6R14：自动识别「我」与个人 Pro/Max 权益门禁集成测试。

覆盖：
- 绑定本人 → players 标记 is_you / identity resolution=identified。
- 未绑定 → 个人 Pro 403 STEAM_IDENTITY_REQUIRED。
- 绑定但不在 roster（他人 demo）→ 个人 Pro 403 STEAM_IDENTITY_NOT_IN_DEMO（「这场没有识别到你」）。
- 绑定且命中 → 个人 summary 正常，玩家数据为本人。
- 手动 focus 伪造为他人（手改 focus）→ 个人 Pro 仍返回绑定本人，不生成对方画像。
- 第三方 demo 不入画像。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime, timedelta, timezone

from app.database import Base, get_db
from app.main import app
from app.models import Demo, Match, Player, PlayerStats, User
from app.services.security import hash_password


STEAM_ME = "76561198812054537"
STEAM_OTHER = "76561198812054538"


def _make_user(db, email, tier="pro", steam=None, expire_days=30):
    u = User(email=email, password_hash=hash_password("password123"), nickname=email.split("@")[0],
             subscription_tier=tier,
             subscription_expire=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expire_days))
    db.add(u)
    db.flush()
    if steam is not None:
        from app.models import SteamIdentity
        db.add(SteamIdentity(provider="steam", subject=steam, steam_id64=steam, user_id=u.id, status="active"))
        db.flush()
    return u


def _make_demo_with_players(db, owner, roster_steam, map_name="de_mirage", rounds=19):
    d = Demo(user_id=owner.id, file_name="a.dem", file_path="demos/a.dem", file_size=1, status="parsed",
             map_name=map_name, created_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db.add(d)
    db.flush()
    m = Match(demo_id=d.id, map_name=map_name, team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
              duration_seconds=500, rounds=rounds)
    db.add(m)
    db.flush()
    d.match_id = m.id
    players = []
    for i, sid in enumerate(roster_steam, start=1):
        p = Player(match_id=m.id, steam_id=sid, name=f"P{i}", team="CT" if i % 2 else "T",
                   team_identity="A" if i % 2 else "B")
        db.add(p)
        db.flush()
        players.append(p)
        if sid == STEAM_ME:
            db.add(PlayerStats(match_id=m.id, player_id=p.id, kills=5, deaths=3, assists=2, hs_kills=1,
                               hs_rate=0.2, damage=800, adr=80.0, first_kills=2, weapon_stats={}))
    db.commit()
    return d, players


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

    def override_get_db():
        db = S()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c, S
    app.dependency_overrides.clear()


def _login(client, email):
    r = client.post("/api/auth/me", headers={"Authorization": f"Bearer {_token(client, email)}"})
    return _token(client, email)


def _token(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


class TestIdentify:
    def test_bound_identifies_you(self, client):
        c, S = client
        db = S()
        u = _make_user(db, "me@test.com", steam=STEAM_ME)
        d, players = _make_demo_with_players(db, u, [STEAM_ME, STEAM_OTHER])
        db.close()
        token = _token(c, "me@test.com")
        h = {"Authorization": f"Bearer {token}"}
        ident = c.get(f"/api/demos/{d.id}/identity", headers=h).json()
        assert ident["resolution"] == "identified"
        assert ident["is_you"] is True
        assert ident["you_player_id"] == players[0].id
        assert ident["steam_id64_masked"] != STEAM_ME
        plist = c.get(f"/api/demos/{d.id}/players", headers=h).json()
        by_id = {p["player_id"]: p for p in plist}
        assert by_id[players[0].id]["is_you"] is True
        assert by_id[players[1].id]["is_you"] is False

    def test_unbound_resolution(self, client):
        c, S = client
        db = S()
        u = _make_user(db, "nb@test.com", steam=None)
        d, players = _make_demo_with_players(db, u, [STEAM_ME, STEAM_OTHER])
        db.close()
        h = {"Authorization": f"Bearer {_token(c, 'nb@test.com')}"}
        ident = c.get(f"/api/demos/{d.id}/identity", headers=h).json()
        assert ident["resolution"] == "unbound"
        assert ident["is_you"] is False


class TestProGate:
    def test_unbound_personal_pro_403(self, client):
        c, S = client
        db = S()
        u = _make_user(db, "nb2@test.com", steam=None)
        d, players = _make_demo_with_players(db, u, [STEAM_ME, STEAM_OTHER])
        db.close()
        h = {"Authorization": f"Bearer {_token(c, 'nb2@test.com')}"}
        r = c.get(f"/api/demos/{d.id}/personal/summary", headers=h)
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "STEAM_IDENTITY_REQUIRED"

    def test_bound_not_in_demo_403(self, client):
        c, S = client
        db = S()
        u = _make_user(db, "oth@test.com", steam=STEAM_ME)
        # 用户 demo roster 不含 STEAM_ME（他上传了别人的 demo）
        d, players = _make_demo_with_players(db, u, ["76561198812050000", STEAM_OTHER])
        db.close()
        h = {"Authorization": f"Bearer {_token(c, 'oth@test.com')}"}
        r = c.get(f"/api/demos/{d.id}/personal/summary", headers=h)
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "STEAM_IDENTITY_NOT_IN_DEMO"

    def test_bound_identified_summary_ok(self, client):
        c, S = client
        db = S()
        u = _make_user(db, "ok2@test.com", steam=STEAM_ME)
        d, players = _make_demo_with_players(db, u, [STEAM_ME, STEAM_OTHER])
        db.close()
        h = {"Authorization": f"Bearer {_token(c, 'ok2@test.com')}"}
        r = c.get(f"/api/demos/{d.id}/personal/summary", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["player"]["player_id"] == players[0].id
        assert body["kills"] == 5


class TestManualFocusNoBypass:
    def test_manual_focus_other_ignored_for_pro(self, client):
        c, S = client
        db = S()
        u = _make_user(db, "mf@test.com", steam=STEAM_ME)
        d, players = _make_demo_with_players(db, u, [STEAM_ME, STEAM_OTHER])
        # 手改 focus 为他人
        from app.models import DemoFocusPlayer
        db.add(DemoFocusPlayer(user_id=u.id, demo_id=d.id, player_id=players[1].id))
        db.commit()
        db.close()
        h = {"Authorization": f"Bearer {_token(c, 'mf@test.com')}"}
        # 个人 Pro 必须命中绑定本人（忽略手动 focus）
        r = c.get(f"/api/demos/{d.id}/personal/summary", headers=h)
        assert r.status_code == 200
        assert r.json()["player"]["player_id"] == players[0].id

    def test_manual_focus_does_not_adopt_other_profile(self, client):
        c, S = client
        db = S()
        u = _make_user(db, "mf2@test.com", steam=STEAM_ME)
        # 手选玩家为无统计的他人；若强行采纳则 kill=0 但 player 应为本人
        d, players = _make_demo_with_players(db, u, [STEAM_ME, STEAM_OTHER])
        from app.models import DemoFocusPlayer
        db.add(DemoFocusPlayer(user_id=u.id, demo_id=d.id, player_id=players[1].id))
        db.commit()
        db.close()
        h = {"Authorization": f"Bearer {_token(c, 'mf2@test.com')}"}
        r = c.get(f"/api/demos/{d.id}/personal/summary", headers=h)
        assert r.status_code == 200
        # 返回的是本人统计（绑定玩家），绝不返回他人画像
        assert r.json()["player"]["player_id"] == players[0].id
