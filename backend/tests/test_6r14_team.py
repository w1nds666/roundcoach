"""6R14：Max 团队工作区 + 实名 roster + 门禁 + 反滥用 集成测试。

覆盖：Max 才可建团队、一 Max 一团队、成员须 Steam 验证、座位上限、唯一 Steam 单团队、
团队报告门禁（≥3 active 成员且含调用者本人）、非成员拒绝、Free/Pro 内容可看。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime, timedelta, timezone

from app.database import Base, get_db
from app.main import app
from app.models import Demo, Match, Player, SteamIdentity, User
from app.services.security import hash_password


def _mk_user(db, email, tier="max", steam=None):
    u = User(email=email, password_hash=hash_password("password123"), nickname=email.split("@")[0],
             subscription_tier=tier,
             subscription_expire=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30))
    db.add(u)
    db.flush()
    if steam:
        db.add(SteamIdentity(provider="steam", subject=steam, steam_id64=steam, user_id=u.id, status="active"))
        db.flush()
    db.commit()
    return u


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


def _token(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


class TestWorkspace:
    def test_create_requires_max(self, client):
        c, S = client
        db = S()
        us = _mk_user(db, "free@test.com", tier="free", steam="76561198812050001")
        db.close()
        r = c.post("/api/team", json={"name": "T"}, headers=_h(_token(c, "free@test.com")))
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"

    def test_create_ok_and_max_one(self, client):
        c, S = client
        db = S()
        us = _mk_user(db, "max@test.com", tier="max", steam="76561198812050002")
        db.close()
        h = _h(_token(c, "max@test.com"))
        r = c.post("/api/team", json={"name": "TeamA"}, headers=h)
        assert r.status_code == 200, r.text
        r2 = c.post("/api/team", json={"name": "TeamB"}, headers=h)
        assert r2.status_code == 409
        assert r2.json()["detail"]["code"] == "TEAM_MAX_ONE"

    def test_add_member_requires_verified(self, client):
        c, S = client
        db = S()
        owner = _mk_user(db, "owner@test.com", tier="max", steam="76561198812050003")
        member = _mk_user(db, "mem@test.com", tier="max", steam=None)  # 未验证
        db.close()
        h = _h(_token(c, "owner@test.com"))
        c.post("/api/team", json={"name": "T"}, headers=h)
        r = c.post("/api/team/members", json={"user_id": member.id}, headers=h)
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "TEAM_VERIFY_REQUIRED"

    def test_add_member_ok(self, client):
        c, S = client
        db = S()
        owner = _mk_user(db, "owner2@test.com", tier="max", steam="76561198812050004")
        member = _mk_user(db, "mem2@test.com", tier="max", steam="76561198812050005")
        db.close()
        h = _h(_token(c, "owner2@test.com"))
        c.post("/api/team", json={"name": "T"}, headers=h)
        r = c.post("/api/team/members", json={"user_id": member.id}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["member"]["role"] == "member"
        g = c.get("/api/team", headers=h).json()
        assert len(g["members"]) == 2


class TestGate:
    def _setup_team(self, c, S, roster, caller_email="gow@test.com"):
        """建团队：owner(roster[0]) + 3 成员(roster[1:4])，均绑定 steam；依次经 API 加入。"""
        db = S()
        owner = _mk_user(db, "gow@test.com", tier="max", steam=roster[0])
        member_ids = []
        for i, sid in enumerate(roster[1:4], start=1):
            u = _mk_user(db, f"g{i}@test.com", tier="max", steam=sid)
            member_ids.append(u.id)
        db.close()
        h = _h(_token(c, "gow@test.com"))
        c.post("/api/team", json={"name": "G"}, headers=h)
        for mid in member_ids:
            c.post("/api/team/members", json={"user_id": mid}, headers=h)
        return owner

    def _make_demo_with(self, S, player_steam_list, sortkey="x"):
        """建一个 demo（owner 为 gow），roster 为给定 steam 列表。返回 demo_id。"""
        db = S()
        owner = db.query(User).filter(User.email == "gow@test.com").first()
        d = Demo(user_id=owner.id, file_name=f"{sortkey}.dem", file_path=f"demos/{sortkey}.dem", file_size=1,
                 status="parsed", map_name="de_mirage", created_at=datetime.now(timezone.utc).replace(tzinfo=None))
        db.add(d); db.flush()
        m = Match(demo_id=d.id, map_name="de_mirage", team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
                  duration_seconds=500, rounds=19)
        db.add(m); db.flush(); d.match_id = m.id
        for i, sid in enumerate(player_steam_list, start=1):
            db.add(Player(match_id=m.id, steam_id=sid, name=f"G{i}", team="CT" if i % 2 else "T",
                          team_identity="A" if i % 2 else "B"))
        db.commit()
        did = d.id
        db.close()
        return did

    def test_gate_ok_with_3_and_self(self, client):
        c, S = client
        roster = ["76561198812060001", "76561198812060002", "76561198812060003", "76561198812060004"]
        self._setup_team(c, S, roster)
        # 团队 4 成员（owner + 3），demo roster 含全部 4 → matched=4 ≥3 且含 owner
        did = self._make_demo_with(S, roster)
        h = _h(_token(c, "gow@test.com"))
        r = c.get(f"/api/team/gate/{did}", headers=h).json()
        assert r["ok"] is True
        assert r["matched"] >= 3
        assert r["caller_included"] is True

    def test_gate_low_matched_below_3(self, client):
        c, S = client
        roster = ["76561198812060101", "76561198812060102", "76561198812060103", "76561198812060104"]
        self._setup_team(c, S, roster)
        # demo roster 只含 2 个成员 → matched=2 < 3 → gate low
        did = self._make_demo_with(S, roster[:2])
        h = _h(_token(c, "gow@test.com"))
        r = c.get(f"/api/team/gate/{did}", headers=h).json()
        assert r["ok"] is False
        assert r["matched"] < 3
        assert r["reason"] is not None

    def test_gate_low_caller_not_included(self, client):
        c, S = client
        roster = ["76561198812060201", "76561198812060202", "76561198812060203", "76561198812060204"]
        self._setup_team(c, S, roster)
        # demo roster 含 4 个成员但都不含 owner(sid=roster[0])？含 roster[1:4]=3 个成员 + 一个非成员
        demo_roster = roster[1:4] + ["76561198812060999"]
        did = self._make_demo_with(S, demo_roster)
        h = _h(_token(c, "gow@test.com"))
        r = c.get(f"/api/team/gate/{did}", headers=h).json()
        assert r["ok"] is False
        assert r["caller_included"] is False

    def test_non_member_gate_rejected(self, client):
        c, S = client
        db = S()
        outsider = _mk_user(db, "out@test.com", tier="max", steam="76561198812060301")
        # outsider 自己的 demo（非成员）
        d = Demo(user_id=outsider.id, file_name="n.dem", file_path="demos/n.dem", file_size=1, status="parsed",
                 map_name="de_mirage", created_at=datetime.now(timezone.utc).replace(tzinfo=None))
        db.add(d); db.flush()
        m = Match(demo_id=d.id, map_name="de_mirage", team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
                  duration_seconds=500, rounds=19)
        db.add(m); db.flush(); d.match_id = m.id
        db.add(Player(match_id=m.id, steam_id="76561198812060301", name="N1", team="CT", team_identity="A"))
        db.commit(); did = d.id; db.close()
        h = _h(_token(c, "out@test.com"))
        r = c.get(f"/api/team/gate/{did}", headers=h).json()
        assert r["ok"] is False
        assert "非团队成员" in r["reason"]


class TestEntitlementEndpoint:
    """6R14：团队付费数据端点的服务端强制门禁（require_team_demo_entitlement）。

    覆盖：合法 roster(5人) 成功；低重合(<3) 403 TEAM_GATE_LOW；调用者不在场 403 TEAM_CALLER_NOT_IN_DEMO；
    非成员 403 TEAM_NOT_MEMBER；roster 不足 5-7 403 TEAM_ROSTER_SIZE；个人端点不被团队门禁误伤。
    均通过真实端点（team/stats、personal/summary）验证，不依赖前端先调 gate。
    """

    def _build_workspace(self, c, S, roster_steams):
        """建 workspace：owner(roster[0]) + 其余成员；bot 邮箱按序。返回 owner email。"""
        db = S()
        owner = _mk_user(db, "ep-owner@test.com", tier="max", steam=roster_steams[0])
        member_ids = []
        for i, sid in enumerate(roster_steams[1:], start=1):
            u = _mk_user(db, f"ep-m{i}@test.com", tier="max", steam=sid)
            member_ids.append(u.id)
        db.close()
        h = _h(_token(c, "ep-owner@test.com"))
        c.post("/api/team", json={"name": "EP Team"}, headers=h)
        for mid in member_ids:
            c.post("/api/team/members", json={"user_id": mid, "slot_type": "main"}, headers=h)
        return "ep-owner@test.com"

    def _make_demo(self, S, owner_email, player_steams, sortkey):
        db = S()
        owner = db.query(User).filter(User.email == owner_email).first()
        d = Demo(user_id=owner.id, file_name=f"{sortkey}.dem", file_path=f"demos/{sortkey}.dem", file_size=1,
                 status="parsed", map_name="de_mirage", created_at=datetime.now(timezone.utc).replace(tzinfo=None))
        db.add(d); db.flush()
        m = Match(demo_id=d.id, map_name="de_mirage", team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
                  duration_seconds=500, rounds=19)
        db.add(m); db.flush(); d.match_id = m.id
        for i, sid in enumerate(player_steams, start=1):
            db.add(Player(match_id=m.id, steam_id=sid, name=f"P{i}", team="CT" if i % 2 else "T",
                          team_identity="A" if i % 2 else "B"))
        db.commit(); did = d.id; db.close()
        return did

    def _summon(self, c, token, did, path):
        return c.get(f"/api/demos/{did}{path}", headers=_h(token))

    def test_legal_roster_success(self, client):
        c, S = client
        roster = ["76561198812070001", "76561198812070002", "76561198812070003",
                  "76561198812070004", "76561198812070005"]
        email = self._build_workspace(c, S, roster)
        did = self._make_demo(S, email, roster, "ok")
        r = self._summon(c, _token(c, email), did, "/team/combat-map")
        assert r.status_code == 200, r.text
        # 团队汇总端点（需 Max + roster gate）也放行
        r2 = self._summon(c, _token(c, email), did, "/team-stats")
        assert r2.status_code == 200, r2.text

    def test_low_overlap_403(self, client):
        c, S = client
        roster = ["76561198812070101", "76561198812070102", "76561198812070103",
                  "76561198812070104", "76561198812070105"]
        email = self._build_workspace(c, S, roster)
        # demo 只含 2 个 roster 成员 → 低重合
        did = self._make_demo(S, email, roster[:2], "low")
        r = self._summon(c, _token(c, email), did, "/team/combat-map")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "TEAM_GATE_LOW"

    def test_caller_not_in_demo_403(self, client):
        c, S = client
        roster = ["76561198812070201", "76561198812070202", "76561198812070203",
                  "76561198812070204", "76561198812070205"]
        email = self._build_workspace(c, S, roster)
        # demo 含 3 个成员（不含 caller=owner roster[0]）→ 且重合=3 但 caller 不在 → 403 caller
        did = self._make_demo(S, email, roster[1:4], "nocaller")
        r = self._summon(c, _token(c, email), did, "/team/combat-map")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "TEAM_CALLER_NOT_IN_DEMO"

    def test_non_member_403(self, client):
        c, S = client
        roster = ["76561198812070301", "76561198812070302", "76561198812070303",
                  "76561198812070304", "76561198812070305"]
        self._build_workspace(c, S, roster)
        # 另一个用户（不在任何 workspace）用自己的 demo 调团队端点
        db = S()
        outsider = _mk_user(db, "ep-out@test.com", tier="max", steam="76561198812070309")
        db.close()
        did = self._make_demo(S, "ep-out@test.com", ["76561198812070309"], "nm")
        r = self._summon(c, _token(c, "ep-out@test.com"), did, "/team/combat-map")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "TEAM_NOT_MEMBER"

    def test_roster_size_403(self, client):
        c, S = client
        # 只有 4 人 → <5 → 403 TEAM_ROSTER_SIZE
        roster = ["76561198812070401", "76561198812070402", "76561198812070403", "76561198812070404"]
        email = self._build_workspace(c, S, roster)
        did = self._make_demo(S, email, roster, "size")
        r = self._summon(c, _token(c, email), did, "/team/combat-map")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "TEAM_ROSTER_SIZE"

    def test_personal_endpoint_not_affected(self, client):
        # 个人端点走身份门禁（require_pro_focus），不受团队 roster 门禁影响
        c, S = client
        db = S()
        us = _mk_user(db, "ep-me@test.com", tier="pro", steam="76561198812070501")
        d = Demo(user_id=us.id, file_name="m.dem", file_path="demos/m.dem", file_size=1, status="parsed",
                 map_name="de_mirage", created_at=datetime.now(timezone.utc).replace(tzinfo=None))
        db.add(d); db.flush()
        m = Match(demo_id=d.id, map_name="de_mirage", team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
                  duration_seconds=500, rounds=19)
        db.add(m); db.flush(); d.match_id = m.id
        p = Player(match_id=m.id, steam_id="76561198812070501", name="ME", team="CT", team_identity="A")
        db.add(p); db.flush()
        from app.models import PlayerStats
        db.add(PlayerStats(match_id=m.id, player_id=p.id, kills=5, deaths=3, assists=2, hs_kills=1, hs_rate=0.2,
                           damage=800, adr=80.0, first_kills=2, weapon_stats={}))
        db.commit(); did = d.id; db.close()
        # 无 workspace 也不影响个人 Pro（身份门禁识别本人）
        r = c.get(f"/api/demos/{did}/personal/summary", headers=_h(_token(c, "ep-me@test.com")))
        assert r.status_code == 200, r.text
        assert r.json()["player"]["player_id"] == p.id
