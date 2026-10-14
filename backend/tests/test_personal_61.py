"""6R1 测试：事件坐标分离/0 坐标/NaN/环境击杀、focus player 越权、CT-T 筛选、entitlement API。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, DemoFocusPlayer, Event, Match, Player, PlayerStats, RoundSide, SteamIdentity, User
from app.services.security import hash_password
from app.tasks.parse_tasks import _clean_coord


@pytest.fixture
def client():
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
    u2 = User(email="b@test.com", password_hash=hash_password("password123"), nickname="B")
    db.add_all([u, u2])
    db.flush()
    # 6R14：焦点用户 A 绑定到 P1（steam "111"），供个人 Pro 端点识别「我」（身份门禁用）
    db.add(SteamIdentity(provider="steam", subject="111", steam_id64="111", user_id=u.id, status="active"))
    db.flush()
    d = Demo(user_id=u.id, file_name="a.dem", file_path="demos/a.dem", file_size=1, status="parsed",
             map_name="de_mirage", created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2))
    db.add(d)
    db.flush()
    m = Match(demo_id=d.id, map_name="de_mirage", team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
              duration_seconds=500, rounds=19)
    db.add(m)
    db.flush()
    d.match_id = m.id
    p1 = Player(match_id=m.id, steam_id="111", name="P1", team="CT", team_identity="A")
    p2 = Player(match_id=m.id, steam_id="222", name="P2", team="T", team_identity="B")
    db.add_all([p1, p2])
    db.flush()
    db.add(PlayerStats(match_id=m.id, player_id=p1.id, kills=5, deaths=3, assists=2, hs_kills=1, hs_rate=0.2,
                       damage=800, adr=80.0, first_kills=2, weapon_stats={}))
    # kill 事件：p1 杀 p2（坐标分离：actor 与 victim 不同位置）
    db.add_all([
        Event(match_id=m.id, tick=100, round=1, event_type="kill", actor_steam="111", victim_steam="222",
              weapon="ak47", x=0.0, y=0.0, actor_x=100.0, actor_y=200.0, victim_x=300.0, victim_y=400.0),
        # 合法 0 坐标（不应被误判缺失）
        Event(match_id=m.id, tick=200, round=1, event_type="kill", actor_steam="111", victim_steam="222",
              weapon="awp", x=0.0, y=0.0, actor_x=0.0, actor_y=0.0, victim_x=50.0, victim_y=60.0),
        # 环境击杀（attack 缺失）：actor 坐标 NULL
        Event(match_id=m.id, tick=300, round=2, event_type="kill", actor_steam="", victim_steam="222",
              weapon="world", x=0.0, y=0.0, actor_x=None, actor_y=None, victim_x=700.0, victim_y=800.0),
        # p2 杀 p1（p1 死亡事件）
        Event(match_id=m.id, tick=400, round=2, event_type="kill", actor_steam="222", victim_steam="111",
              weapon="deagle", x=0.0, y=0.0, actor_x=900.0, actor_y=910.0, victim_x=500.0, victim_y=510.0),
    ])
    db.add(RoundSide(match_id=m.id, round=1, side_a=2, side_b=3))
    db.add(RoundSide(match_id=m.id, round=2, side_a=3, side_b=2))   # 换边
    db.commit()
    db.close()

    with TestClient(app) as c:
        ta = c.post("/api/auth/login", json={"email": "a@test.com", "password": "password123"}).json()["access_token"]
        tb = c.post("/api/auth/login", json={"email": "b@test.com", "password": "password123"}).json()["access_token"]
        yield c, {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}, S
    app.dependency_overrides.clear()


def _set_tier(S, email, tier_name, expire_days):
    db = S()
    u = db.query(User).filter(User.email == email).one()
    u.subscription_tier = tier_name
    u.subscription_expire = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expire_days)
                             if expire_days is not None else None)
    db.commit()
    db.close()


def _grant_team(S):
    """6R14：给 a@test.com/demo1 建立满足团队门禁的 workspace+roster。"""
    from tests._team_provision import grant_team_entitlement

    db = S()
    try:
        grant_team_entitlement(db, "a@test.com", 1)
    finally:
        db.close()


def _unbind_steam(S, email):
    """移除该用户的 active Steam 绑定（6R14 门禁测试：未绑定 → 403 STEAM_IDENTITY_REQUIRED）。"""
    db = S()
    u = db.query(User).filter(User.email == email).one()
    db.query(SteamIdentity).filter(SteamIdentity.user_id == u.id).delete()
    db.commit()
    db.close()


# ---------- 坐标清洗（parse_tasks._clean_coord） ----------

def test_clean_coord_rules():
    """坐标清洗：is not None + finite；0 合法；NaN/Inf/None → None。"""
    assert _clean_coord(0.0) == 0.0            # 合法 0 坐标
    assert _clean_coord(100.5) == 100.5
    assert _clean_coord(None) is None
    assert _clean_coord(float("nan")) is None
    assert _clean_coord(float("inf")) is None
    assert _clean_coord("bad") is None


# ---------- entitlement / focus / 越权 ----------

def test_entitlement_api_tiers(client):
    c, h, _, S = client
    m = c.get("/api/entitlement", headers=h).json()
    assert m["tier"] == "free"
    assert m["features"]["personal_analysis"] is False
    _set_tier(S, "a@test.com", "max", 30)
    m2 = c.get("/api/entitlement", headers=h).json()
    assert m2["tier"] == "max"
    assert m2["features"]["personal_analysis"] is True
    assert m2["features"]["team_analysis"] is True
    _set_tier(S, "a@test.com", "pro", -1)      # 过期
    m3 = c.get("/api/entitlement", headers=h).json()
    assert m3["tier"] == "free"
    assert m3["expired"] is True


def test_players_list_no_steam_id(client):
    """选择列表绝不返回完整 steam_id。"""
    c, h, _, _ = client
    rows = c.get("/api/demos/1/players", headers=h).json()
    assert len(rows) == 2
    for r in rows:
        assert "steam_id" not in r
        assert "steam" not in str(r)
        assert "player_id" in r


def test_focus_player_set_and_get(client):
    c, h, _, _ = client
    r = c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    assert r.status_code == 200
    assert r.json()["player_id"] == 1
    # 幂等
    r2 = c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    assert r2.json()["player_id"] == 1
    g = c.get("/api/demos/1/focus-player", headers=h).json()
    assert g["player_id"] == 1


def test_focus_player_cross_demo_404(client):
    """越权：B 用户对 A 的 demo 设置 focus → 404（对象隔离）。"""
    c, h, hb, _ = client
    assert c.put("/api/demos/1/focus-player", headers=hb, json={"player_id": 1}).status_code == 404
    assert c.get("/api/demos/1/focus-player", headers=hb).status_code == 404
    assert c.get("/api/demos/1/players", headers=hb).status_code == 404


def test_focus_player_not_in_match_rejected(client):
    c, h, _, S = client
    db = S()
    other = Player(match_id=99999, steam_id="333", name="X", team="CT")
    db.add(other)
    db.commit()
    pid = other.id
    db.close()
    r = c.put("/api/demos/1/focus-player", headers=h, json={"player_id": pid})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "PLAYER_NOT_FOUND"


# ---------- personal summary / combat map ----------

def test_personal_requires_pro_and_focus(client):
    c, h, _, S = client
    # free → 403
    assert c.get("/api/demos/1/personal/summary", headers=h).status_code == 403
    _set_tier(S, "a@test.com", "pro", 30)
    # 6R14：手动 focus 已被身份门禁替代；未绑定 Steam → 403 STEAM_IDENTITY_REQUIRED
    _unbind_steam(S, "a@test.com")
    r = c.get("/api/demos/1/personal/summary", headers=h)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "STEAM_IDENTITY_REQUIRED"


def test_personal_summary_values(client):
    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    s = c.get("/api/demos/1/personal/summary", headers=h).json()
    assert s["player"]["player_id"] == 1
    assert s["kills"] == 5 and s["deaths"] == 3
    assert s["kd"] == round(5 / 3, 2)
    assert s["engagement_rate"] == round(5 / 8, 3)
    assert s["engagement_formula"] == "K/(K+D)"
    assert s["adr"] == 80.0
    assert s["first_kills"] == 2
    assert s["rounds_alive"] == 18   # 19 回合 - 1 个死亡回合（R2）
    assert s["rounds_total"] == 19
    assert s["survival_rate"] == round(18 / 19, 3)


# ---------- 6R10B：Rating Pro v1（主指标） ----------

def test_personal_rating_requires_pro(client):
    """Rating Pro 属于个人分析能力：free → 403；pro/max 可用。"""
    c, h, _, S = client
    assert c.get("/api/demos/1/personal/rating", headers=h).status_code == 403
    _set_tier(S, "a@test.com", "pro", 30)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    assert c.get("/api/demos/1/personal/rating", headers=h).status_code == 200


def test_personal_rating_unavailable_without_raw(client):
    """无 rating_raw → unavailable（不当作 0 分）。"""
    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    r = c.get("/api/demos/1/personal/rating", headers=h).json()
    assert r["rating"] is None
    assert r["status"] == "unavailable"


_RAW_FOR_RATING = {
    "schema": "rating_raw_v1", "version": "1.0", "rounds": 20, "tick_rate": 64.0,
    "components": {
        "frag": {"numerator": 18.0, "denominator": 20, "value": 0.9},
        "survival": {"numerator": 10, "denominator": 20, "value": 0.5},
        "kast": {"numerator": 14, "denominator": 20, "value": 0.7},
        "adr": {"value": 85.0},
        "trade": {"numerator": 3, "denominator": 6, "value": 0.5, "status": "ok"},
    },
}


def test_personal_rating_available_full_shape(client):
    """有 rating_raw → available：总分/版本/分量/人话解释/置信度/覆盖/参考范围齐全，weights 和为 1。"""
    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    db = S()
    st = db.query(PlayerStats).filter(PlayerStats.player_id == 1).one()
    st.rating_raw = _RAW_FOR_RATING
    db.commit()
    db.close()
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    r = c.get("/api/demos/1/personal/rating", headers=h).json()
    assert r["status"] == "available"
    assert isinstance(r["rating"], float) and 0.0 <= r["rating"] <= 3.0
    assert r["version"] == "1.0"
    assert len(r["components"]) == 5
    assert r["confidence"] in ("high", "medium", "low")
    assert r["coverage"]["rounds"] == 20
    assert "非行业" in r["reference_scope"] or "基线" in r["reference_scope"]
    assert "HLTV" in r["not_hltv_note"]
    assert abs(sum(r["weights"].values()) - 1.0) < 1e-9
    assert r["explanation"]


def test_personal_rating_switch_player_no_cache_cross(client):
    """个人评级按绑定身份解析；手动切到他人不改变本人画像（身份优先，不串玩家缓存）。"""
    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    # p1 有 rating_raw
    db = S()
    st = db.query(PlayerStats).filter(PlayerStats.player_id == 1).one()
    st.rating_raw = _RAW_FOR_RATING
    db.commit()
    db.close()
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    r1 = c.get("/api/demos/1/personal/rating", headers=h).json()
    assert r1["status"] == "available"
    # 6R14 身份门禁：手动把焦点切到 P2，个人评级仍按绑定身份解析为 P1（身份优先，不串玩家缓存）
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 2})
    r2 = c.get("/api/demos/1/personal/rating", headers=h).json()
    assert r2["status"] == "available"
    assert r1["player"]["player_id"] == 1
    assert r2["player"]["player_id"] == 1


def test_personal_summary_contains_rating_pro(client):
    """summary 返回 rating_pro 主指标（rating=null 时不当作 0 分）。"""
    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    s = c.get("/api/demos/1/personal/summary", headers=h).json()
    assert "rating_pro" in s
    assert s["rating_pro"]["status"] in ("available", "unavailable")


def test_personal_combat_map_separated_coords(client):
    """kills 坐标来自 actor、deaths 坐标来自 victim；side 为该回合真实阵营。"""
    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    m = c.get("/api/demos/1/personal/combat-map", headers=h).json()

    # p1 的击杀：R1 两条（含合法 0 坐标）+ R2 无（R2 p1 没杀）
    assert len(m["kills"]) == 2
    k0, k1 = m["kills"]
    assert k0["x"] == 100.0 and k0["y"] == 200.0       # actor 坐标
    assert k1["x"] == 0.0 and k1["y"] == 0.0           # 合法 0 坐标保留
    assert k0["side"] == "CT"                           # R1 focus 在 CT（side_a=2）

    # p1 的死亡：R2 一条（p2 杀 p1），坐标来自 victim（500,510）；side=R2 换边后 focus 在 T
    assert len(m["deaths"]) == 1
    d0 = m["deaths"][0]
    assert d0["x"] == 500.0 and d0["y"] == 510.0       # victim 坐标（不是 actor 的 900,910）
    assert d0["side"] == "T"                            # R2 换边后 focus（A 队）在 T 侧


def test_personal_combat_map_ct_t_filter(client):
    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    m_ct = c.get("/api/demos/1/personal/combat-map?side=CT", headers=h).json()
    assert len(m_ct["kills"]) == 2      # R1 两杀都在 CT
    assert len(m_ct["deaths"]) == 0     # 死亡在 T 侧
    m_t = c.get("/api/demos/1/personal/combat-map?side=T", headers=h).json()
    assert len(m_t["kills"]) == 0
    assert len(m_t["deaths"]) == 1
    # 回合筛选
    m_r = c.get("/api/demos/1/personal/combat-map?round_no=2", headers=h).json()
    assert len(m_r["kills"]) == 0
    assert len(m_r["deaths"]) == 1


def test_personal_combat_density_reuses_focus_events(client):
    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    r = c.get("/api/demos/1/personal/combat-density?kind=kills", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["map"] == "de_mirage"
    assert body["kind"] == "kills"
    assert body["data"]


def test_personal_combat_map_has_authoritative_rx_ry(client):
    """6R7A：combat-map 返回服务端按 maps.json 换算的 rx/ry（前端不得自行猜地图换算）。"""
    from app.services.heatmap import load_maps_config
    from app.api.personal import _route_xy

    c, h, _, S = client
    _set_tier(S, "a@test.com", "pro", 30)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    body = c.get("/api/demos/1/personal/combat-map", headers=h).json()
    meta = load_maps_config()["de_mirage"]
    # kills[0] 坐标 (100,200)；断言 rx/ry 与 _route_xy 一致
    k = body["kills"][0]
    assert k["x"] == 100.0 and k["y"] == 200.0
    assert k["rx"] == round((100 - meta["pos_x"]) / meta["scale"] / 1024, 5)
    assert k["ry"] == round((meta["pos_y"] - 200) / meta["scale"] / 1024, 5)
    # deaths 也含 rx/ry
    d = body["deaths"][0]
    assert d["rx"] is not None and d["ry"] is not None
    # 无坐标事件（环境击杀 attack 缺失）：rx/ry 为 null 而非伪造 0
    env = next((x for x in body["kills"] if x["weapon"] == "world" or (x["x"] is None)), None)
    if env is not None:
        assert env["rx"] is None and env["ry"] is None


# ---------- 6R7B gate4：团队交战图（与个人交战图数据范围隔离） ----------

def test_team_combat_map_requires_max_and_separate_range(client):
    """团队交战图为独立数据范围（含全部玩家），与个人（focus）隔离；权限 max。"""
    c, h, _, S = client
    # free → 403；pro → 403（Max-only）
    assert c.get("/api/demos/1/team/combat-map", headers=h).status_code == 403
    _set_tier(S, "a@test.com", "pro", 30)
    assert c.get("/api/demos/1/team/combat-map", headers=h).status_code == 403
    _set_tier(S, "a@test.com", "max", 30)
    _grant_team(S)

    body = c.get("/api/demos/1/team/combat-map", headers=h).json()
    # 全队数据范围：4 条 kill 事件全部纳入（含环境击杀、p2 击杀者）
    assert len(body["kills"]) == 4
    assert len(body["deaths"]) == 4

    # 与个人交战图隔离：个人只含 focus(p1) 的事件
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    personal = c.get("/api/demos/1/personal/combat-map", headers=h).json()
    assert len(personal["kills"]) == 2 and len(personal["deaths"]) == 1
    assert len(body["kills"]) > len(personal["kills"])    # 团队范围更广（隔离成立）


def test_team_combat_map_filters(client):
    c, h, _, S = client
    _set_tier(S, "a@test.com", "max", 30)
    _grant_team(S)
    # team=A：kill 只含攻击者 A 队（p1，2 条）；R2 p2 杀 p1 的 actor 是 B，不含
    a = c.get("/api/demos/1/team/combat-map?team=A", headers=h).json()
    assert all(k["team"] == "A" for k in a["kills"])
    assert len(a["kills"]) == 2
    # 环境击杀（team=None）：即使在 team=A 下也不该被算作 A 的击杀
    assert all(k["x"] is not None and k["y"] is not None for k in a["kills"])
    # team=B 的死亡（受害者 B 队）
    b_deaths = c.get("/api/demos/1/team/combat-map?team=B", headers=h).json()["deaths"]
    assert b_deaths and all(d["team"] == "B" for d in b_deaths)
    # side 过滤：R1 该队在 CT（side_a=2 → A=CT）
    ct = c.get("/api/demos/1/team/combat-map?team=A&side=CT", headers=h).json()
    assert all(k["side"] == "CT" for k in ct["kills"])
    assert all(k["x"] is not None for k in ct["kills"])
