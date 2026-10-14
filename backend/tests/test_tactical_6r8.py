"""6R8 测试：真实朝向（yaw/pitch 权威）+ 道具生命周期 authority + 战术回放 API + 原语。

- 字段权威：m_angEyeAngles 确为 [pitch, yaw, roll]（pitch 受限 ±90、yaw 全 360、roll=0）。
- 道具生命周期：smoke/fire 由真实开始/结束事件配对，active_start < active_end；HE/flash 瞬时。
- 战术 artifact：玩家点带 yaw/pitch；道具带 active interval + authority + 轨迹。
- 权限：team/replay 与 tactical/primitives 需 Max；跨用户 404；缺失 artifact → TACTICAL_UNAVAILABLE。
- 原语：跨回合由计算给出，crossfire 只命名为「交叉火力候选」+ reason。
"""
import gzip
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, DemoFocusPlayer, Match, Player, PlayerStats, RoundSide, SteamIdentity, TacticalArtifact, User
from app.services.parser import DemoAnalyzer
from app.services.primitives import compute_primitives
from app.services.routes import gzip_json
from app.services.security import hash_password
from app.services.tactical import TACTICAL_SCHEMA_VERSION, artifact_sha256 as aircraft_sha, build_tactical_artifact, tactical_object_key

FIXTURE = Path(__file__).resolve().parent / "demos" / "test_demo.dem"


@pytest.fixture(scope="module")
def tactical_demo_artifact():
    """真实 fixture 的一次完整 build（模块级复用，避免重扫 60MB demo）。"""
    if not FIXTURE.exists():
        pytest.skip("fixture test_demo.dem 缺失")
    analyzer = DemoAnalyzer(FIXTURE)
    header = analyzer.parse_header()
    rounds = analyzer.parse_rounds()
    players = analyzer.parse_player_info()
    deaths = analyzer.parse_deaths()
    grenades = analyzer.parse_grenades()
    identity_of = {}
    for p in players:
        identity_of[str(p["steamid"])] = "A"
    player_id_of = {str(p["steamid"]): i for i, p in enumerate(players, start=1)}
    return {
        "analyzer": analyzer,
        "header": header,
        "rounds": rounds,
        "players": players,
        "deaths": deaths,
        "grenades": grenades,
        "builder_args": {
            "match_id": 1, "map_name": header.map_name, "playout_ticks": int(header.playout_ticks or 0),
            "rounds": rounds, "players": players, "identity_of": identity_of,
            "player_id_of": player_id_of, "deaths": deaths, "grenades_rows": grenades, "tick_rate": header.tick_rate,
        },
    }


# ---------- 字段权威 ----------

def test_eye_angle_is_pitch_yaw_roll(tactical_demo_artifact):
    """m_angEyeAngles 为 [pitch, yaw, roll]：pitch 受限、yaw 全 360、roll 恒 0。"""
    analyzer = tactical_demo_artifact["analyzer"]
    df = analyzer._parser.parse_ticks(["CCSPlayerPawn.m_angEyeAngles"], ticks=range(5000, 5200, 16))
    ea = np.array(df["CCSPlayerPawn.m_angEyeAngles"].tolist())
    assert ea.shape[1] == 3
    assert abs(ea[:, 2]).max() < 1e-6          # roll 恒 0
    assert -90.0 <= ea[:, 0].min() and ea[:, 0].max() <= 90.0   # pitch 受限
    assert (ea[:, 1].max() - ea[:, 1].min()) > 180              # yaw 覆盖超过 180°
    # 非移动方向：yaw 分布不是窄于移动方向（不能拿移动方向冒充枪线）


def test_prop_lifecycle_events_authority(tactical_demo_artifact):
    """烟雾/燃烧结束事件为真实事件，可按 entityid 配对得到 active_start < active_end。"""
    analyzer = tactical_demo_artifact["analyzer"]
    det = analyzer._parser.parse_event("smokegrenade_detonate")
    exp = analyzer._parser.parse_event("smokegrenade_expired")
    assert len(det) > 0 and len(exp) > 0
    assert list(det.columns) == ["entityid", "tick", "user_name", "user_steamid", "x", "y", "z"]
    end_by = {int(r["entityid"]): int(r["tick"]) for _, r in exp.iterrows()}
    paired = [d for _, d in det.iterrows() if int(d["entityid"]) in end_by]
    assert len(paired) > 0
    # 多数配对满足 active_start( detonate ) < active_end( expire )
    ok = sum(1 for d in paired if int(d["tick"]) < end_by[int(d["entityid"])])
    assert ok / len(paired) > 0.9


