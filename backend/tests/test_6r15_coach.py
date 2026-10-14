"""6R15：长期教练确定性事实层 / 身份隔离 / 权益 / 10-30 窗口 / 重复 / 趋势 / Top3 / 迁移 集成测试。

全部用 in-memory sqlite + TestClient；不发起任何真实网络/AI 调用；证据秒/区域在无战术 artifact 时诚实降级。
"""
import json
import gzip
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, Event, Match, Player, PlayerStats, SteamIdentity, TacticalArtifact, User
from app.services.coach import COACH_METRIC_VERSION, compute_match_snapshot, profile_snapshot
from app.services.security import hash_password

STEAM_ME = "76561198812054537"
STEAM_OTHER = "76561198812054538"
STEAM_3RD = "76561198812054539"


def _u(db, email, tier="max", steam=STEAM_ME, expire_days=365, status="active"):
    user = User(email=email, password_hash=hash_password("password123"), nickname=email.split("@")[0],
                subscription_tier=tier,
                subscription_expire=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expire_days))
    db.add(user); db.flush()
    if steam:
        si = SteamIdentity(provider="steam", subject=steam, steam_id64=steam, user_id=user.id, status=status)
        db.add(si); db.flush()
    db.commit()
    return user


def _mk_match(db, owner, roster_steam, map_name="de_mirage", rounds=19, created_at=None,
              kd_stats=None, idx=0, me_extra=None):
    """创建一场 demo+match+roster+本人 stats；kd_stats={kills,deaths,adr,hs_rate,first_kills}。"""
    d = Demo(user_id=owner.id, file_name=f"a{idx}.dem", file_path=f"demos/a{idx}.dem", file_size=1,
             status="parsed", map_name=map_name,
             created_at=(created_at or datetime.now(timezone.utc).replace(tzinfo=None)))
    db.add(d); db.flush()
    m = Match(demo_id=d.id, map_name=map_name, team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
              duration_seconds=500, rounds=rounds)
    db.add(m); db.flush()
    d.match_id = m.id
    players = []
    for i, sid in enumerate(roster_steam, start=1):
        p = Player(match_id=m.id, steam_id=sid, name=f"P{i}", team="CT" if i % 2 else "T",
                   team_identity="A" if i % 2 else "B")
        db.add(p); db.flush()
        players.append(p)
        if sid == STEAM_ME:
            ks = (kd_stats or {})
            db.add(PlayerStats(match_id=m.id, player_id=p.id,
                               kills=ks.get("kills", 5), deaths=ks.get("deaths", 3),
                               assists=2, hs_kills=ks.get("hs_kills", 1),
                               hs_rate=ks.get("hs_rate", 0.2),
                               damage=ks.get("damage", 800), adr=ks.get("adr", 80.0),
                               first_kills=ks.get("first_kills", 2), clutches_won=0, weapon_stats={}))
    db.commit()
    return d, m, players


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


def _token(c, email):
    r = c.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(c, email):
    return {"Authorization": f"Bearer {_token(c, email)}"}


# ---------------- 身份隔离 + 权益 ----------------

def test_free_locked_status(client):
    c, S = client
    db = S()
    _u(db, "f@t.com", tier="free", steam=None)
    db.close()
    r = c.get("/api/coach/status", headers=_h(c, "f@t.com"))
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "free" and body["locked"] is True and body["max_window"] == 0


def test_unbound_profile_403(client):
    c, S = client
    db = S()
    _u(db, "u@t.com", tier="pro", steam=None)
    db.close()
    r = c.get("/api/coach/profile", headers=_h(c, "u@t.com"))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "STEAM_IDENTITY_REQUIRED"


def test_pro_window_clamped_to_10(client):
    c, S = client
    db = S()
    u = _u(db, "pr@t.com", tier="pro", steam=STEAM_ME)
    for i in range(12):
        _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=i)
    db.close()
    body = c.get("/api/coach/profile?window=30", headers=_h(c, "pr@t.com")).json()
    assert body["window"] == 10          # Pro 请求 30 → 只给 10
    assert body["coverage"]["window_10"]["available"] == 10
    assert body["privileges"]["can_toggle_30"] is False


