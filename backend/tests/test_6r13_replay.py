"""6R13 测试：击杀播报 / 投掷归属 / 真实 player_blind / HE 伤害（权威与诚实降级）。

- build_killfeed：每条 player_death → 分类/武器/爆头/穿烟/穿墙/助攻，按 (tick, seq) 稳定排序。
- build_blinds：player_blind + flashbang_detonate 关联 → 真实致盲（自闪/队闪/敌闪），盲时长来自
  blind_duration（秒）派生结束时刻（derived, 非事件字段）。
- build_he_damage：player_hurt(weapon=hegrenade) 关联 hegrenade_detonate → HE 真实受伤。
- _build_props：投掷者蒸汽无法映射到 roster → 不丢弃，thrower_player_id=None + authority=unknown_thrower。
- API：team/replay 与 personal/replay 输出 killfeed/blinds/he_damage + thrower_name/thrower_team；
  personal 只含焦点玩家相关击杀；不泄露 steam。
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, DemoFocusPlayer, Match, Player, PlayerStats, RoundSide, SteamIdentity, TacticalArtifact, User
from app.services.routes import gzip_json
from app.services.security import hash_password
from app.services.tactical import (
    TACTICAL_SCHEMA_VERSION, artifact_sha256 as aircraft_sha,
    build_blinds, build_he_damage, build_killfeed, tactical_object_key,
)


# ---------- 纯构建函数（输入为真实事件行） ----------

def _rounds():
    return [{"round": 1, "start_tick": 65, "end_tick": 8000, "freeze_end_tick": 65}]


def test_build_killfeed_classify_and_fields():
    deaths = [
        {"tick": 1000, "attacker_steamid": "A1", "user_steamid": "B1", "weapon": "ak47",
         "headshot": True, "thrusmoke": False, "penetrated": False,
         "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0},
        {"tick": 1010, "attacker_steamid": "A1", "user_steamid": "B2", "weapon": "ak47",
         "headshot": False, "thrusmoke": True, "penetrated": False,
         "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0},
        {"tick": 1020, "attacker_steamid": "A1", "user_steamid": "B3", "weapon": "ak47",
         "headshot": False, "thrusmoke": False, "penetrated": True,
         "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0},
        {"tick": 1030, "attacker_steamid": "A1", "user_steamid": "B4", "weapon": "hegrenade",
         "headshot": False, "thrusmoke": False, "penetrated": False,
         "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0},
        {"tick": 1040, "attacker_steamid": "B1", "user_steamid": "B1", "weapon": "ak47",
         "headshot": False, "thrusmoke": False, "penetrated": False,
         "user_X": 1.0, "user_Y": 1.0, "attacker_X": 1.0, "attacker_Y": 1.0},
        {"tick": 1050, "attacker_steamid": "A1", "user_steamid": "B5", "weapon": "ak47",
         "headshot": False, "thrusmoke": False, "penetrated": False,
         "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0,
         "assister_steamid": "A2"},
        # 回合外 tick → 不入任何回合
        {"tick": 999999, "attacker_steamid": "A1", "user_steamid": "B6", "weapon": "ak47",
         "headshot": False, "thrusmoke": False, "penetrated": False,
         "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0},
    ]
    pid = {"A1": 1, "A2": 7, "B1": 2, "B2": 3, "B3": 4, "B4": 5, "B5": 6}
    out = build_killfeed(deaths, pid, _rounds())
    kf = out[1]
    # 回合外 tick 被剔除，且按 (tick, seq) 稳定排序
    assert len(kf) == 6
    assert [e["tick"] for e in kf] == [1000, 1010, 1020, 1030, 1040, 1050]

    def find(tick):
        return next(e for e in kf if e["tick"] == tick)

    assert find(1000)["class"] == "direct" and find(1000)["headshot"] is True
    assert find(1000)["weapon"] == "ak47"
    assert find(1010)["class"] == "thrusmoke"
    assert find(1020)["class"] == "wallbang"
    assert find(1030)["class"] == "grenade"
    assert find(1040)["class"] == "suicide" and find(1040)["self_kill"] is True
    assert find(1050)["assist_player_id"] == 7


def test_build_blinds_self_team_enemy_and_end_tick():
    # flash detonate：entityid 281 (at tick 100), entityid 300 (at tick 2000)
    flash_rows = [
        {"entityid": 281, "tick": 100, "user_steamid": "A1"},
        {"entityid": 300, "tick": 2000, "user_steamid": "B1"},
    ]
    # A 队 = {A1,A2}, B 队 = {B1,B2}
    blind_rows = [
        {"entityid": 281, "tick": 100, "user_steamid": "A1", "attacker_steamid": "A1", "blind_duration": 0.5},  # 自闪
        {"entityid": 281, "tick": 100, "user_steamid": "A2", "attacker_steamid": "A1", "blind_duration": 1.2},  # 队闪
        {"entityid": 281, "tick": 100, "user_steamid": "B1", "attacker_steamid": "A1", "blind_duration": 2.0},  # 敌闪
        {"entityid": 300, "tick": 2000, "user_steamid": "B1", "attacker_steamid": "B1", "blind_duration": 0.9},  # 自闪
        # 无法关联到任何 flash detonate → 丢弃（不伪造）
        {"entityid": 999, "tick": 3000, "user_steamid": "A1", "attacker_steamid": "A1", "blind_duration": 1.0},
    ]
    pid = {"A1": 1, "A2": 7, "B1": 2, "B2": 8}
    identity = {"A1": "A", "A2": "A", "B1": "B", "B2": "B"}
    out = build_blinds(blind_rows, flash_rows, pid, identity, _rounds(), tick_rate=128)
    bl = out[1]
    assert len(bl) == 4
    by_victim = {(e["victim_player_id"], e["tick"]): e for e in bl}
    assert by_victim[(1, 100)]["flash_class"] == "self"
    assert by_victim[(7, 100)]["flash_class"] == "team"
    assert by_victim[(2, 100)]["flash_class"] == "enemy"
    # 结束时刻 = tick + blind_duration * tick_rate
    assert by_victim[(7, 100)]["blind_end_tick"] == 100 + round(1.2 * 128)
    assert by_victim[(7, 100)]["attacker_player_id"] == 1


def test_build_he_damage_links_detonate():
    he_rows = [
        {"entityid": 501, "tick": 1000, "user_steamid": "A1", "x": 10.0, "y": 20.0, "z": 0.0},
        # 第二个 HE（无匹配受伤亦可）
        {"entityid": 502, "tick": 3000, "user_steamid": "B1", "x": 0.0, "y": 0.0, "z": 0.0},
    ]
    hurt_rows = [
        {"tick": 1005, "attacker_steamid": "A1", "user_steamid": "B1", "weapon": "hegrenade", "dmg_health": 40},
        {"tick": 1010, "attacker_steamid": "A1", "user_steamid": "B2", "weapon": "hegrenade", "dmg_health": 30},
        # 超窗 → 不关联到 entityid 501
        {"tick": 2500, "attacker_steamid": "A1", "user_steamid": "B3", "weapon": "hegrenade", "dmg_health": 20},
    ]
    pid = {"A1": 1, "B1": 2, "B2": 8, "B3": 9}
    out = build_he_damage(hurt_rows, he_rows, pid, _rounds())
    hd = out[1]
    assert len(hd) == 2
    # 伤害关联到 he entityid 501（按攻击者+时间窗），并注明 derived authority
    for e in hd:
        assert e["authority"]["kind"] == "inferred"
        assert e["dmg_health"] in (40, 30)
        assert e["he_tick"] == 1000
    # 超窗的 2500 → 未关联，不出现在 hd（不伪造爆点半径外的伤害归属）


def test_build_props_unknown_thrower_not_dropped():
    from app.services.tactical import _build_props
    # 投掷者 steamid 不在 player_id_of → thrower_player_id=None 且保留（autority=unknown_thrower）
    smoke_start = [{"entityid": 273, "tick": 1000, "user_steamid": "ZZZ", "x": 1.0, "y": 2.0, "z": 3.0}]
    prop = _build_props(_rounds(), 1, {"A": 2, "B": 3}, {"A1": "A"}, {}, smoke_start, {273: [2000]}, [], {}, [], [], [],
                        65, 8000, 128)
    assert len(prop) == 1
    assert prop[0]["thrower_player_id"] is None
    assert prop[0]["authority"]["kind"] == "event"
    assert "smokegrenade_detonate" in prop[0]["authority"].get("detail", "")


# ---------- API（sqlite + FakeStorage，最小 v3 artifact 带新字段） ----------

def _v3_fixture_artifact():
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
                {"type": "he", "thrower_player_id": None, "throw_tick": 5000, "detonate_tick": 5600,
                 "x": -30.0, "y": -80.0, "z": 0.0, "active_start": 5600, "active_end": 5600,
                 "authority": {"kind": "event", "source": "hegrenade_detonate"}, "trajectory": []},
            ],
            "killfeed": [
                {"tick": 4000, "seq": 0, "class": "direct", "weapon": "ak47", "headshot": True,
                 "thrusmoke": False, "penetrated": False, "self_kill": False,
                 "attacker_player_id": 1, "victim_player_id": 2, "assist_player_id": None},
            ],
            "blinds": [
                {"tick": 1200, "flash_entity_id": 203, "blind_duration": 1.5, "blind_end_tick": 1200 + round(1.5 * 128),
                 "attacker_player_id": 2, "victim_player_id": 1, "flash_class": "enemy"},
            ],
            "he_damage": [
                {"tick": 5605, "he_tick": 5600, "dmg_health": 55,
                 "attacker_player_id": 2, "victim_player_id": 1,
                 "authority": {"kind": "inferred", "source": "thrower+weapon+time-window"}},
            ],
        }],
    }


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
    art = _v3_fixture_artifact()
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
        yield c, {"Authorization": f"Bearer {ta}"}, S
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


def test_team_replay_has_killfeed_blinds_he_and_thrower(client):
    c, h, S = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    body = c.get("/api/demos/1/team/replay?round_no=1", headers=h).json()
    assert body["schema_version"] == TACTICAL_SCHEMA_VERSION
    # killfeed
    assert len(body["killfeed"]) == 1
    kf = body["killfeed"][0]
    assert kf["class"] == "direct" and kf["weapon"] == "ak47" and kf["headshot"] is True
    assert kf["attacker_name"] == "P1" and kf["victim_name"] == "P2"
    assert kf["attacker_team"] == "A" and kf["victim_team"] == "B"
    assert kf["time_sec"] is not None
    # blinds（真实致盲）
    assert len(body["blinds"]) == 1
    bl = body["blinds"][0]
    assert bl["flash_class"] == "enemy"
    assert bl["victim_name"] == "P1" and bl["attacker_name"] == "P2"
    assert bl["start_sec"] is not None and bl["end_sec"] is not None
    assert bl["end_sec"] - bl["start_sec"] == 1.5
    # he_damage
    assert len(body["he_damage"]) == 1
    hd = body["he_damage"][0]
    assert hd["dmg_health"] == 55 and hd["victim_name"] == "P1"
    assert hd["authority"]["kind"] == "inferred"
    # thrower：he 无投掷者映射 → 归属未知，不丢
    he = next(pr for pr in body["props"] if pr["type"] == "he")
    assert he["thrower_name"] is None and he["thrower_team"] is None
    smoke = next(pr for pr in body["props"] if pr["type"] == "smoke")
    assert smoke["thrower_name"] == "P1" and smoke["thrower_team"] == "A"
    # 不泄露 steam / 裸 tick / 世界坐标
    assert "steam" not in str(body)


def test_personal_replay_killfeed_only_focus(client):
    c, h, S = client
    _set_tier(S, "a@test.com", "pro")
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    body = c.get("/api/demos/1/personal/replay?round_no=1", headers=h).json()
    # 焦点玩家 1 是 killfeed 的击杀者 → 出现
    assert len(body["killfeed"]) == 1
    assert body["killfeed"][0]["attacker_player_id"] == 1
    assert "steam" not in str(body)