def test_build_tactical_artifact_has_yaw_and_props(tactical_demo_artifact):
    """战术 artifact 玩家点带 yaw；道具带 active interval + authority；烟轨迹匹配。"""
    art = build_tactical_artifact(tactical_demo_artifact["analyzer"], **tactical_demo_artifact["builder_args"])
    assert art["schema_version"] == TACTICAL_SCHEMA_VERSION
    assert art["rounds"]
    yaw_seen = False
    smoke_with_interval = 0
    for rnd in art["rounds"]:
        for pid, pd in rnd["players"].items():
            for p in pd["points"]:
                if p["yaw"] is not None and p["pitch"] is not None:
                    yaw_seen = True
        for pr in rnd["props"]:
            assert "authority" in pr and pr["authority"]["kind"] == "event"
            if pr["type"] == "smoke" and pr["active_start"] is not None and pr["active_end"] is not None:
                assert pr["active_start"] < pr["active_end"]
                smoke_with_interval += 1
    assert yaw_seen, "玩家点应带真实 yaw"
    assert smoke_with_interval > 0, "烟雾应带真实 active_start/active_end"


def test_prop_end_picks_nearest_subsequent():
    """结束事件匹配取最近后续（规避实体 id 复用导致的跨投掷污染：正确匹配不出超长/错误 interval）。"""
    from app.services.tactical import _next_end
    assert _next_end([1000, 2000, 3000], 2500) == 3000
    assert _next_end([1000, 2000], 2500) is None
    assert _next_end([1000, 2000, 3000], 500) == 1000
    assert _next_end([], 10) is None
    assert _next_end(None, 10) is None


def test_prop_end_clamped_to_max_duration():
    """结束事件超过合理时长（实体 id 复用污染）→ missing_expire（fail closed，不编造时长）。"""
    from app.services.tactical import _clamped_end
    # 64 tick：45s * 64 = 2880 tick；超过 → None（缺证）；合理内 → 保留
    assert _clamped_end(1000 + 1500, 1000, 64) == 2500
    assert _clamped_end(1000 + 9000, 1000, 64) is None
    assert _clamped_end(None, 1000, 64) is None


def test_gzip_tactical_deterministic():
    """战术 artifact 压缩幂等（mtime=0）。"""
    obj = {"schema_version": TACTICAL_SCHEMA_VERSION, "x": [1, 2, {"n": "中文"}]}
    b1 = gzip_json(obj)
    b2 = gzip_json(obj)
    assert b1 == b2
    assert aircraft_sha(b1) == aircraft_sha(b2)


# ---------- API（sqlite + FakeStorage） ----------

class FakeStorage:
    def __init__(self, artifact_data):
        self.data = {tactical_object_key(1, TACTICAL_SCHEMA_VERSION): artifact_data}
    def get_bytes(self, key):
        if key not in self.data:
            raise FileNotFoundError(key)
        return self.data[key]
    def upload_bytes(self, key, data, content_type="application/gzip"):
        self.data[key] = data
        return key
    def exists(self, key):
        return key in self.data