def test_third_party_demo_not_in_profile(client):
    c, S = client
    db = S()
    u = _u(db, "tp@t.com", tier="max", steam=STEAM_ME)
    db.add(Demo(user_id=u.id, file_name="x.dem", file_path="demos/x.dem", file_size=1, status="parsed",
                map_name="de_mirage", created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    db.flush()
    # 该 roster 不含 STEAM_ME（第三方 demo，仅 STEAM_3RD + STEAM_OTHER）
    _mk_match(db, u, [STEAM_3RD, STEAM_OTHER], idx=0, kd_stats=None)
    db.close()
    body = c.get("/api/coach/profile?window=30", headers=_h(c, "tp@t.com")).json()
    assert body["coverage"]["total_matches_available"] == 0   # 本人不在该 demo → 未纳入


def test_manual_focus_cannot_switch_identity(client):
    c, S = client
    db = S()
    u = _u(db, "mf@t.com", tier="max", steam=STEAM_ME)
    d, m, players = _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=0, kd_stats={"kills": 5, "deaths": 3})
    from app.models import DemoFocusPlayer
    # 手动 focus 到他人（STEAM_OTHER 对应的 player）
    other_p = next(p for p in players if p.steam_id == STEAM_OTHER)
    db.add(DemoFocusPlayer(user_id=u.id, demo_id=d.id, player_id=other_p.id))
    db.commit()
    db.close()
    # 教练主体仍是绑定本人（STEAM_ME）
    snap = profile_snapshot(S(), S().get(User, u.id), window=10)
    assert snap.steam_id64 == STEAM_ME


# ---------------- 幂等 / hash 确定性 ----------------

def test_hash_deterministic_twice(client):
    c, S = client
    db = S()
    u = _u(db, "det@t.com", tier="max", steam=STEAM_ME)
    for i in range(6):
        _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=i,
                  kd_stats={"kills": 5, "deaths": 3, "adr": 80.0, "hs_rate": 0.2, "first_kills": 2})
    db.close()
    a = profile_snapshot(S(), S().get(User, db.scalar(select(User).where(User.email == "det@t.com")).id), window=10)
    b = profile_snapshot(S(), S().get(User, db.scalar(select(User).where(User.email == "det@t.com")).id), window=10)
    assert a.content_hash == b.content_hash


# ---------------- 10/30 窗口 + 少样本 ----------------

def test_fewer_matches_underfilled(client):
    c, S = client
    db = S()
    u = _u(db, "few@t.com", tier="max", steam=STEAM_ME)
    for i in range(5):
        _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=i,
                  kd_stats={"kills": 5, "deaths": 3, "adr": 80.0, "hs_rate": 0.2, "first_kills": 2})
    db.close()
    u2 = S().scalar(select(User).where(User.email == "few@t.com"))
    body = c.get("/api/coach/profile?window=10", headers=_h(c, "few@t.com")).json()
    assert body["coverage"]["window_10"]["available"] == 5
    assert body["coverage"]["window_10"]["max"] == 10
    assert body["coverage"]["window_30"]["max"] == 30
    assert body["states"]["status"] == "underfilled"


# ---------------- 时间排序 ----------------

def test_window_takes_most_recent(client):
    c, S = client
    db = S()
    u = _u(db, "ord@t.com", tier="max", steam=STEAM_ME)
    base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=20)
    for i in range(5):
        _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=i, created_at=base + timedelta(days=i))
    db.close()
    body = c.get("/api/coach/profile?window=10", headers=_h(c, "ord@t.com")).json()
    assert body["windows"]["matches"][-1] == 5  # 最近一场（created_at 最晚）排最后


# ---------------- 零机会 / 重复 / 趋势 / Top3 ----------------

def test_zero_opportunity_no_opportunity(client):
    c, S = client
    db = S()
    u = _u(db, "zero@t.com", tier="max", steam=STEAM_ME)
    # kills=0, deaths=0 → hs_rate 分母 0（no_opportunity，绝非 0 分）；kd 无法算（insufficient）
    d, m, players = _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=0,
                              kd_stats={"kills": 0, "deaths": 0, "adr": 0.0, "hs_rate": 0.0, "first_kills": 0})
    db.close()
    snap = compute_match_snapshot(S(), S().scalar(select(User).where(User.email == "zero@t.com")), d)
    assert snap.facts["hs_rate"]["status"] == "no_opportunity"
    assert snap.facts["kd_ratio"]["status"] == "insufficient"
    assert snap.facts["hs_rate"]["rate"] == 0.0  # 保存原始值，但状态为 no_opportunity（前端按状态显示「暂无样本」）


def test_repeat_requires_two_and_top3(client):
    c, S = client
    db = S()
    u = _u(db, "rep@t.com", tier="max", steam=STEAM_ME)
    # 前 2 场低 ADR（<70）+ 高死亡；后 2 场正常 → low_adr/high_deaths 命中>=2 → 重复
    for i in range(4):
        low = i < 2
        _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=i,
                  kd_stats={"kills": 5, "deaths": (12 if low else 4), "adr": (55.0 if low else 85.0),
                            "hs_rate": 0.2, "first_kills": (0 if low else 3)})
    db.close()
    snap = profile_snapshot(S(), S().scalar(select(User).where(User.email == "rep@t.com")), window=10)
    rep = {r["candidate_id"]: r for r in snap.repeated_problems}
    assert rep["low_adr"]["repeat"] is True and rep["low_adr"]["hits"] == 2
    assert rep["high_deaths"]["repeat"] is True and rep["high_deaths"]["hits"] == 2
    # top3 按 score 排序，含人话字段与证据数
    assert snap.top3 and all(t["rank"] == i for i, t in enumerate(snap.top3, start=1))
    assert all(t["why"] and t["advice"] and t["human_name"] for t in snap.top3)


def test_trend_insufficient_when_few(client):
    c, S = client
    db = S()
    u = _u(db, "tr@t.com", tier="max", steam=STEAM_ME)
    for i in range(3):
        _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=i, kd_stats={"kills": 5, "deaths": 4, "adr": 80.0})
    db.close()
    snap = profile_snapshot(S(), S().scalar(select(User).where(User.email == "tr@t.com")), window=10)
    assert any(t.get("status") == "insufficient" for t in snap.trends)


def test_evidence_tuple_shape_client(client):
    c, S = client
    db = S()
    u = _u(db, "ev@t.com", tier="max", steam=STEAM_ME)
    d, m, players = _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=0,
                              kd_stats={"kills": 5, "deaths": 9, "adr": 55.0})
    # 无战术 artifact → 证据 seconds=None（诚实降级），但必须有完整字段形状
    for r in (1, 2, 3):
        db.add(Event(match_id=m.id, tick=100 + r, round=r, event_type="kill",
                     actor_steam=STEAM_ME, victim_steam=STEAM_OTHER, weapon="ak47"))
    db.commit()
    db.close()
    snap = compute_match_snapshot(S(), S().scalar(select(User).where(User.email == "ev@t.com")), d)
    evs = snap.evidence
    assert evs, "本人参与的 kill/death 事件应产生证据锚"
    for e in evs:
        assert {"demo_id", "round_no", "seconds", "region", "metric_id", "players", "layers", "highlight"} <= set(e)
        assert e["seconds"] is None or isinstance(e["seconds"], (int, float))
        assert e["region"] == "未知区域" or bool(e["region"])
        assert isinstance(e["players"], list) and isinstance(e["layers"], list)
        assert e["time_precision"] == "round"  # 无 artifact → round 级


# ---------------- 解绑 ----------------

def test_unbind_stops_profile(client):
    c, S = client
    db = S()
    u = _u(db, "ub@t.com", tier="max", steam=STEAM_ME)
    _mk_match(db, u, [STEAM_ME, STEAM_OTHER], idx=0, kd_stats={"kills": 5, "deaths": 3})
    si = db.scalar(select(SteamIdentity).where(SteamIdentity.user_id == u.id))
    si.status = "unbound"; si.user_id = None
    db.commit()
    db.close()
    r = c.get("/api/coach/profile", headers=_h(c, "ub@t.com"))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "STEAM_IDENTITY_REQUIRED"