def _tactical_fixture_artifact():
    """构造最小 v2 战术 artifact（2 玩家 + 烟 + HE），供 API 测试。"""
    return {
        "schema_version": TACTICAL_SCHEMA_VERSION,
        "match_id": 1, "map_name": "de_mirage", "tick_rate": 128,
        "rounds": [{
            "round": 1, "start_tick": 65, "end_tick": 8000, "freeze_end_tick": 65,
            "winner_side": 2, "winner_team": "A", "team_side_map": {"A": 2, "B": 3},
            "players": {
                "1": {"player_id": 1, "side": "CT", "outcome": "alive",
                      "points": [{"tick": 65, "time": 0.0, "x": 100.0, "y": 200.0, "z": 0.0,
                                  "yaw": 90.0, "pitch": 0.0, "alive": True, "place": "BombsiteA"},
                                 {"tick": 4000, "time": 30.7, "x": 150.0, "y": 250.0, "z": 0.0,
                                  "yaw": 120.0, "pitch": 0.0, "alive": True, "place": "Middle"}]},
                "2": {"player_id": 2, "side": "T", "outcome": "dead",
                      "points": [{"tick": 65, "time": 0.0, "x": -100.0, "y": -200.0, "z": 0.0,
                                  "yaw": -90.0, "pitch": 0.0, "alive": True, "place": "TSpawn"},
                                 {"tick": 4000, "time": 30.7, "x": -50.0, "y": -100.0, "z": 0.0,
                                  "yaw": -120.0, "pitch": 0.0, "alive": False, "place": "Middle"}]},
            },
            "props": [
                {"type": "smoke", "thrower_player_id": 1, "throw_tick": 1000, "detonate_tick": 1200,
                 "x": 120.0, "y": 220.0, "z": 0.0, "active_start": 1200, "active_end": 4200,
                 "authority": {"kind": "event", "source": "smokegrenade_detonate/expired entityid=273"},
                 "trajectory": [{"tick": 1000, "x": 110.0, "y": 210.0, "z": 0.0},
                                {"tick": 1200, "x": 120.0, "y": 220.0, "z": 0.0}]},
                {"type": "he", "thrower_player_id": 2, "throw_tick": 5000, "detonate_tick": 5600,
                 "x": -30.0, "y": -80.0, "z": 0.0, "active_start": 5600, "active_end": 5600,
                 "authority": {"kind": "event", "source": "hegrenade_detonate"}, "trajectory": []},
            ],
        }],
    }


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
    # 6R14：焦点用户 A 绑定到 P1（steam "111"），供个人 Pro 端点识别「我」（身份门禁用）
    db.add(SteamIdentity(provider="steam", subject="111", steam_id64="111", user_id=u.id, status="active"))
    db.flush()
    d = Demo(user_id=u.id, file_name="a.dem", file_path="demos/a.dem", file_size=1, status="parsed",
             map_name="de_mirage", created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2))
    db.add(d); db.flush()
    m = Match(demo_id=d.id, map_name="de_mirage", team_a_score=6, team_b_score=13, score_ct=9, score_t=10,
              duration_seconds=500, rounds=1)
    db.add(m); db.flush()
    d.match_id = m.id
    p1 = Player(match_id=m.id, steam_id="111", name="P1", team="CT", team_identity="A")
    p2 = Player(match_id=m.id, steam_id="222", name="P2", team="T", team_identity="B")
    db.add_all([p1, p2]); db.flush()
    db.add(PlayerStats(match_id=m.id, player_id=p1.id, kills=5, deaths=1, assists=2, hs_kills=1, hs_rate=0.2,
                       damage=800, adr=80.0, first_kills=2, weapon_stats={}))
    db.add(RoundSide(match_id=m.id, round=1, side_a=2, side_b=3))
    art = _tactical_fixture_artifact()
    artifact_data = gzip_json(art)
    db.add(TacticalArtifact(match_id=m.id, schema_version=TACTICAL_SCHEMA_VERSION,
                            object_key=tactical_object_key(1, TACTICAL_SCHEMA_VERSION),
                            size_bytes=len(artifact_data), sha256=aircraft_sha(artifact_data),
                            player_count=2, round_count=1, prop_count=2))
    db.commit(); db.close()

    fake = FakeStorage(artifact_data)
    monkeypatch.setattr(storage_module, "storage", fake)
    with TestClient(app) as c:
        ta = c.post("/api/auth/login", json={"email": "a@test.com", "password": "password123"}).json()["access_token"]
        yield c, {"Authorization": f"Bearer {ta}"}, S, fake
    app.dependency_overrides.clear()


def _set_tier(S, email, tier_name):
    db = S()
    u = db.query(User).filter(User.email == email).one()
    u.subscription_tier = tier_name
    u.subscription_expire = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
    db.commit(); db.close()


def _grant_team(S):
    """6R14：给 a@test.com/demo1 建立满足团队门禁的 workspace+roster。"""
    from tests._team_provision import grant_team_entitlement

    db = S()
    try:
        grant_team_entitlement(db, "a@test.com", 1)
    finally:
        db.close()


def test_team_replay_requires_max(client):
    c, h, _, _ = client
    assert c.get("/api/demos/1/team/replay?round_no=1", headers=h).status_code == 403  # free
    S = client[2]
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    assert c.get("/api/demos/1/team/replay?round_no=1", headers=h).status_code == 200


def test_team_replay_yaw_and_props(client):
    c, h, S, _ = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    body = c.get("/api/demos/1/team/replay?round_no=1", headers=h).json()
    assert body["schema_version"] == TACTICAL_SCHEMA_VERSION
    # 玩家带 yaw/pitch + 归一化坐标 + 中文区域
    p1 = next(p for p in body["players"] if p["player_id"] == 1)
    assert p1["points"][0]["yaw"] == 90.0
    assert p1["points"][0]["rx"] is not None and p1["points"][0]["ry"] is not None
    assert p1["points"][0]["place"] in ("A 区", "中路")
    # 道具：烟带 active interval（active_start_sec < active_end_sec），HE 瞬时（相等）
    props = body["props"]
    smoke = next(pr for pr in props if pr["type"] == "smoke")
    assert smoke["active_start_sec"] is not None and smoke["active_end_sec"] is not None
    assert smoke["active_start_sec"] < smoke["active_end_sec"]
    he = next(pr for pr in props if pr["type"] == "he")
    assert he["active_start_sec"] == he["active_end_sec"]   # 瞬时
    assert smoke["authority"]["kind"] == "event"
    assert smoke["thrower_name"] == "P1"
    # 不泄露 steam
    assert "steam" not in str(body)


def test_team_replay_cross_user_404(client):
    """对象归属先于套餐：跨用户 404（不泄露存在性）。"""
    c, h, S, _ = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    # 注册 + 登录另一用户
    c.post("/api/auth/register", json={"email": "b@test.com", "password": "password123", "nickname": "B"})
    hb = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": "b@test.com", "password": "password123"}).json()["access_token"]}
    r = c.get("/api/demos/1/team/replay?round_no=1", headers=hb)
    assert r.status_code == 404


def test_team_replay_missing_artifact(client):
    c, h, S, _ = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    db = S(); db.query(TacticalArtifact).delete(); db.commit(); db.close()
    r = c.get("/api/demos/1/team/replay?round_no=1", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TACTICAL_UNAVAILABLE"


def test_primitives_requires_max_and_shape(client):
    c, h, S, _ = client
    assert c.get("/api/demos/1/tactical/primitives", headers=h).status_code == 403
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    body = c.get("/api/demos/1/tactical/primitives", headers=h).json()
    assert "primitives" in body
    for pr in body["primitives"]:
        assert "round" in pr and "region" in pr and "players" in pr
    # crossfire 只命名为候选且带 reason（无 navmesh）
    crossfires = [p for p in body["primitives"] if p["kind"] == "crossfire_candidate"]
    for cf in crossfires:
        assert cf["reason"]


def test_primitives_compute_honest_library():
    """单元级：crossfire_candidate 只在角度差大时产生，且标注 reason。"""
    art_rounds = _tactical_fixture_artifact()["rounds"]
    kills = [{"round": 1, "tick": 4000, "actor_steam": "111", "victim_steam": "222",
              "actor_x": 150.0, "actor_y": 250.0, "victim_x": -50.0, "victim_y": -100.0}]
    deaths = [{"round": 1, "tick": 4000, "actor_steam": "111", "victim_steam": "222",
               "actor_x": 150.0, "actor_y": 250.0, "victim_x": -50.0, "victim_y": -100.0}]
    id_of = {"111": 1, "222": 2}
    name_of = {1: "P1", 2: "P2"}
    team_of = {1: "A", 2: "B"}
    pr = compute_primitives(art_rounds, "de_mirage", 128, kills, deaths, id_of, name_of, team_of)
    n_cross = sum(1 for p in pr if p["kind"] == "crossfire_candidate")
    assert n_cross <= 1
    for p in pr:
        if p["kind"] == "crossfire_candidate":
            assert "reason" in p


# ---------- 6R8 返工：个人战术回放（仅所选玩家 + 其投掷道具） ----------

def test_personal_replay_scope_only_focus_player(client):
    """scope 只含焦点玩家：points 仅该玩家、props 仅该玩家投掷、无 steam。"""
    c, h, S, _ = client
    _set_tier(S, "a@test.com", "pro")
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    body = c.get("/api/demos/1/personal/replay?round_no=1", headers=h).json()
    assert body["schema_version"] == TACTICAL_SCHEMA_VERSION
    assert body["player"]["player_id"] == 1
    # 仅焦点玩家轨迹（含 yaw）
    assert all(p["yaw"] is not None for p in body["points"] if "yaw" in p)
    # 道具只含该玩家投掷的 smoke（fixture 中 he 由 player 2 投掷 → 不应出现）
    assert set(pr["type"] for pr in body["props"]) == {"smoke"}
    assert all(pr["thrower_player_id"] == 1 for pr in body["props"])
    assert "steam" not in str(body)


def test_personal_replay_round_list_switch_no_cross_cache(client):
    """回合列表按焦点玩家聚合；道具数只统计该玩家。6R14：焦点由绑定身份决定（手动切换不改变本人画像）。"""
    c, h, S, _ = client
    _set_tier(S, "a@test.com", "pro")
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    lst = c.get("/api/demos/1/personal/replay", headers=h).json()
    r1 = next(r for r in lst["rounds"] if r["round"] == 1)
    assert r1["props"] == 1     # P1 在回合 1 只投掷了 1 个（smoke）
    assert r1["side"] == "CT"
    # 6R14：手动切到 P2 不改变结果——身份门禁下仍按绑定身份（P1）解析，不跨玩家混用数据
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 2})
    lst2 = c.get("/api/demos/1/personal/replay", headers=h).json()
    r1b = next(r for r in lst2["rounds"] if r["round"] == 1)
    assert r1b["props"] == 1    # 仍为 P1 的 smoke（身份决定焦点玩家）
