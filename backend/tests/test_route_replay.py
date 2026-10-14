"""6R4 测试：个人动态路线回放 API 契约 + 解析幂等 artifact。

覆盖（按 6R4 验收）：
- tick 严格有序、玩家身份（focus player）
- 上下半场真实阵营（CT/T，换边正确）
- 死亡 X（outcome=dead + death 事件）与存活结束（outcome=alive + round_end）
- 采样仍保留事件（spawn/kill/death/round_end 标记）
- 幂等重跑（process_demo 再跑 → already_parsed 且 artifact 不变；builder 确定性）
- free 403、跨用户 404、未选 focus 409、旧 demo（无 manifest）稳定错误码、部分/不完整数据
"""
import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Demo, DemoFocusPlayer, Match, Player, PlayerStats, RoundSide, RouteArtifact, SteamIdentity, User
from app.services.routes import ROUTE_SCHEMA_VERSION, artifact_sha256, build_trajectory, gzip_json, gunzip_json
from app.services.security import hash_password


def _artifact() -> dict:
    """构造最小 route artifact（2 回合：R1 p1 CT 存活，R2 换边 p1 T 死亡）。"""
    return {
        "schema_version": ROUTE_SCHEMA_VERSION,
        "match_id": 1,
        "map_name": "de_mirage",
        "tick_rate": 128,
        "rounds": [
            {
                "round": 1,
                "start_tick": 65,
                "end_tick": 8000,
                "winner_side": 2,
                "winner_team": "A",
                "side_map": {"A": 2, "B": 3},
                "trajectories": {
                    "1": {
                        "side": "CT",
                        "outcome": "alive",
                        "points": [
                            {"tick": 65, "time": 0.0, "x": 100.0, "y": 200.0, "z": 0.0,
                             "rx": 0.10, "ry": 0.20, "side": "CT", "alive": True, "events": ["spawn"], "event": "spawn", "source": "player_position"},
                            {"tick": 1000, "time": 7.3, "x": 150.0, "y": 260.0, "z": 0.0,
                             "rx": 0.12, "ry": 0.24, "side": "CT", "alive": True, "events": [], "event": None, "source": "player_position"},
                            {"tick": 5000, "time": 38.6, "x": 300.0, "y": 400.0, "z": 0.0,
                             "rx": 0.20, "ry": 0.34, "side": "CT", "alive": True, "events": ["kill"], "event": "kill", "source": "event"},
                            {"tick": 8000, "time": 62.0, "x": 320.0, "y": 410.0, "z": 0.0,
                             "rx": 0.21, "ry": 0.35, "side": "CT", "alive": True, "events": ["round_end"], "event": "round_end", "source": "round_end"},
                        ],
                        "integrity": {"ordered": True, "dropped_points": 1, "sampled_points": 5, "kept_points": 4,
                                      "coverage": 0.99, "first_tick": 65, "last_tick": 8000,
                                      "sampling": {"tick_step": 16, "mode": "time+distance+event"}, "warnings": []},
                    }
                },
            },
            {
                "round": 2,
                "start_tick": 9000,
                "end_tick": 16000,
                "winner_side": 3,
                "winner_team": "B",
                "side_map": {"A": 3, "B": 2},
                "trajectories": {
                    "1": {
                        "side": "T",
                        "outcome": "dead",
                        "points": [
                            {"tick": 9000, "time": 0.0, "x": 120.0, "y": 210.0, "z": 0.0,
                             "rx": 0.11, "ry": 0.21, "side": "T", "alive": True, "events": ["spawn"], "event": "spawn", "source": "player_position"},
                            {"tick": 12000, "time": 23.4, "x": 200.0, "y": 300.0, "z": 0.0,
                             "rx": 0.16, "ry": 0.28, "side": "T", "alive": True, "events": [], "event": None, "source": "player_position"},
                            {"tick": 15000, "time": 46.9, "x": 400.0, "y": 500.0, "z": 0.0,
                             "rx": 0.26, "ry": 0.42, "side": "T", "alive": False, "events": ["kill", "death"], "event": "death", "source": "event"},
                        ],
                        "integrity": {"ordered": True, "dropped_points": 0, "sampled_points": 3, "kept_points": 3,
                                      "coverage": 0.55, "first_tick": 9000, "last_tick": 15000,
                                      "sampling": {"tick_step": 16, "mode": "time+distance+event"},
                                      "warnings": ["该回合玩家中途死亡，轨迹在死亡时刻终止"]},
                    }
                },
            },
            {
                "round": 3,
                "start_tick": 18000,
                "end_tick": 24000,
                "winner_side": 2,
                "winner_team": "A",
                "side_map": {"A": 2, "B": 3},
                "trajectories": {
                    "1": {
                        "side": "CT",
                        "outcome": "dead",
                        "points": [
                            {"tick": 18000, "time": 0.0, "x": 90.0, "y": 180.0, "z": 0.0,
                             "rx": 0.09, "ry": 0.18, "side": "CT", "alive": True, "events": ["spawn"], "event": "spawn", "source": "player_position"},
                            {"tick": 21000, "time": 23.4, "x": 250.0, "y": 340.0, "z": 0.0,
                             "rx": 0.20, "ry": 0.30, "side": "CT", "alive": False, "events": ["death"], "event": "death", "source": "event"},
                        ],
                        "integrity": {"ordered": True, "dropped_points": 0, "sampled_points": 2, "kept_points": 2,
                                      "coverage": 0.5, "first_tick": 18000, "last_tick": 21000,
                                      "sampling": {"tick_step": 16, "mode": "time+distance+event"},
                                      "warnings": ["该回合玩家中途死亡，轨迹在死亡时刻终止"]},
                    }
                },
            },
        ],
    }


def _artifact_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _gzip_artifact() -> bytes:
    # mtime=0：确定性压缩（跨秒相同 sha），与 routes.gzip_json 一致
    return gzip.compress(json.dumps(_artifact(), ensure_ascii=False).encode("utf-8"), mtime=0)


class FakeStorage:
    def __init__(self):
        self.data = {f"routes/1/{ROUTE_SCHEMA_VERSION}/routes.json.gz": _gzip_artifact()}
        self.uploads = {}

    def get_bytes(self, key):
        if key not in self.data:
            raise FileNotFoundError(key)
        return self.data[key]

    def upload_bytes(self, key, data, content_type="image/png"):
        self.data[key] = data
        self.uploads[key] = data
        return key

    def exists(self, key):
        return key in self.data


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
              duration_seconds=500, rounds=3)
    db.add(m)
    db.flush()
    d.match_id = m.id
    p1 = Player(match_id=m.id, steam_id="111", name="P1", team="CT", team_identity="A")
    p2 = Player(match_id=m.id, steam_id="222", name="P2", team="T", team_identity="B")
    db.add_all([p1, p2])
    db.flush()
    db.add(PlayerStats(match_id=m.id, player_id=p1.id, kills=5, deaths=1, assists=2, hs_kills=1, hs_rate=0.2,
                       damage=800, adr=80.0, first_kills=2, weapon_stats={}))
    db.add(RoundSide(match_id=m.id, round=1, side_a=2, side_b=3))
    db.add(RoundSide(match_id=m.id, round=2, side_a=3, side_b=2))   # 换边：A 队到 T
    db.add(RoundSide(match_id=m.id, round=3, side_a=2, side_b=3))   # 回到 CT
    # manifest（默认存在；缺失/旧 demo 用删除法测）。sha256/size 与存储字节一致（API 校验用）
    fake = FakeStorage()
    artifact_key = f"routes/1/{ROUTE_SCHEMA_VERSION}/routes.json.gz"
    stored = fake.get_bytes(artifact_key)
    db.add(RouteArtifact(match_id=m.id, schema_version=ROUTE_SCHEMA_VERSION,
                         object_key=artifact_key,
                         size_bytes=len(stored), sha256=_artifact_sha256(stored), player_count=2, round_count=3))
    db.commit()
    db.close()

    monkeypatch.setattr(storage_module, "storage", fake)

    with TestClient(app) as c:
        ta = c.post("/api/auth/login", json={"email": "a@test.com", "password": "password123"}).json()["access_token"]
        tb = c.post("/api/auth/login", json={"email": "b@test.com", "password": "password123"}).json()["access_token"]
        yield c, {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}, S, fake
    app.dependency_overrides.clear()


def _set_tier(S, email, tier_name, expire_days):
    db = S()
    u = db.query(User).filter(User.email == email).one()
    u.subscription_tier = tier_name
    u.subscription_expire = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expire_days)
                             if expire_days is not None else None)
    db.commit()
    db.close()


def _set_pro(S):
    _set_tier(S, "a@test.com", "pro", 30)


def _unbind_steam(S, email):
    """移除该用户的 active Steam 绑定（6R14 门禁测试：未绑定 → 403 STEAM_IDENTITY_REQUIRED）。"""
    db = S()
    u = db.query(User).filter(User.email == email).one()
    db.query(SteamIdentity).filter(SteamIdentity.user_id == u.id).delete()
    db.commit()
    db.close()


# ---------- 鉴权 / 对象隔离 ----------

def test_route_requires_pro(client):
    c, h, _, _, _ = client
    assert c.get("/api/demos/1/personal/route?round_no=1", headers=h).status_code == 403


def test_route_requires_focus(client):
    c, h, _, S, _ = client
    _set_pro(S)
    # 6R14：手动 focus 已被身份门禁替代；未绑定 Steam → 403 STEAM_IDENTITY_REQUIRED
    _unbind_steam(S, "a@test.com")
    r = c.get("/api/demos/1/personal/route?round_no=1", headers=h)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "STEAM_IDENTITY_REQUIRED"


def test_route_cross_user_404(client):
    c, h, hb, _, _ = client
    assert c.get("/api/demos/1/personal/route?round_no=1", headers=hb).status_code == 404


# ---------- 有效路线：有序 / 身份 / 阵营 / 事件 / 终局 ----------

def test_route_round1_alive_and_ordered(client):
    c, h, _, S, _ = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    body = c.get("/api/demos/1/personal/route?round_no=1", headers=h).json()

    assert body["player"]["player_id"] == 1 and body["player"]["name"] == "P1"
    assert body["round"]["number"] == 1 and body["round"]["side"] == "CT"
    assert body["outcome"] == "alive"

    ticks = [p["tick"] for p in body["points"]]
    assert ticks == sorted(ticks)                      # tick 严格有序
    assert body["integrity"]["ordered"] is True

    events = {p["event"] for p in body["points"]}
    assert "spawn" in events and "round_end" in events  # 采样保留事件
    assert body["events"]["spawn"]["tick"] == 65
    assert body["events"]["round_end"]["tick"] == 8000
    assert body["events"]["death"] is None              # 存活无死亡

    assert body["map"]["name"] == "de_mirage"
    assert body["map"]["image_size"] == 1024


def test_route_round2_dead_and_side_swap(client):
    c, h, _, S, _ = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    body = c.get("/api/demos/1/personal/route?round_no=2", headers=h).json()

    assert body["round"]["side"] == "T"                 # 换边后 focus 在 T
    assert body["outcome"] == "dead"
    assert body["events"]["death"] is not None
    assert body["events"]["death"]["tick"] == 15000
    assert all(p["side"] == "T" for p in body["points"])
    assert body["integrity"]["warnings"]               # 部分/不完整数据有警告

    # 同 tick 多事件：kill+death 同时保留（不被相互覆盖），死亡 X 位置可见
    same = next(p for p in body["points"] if p["tick"] == 15000)
    assert "kill" in same["events"] and "death" in same["events"]
    assert any(k["tick"] == 15000 for k in body["events"]["kills"])
    assert body["events"]["kills"] and body["events"]["kills"][-1]["tick"] == 15000  # kill 同 tick 也聚合


def test_route_side_filter_lists_rounds(client):
    c, h, _, S, _ = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    ct = c.get("/api/demos/1/personal/route?side=CT", headers=h).json()
    assert [r["round"] for r in ct["rounds"]] == [1, 3]
    t = c.get("/api/demos/1/personal/route?side=T", headers=h).json()
    assert [r["round"] for r in t["rounds"]] == [2]
    assert t["rounds"][0]["outcome"] == "dead"


# ---------- 6R5B：半场总览（view=half） ----------

def test_route_half_requires_ct_or_t(client):
    c, h, _, S, _ = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    assert c.get("/api/demos/1/personal/route?view=half&side=all", headers=h).status_code == 422
    assert c.get("/api/demos/1/personal/route?view=half", headers=h).status_code == 422


def test_route_half_side_filter_and_ascending(client):
    """CT/T 换边筛选：一次请求聚合该阵营全部回合，升序。"""
    c, h, _, S, _ = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    ct = c.get("/api/demos/1/personal/route?view=half&side=CT", headers=h).json()
    assert [r["round"] for r in ct["rounds"]] == [1, 3]
    assert all(r["side"] == "CT" for r in ct["rounds"])
    t = c.get("/api/demos/1/personal/route?view=half&side=T", headers=h).json()
    assert [r["round"] for r in t["rounds"]] == [2]
    assert all(r["side"] == "T" for r in t["rounds"])
    assert ct["side"] == "CT" and ct["side_filter"] == "CT"


def test_route_half_summary_and_outcome(client):
    """摘要（回合/死亡/存活/存活率）与各回合 outcome 正确；含 maps/rx-ry 归一化。"""
    c, h, _, S, _ = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    ct = c.get("/api/demos/1/personal/route?view=half&side=CT", headers=h).json()
    assert ct["summary"] == {"rounds": 2, "alive": 1, "dead": 1, "survival_rate": 0.5, "kills": 1}
    by_round = {r["round"]: r for r in ct["rounds"]}
    assert by_round[1]["outcome"] == "alive"
    assert by_round[3]["outcome"] == "dead"
    assert by_round[1]["events"]["round_end"] is not None
    assert by_round[3]["events"]["death"] is not None
    assert ct["map"]["name"] == "de_mirage" and ct["map"]["image_size"] == 1024
    assert ct["max_duration"] > 0
    # 一次请求聚合：CT 半场含 2 回合
    assert len(ct["rounds"]) == 2


def test_route_half_one_request_no_n1(client):
    """半场总览为单次请求：response 一次性包含该阵营全部回合（不逐回合拉取）。"""
    c, h, _, S, _ = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    r = c.get("/api/demos/1/personal/route?view=half&side=CT", headers=h)
    assert r.status_code == 200
    assert r.json()["rounds"]  # 一次响应含全部 CT 回合
    assert len(r.json()["rounds"]) == 2


def test_route_half_free_403(client):
    c, h, _, _, _ = client
    assert c.get("/api/demos/1/personal/route?view=half&side=CT", headers=h).status_code == 403


def test_route_half_cross_user_404(client):
    c, h, hb, _, _ = client
    assert c.get("/api/demos/1/personal/route?view=half&side=CT", headers=hb).status_code == 404


def test_route_half_missing_artifact_old_demo(client):
    c, h, _, S, fake = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    db = S()
    db.query(RouteArtifact).delete()
    db.commit()
    db.close()
    r = c.get("/api/demos/1/personal/route?view=half&side=CT", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ROUTE_UNAVAILABLE"


def test_route_half_corrupt_artifact_checksum(client):
    c, h, _, S, fake = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    key = f"routes/1/{ROUTE_SCHEMA_VERSION}/routes.json.gz"
    stored = fake.get_bytes(key)
    fake.data[key] = stored + b"\x00"   # 篡改 size → 校验失败
    r = c.get("/api/demos/1/personal/route?view=half&side=CT", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ROUTE_UNAVAILABLE"


# ---------- 缺失 / 旧 demo ----------

def test_route_missing_artifact_old_demo(client):
    c, h, _, S, fake = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    # 删除 manifest 模拟旧 demo（解析于 6R4 之前，无路线 artifact）
    db = S()
    db.query(RouteArtifact).delete()
    db.commit()
    db.close()
    r = c.get("/api/demos/1/personal/route?round_no=1", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ROUTE_UNAVAILABLE"


def test_route_incomplete_trajectory_has_warning(client):
    c, h, _, S, _ = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    body = c.get("/api/demos/1/personal/route?round_no=2", headers=h).json()
    assert body["integrity"]["warnings"]
    assert body["integrity"]["coverage"] < 1.0


# ---------- 6R4.1：gzip 可复现 / artifact 校验 / 同 tick 多事件 ----------

def test_gzip_json_deterministic_across_seconds():
    """gzip_json 固定 mtime=0：跨秒重复压缩产生相同字节（幂等/可复现 sha）。"""
    import time

    obj = {"a": 1, "nested": {"x": [1, 2, 3]}, "msg": "中文"}
    b1 = gzip_json(obj)
    time.sleep(1.1)
    b2 = gzip_json(obj)
    assert b1 == b2
    assert artifact_sha256(b1) == artifact_sha256(b2)
    # 解压后内容一致
    assert gunzip_json(b1) == obj


def test_route_api_validates_artifact_checksum(client):
    """MinIO 数据被篡改/损坏（size 或 sha 不符）→ 稳定 ROUTE_UNAVAILABLE，不返回错数据。"""
    c, h, _, S, fake = client
    _set_pro(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    key = f"routes/1/{ROUTE_SCHEMA_VERSION}/routes.json.gz"
    stored = fake.get_bytes(key)
    # 篡改一个字节（长度不变 → sha 不一致）
    fake.data[key] = stored[:-1] + bytes([stored[-1] ^ 0xFF])
    r = c.get("/api/demos/1/personal/route?round_no=1", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ROUTE_UNAVAILABLE"


def test_same_tick_kill_death_preserved():
    """同一玩家同 tick 的 kill+death 同时保留：events 数组含两者，主标记 death，alive=False。"""
    tr = build_trajectory(
        start_tick=9000, end_tick=16000, side="T",
        sampled=[
            {"tick": 9000, "x": 100.0, "y": 100.0, "z": 0.0, "alive": True},
            {"tick": 12000, "x": 200.0, "y": 200.0, "z": 0.0, "alive": True},
            {"tick": 15000, "x": 400.0, "y": 500.0, "z": 0.0, "alive": True},
        ],
        kills=[{"tick": 15000, "x": 400.0, "y": 500.0}],
        death={"tick": 15000, "x": 405.0, "y": 505.0},
        tick_rate=128,
    )
    assert tr["outcome"] == "dead"
    pt = next(p for p in tr["points"] if p["tick"] == 15000)
    assert set(pt["events"]) == {"kill", "death"}
    assert pt["event"] == "death"          # 兼容主标记：最高优先级事件
    assert pt["alive"] is False            # 死亡视为 not alive
    # 死亡位置（victim）优先，死亡 X 画在该处
    assert pt["x"] == 405.0 and pt["y"] == 505.0


# ---------- 6R6A：团队十人单回合同步路线（Max） ----------

def _set_max(S):
    _set_tier(S, "a@test.com", "max", 30)


def _grant_team(S):
    """6R14：给 a@test.com/demo1 建立满足团队门禁的 workspace+roster。"""
    from tests._team_provision import grant_team_entitlement

    db = S()
    try:
        grant_team_entitlement(db, "a@test.com", 1)
    finally:
        db.close()


def test_team_route_requires_max(client):
    c, h, _, S, _ = client
    assert c.get("/api/demos/1/team/route?round_no=1", headers=h).status_code == 403   # free
    _set_pro(S)
    assert c.get("/api/demos/1/team/route?round_no=1", headers=h).status_code == 403   # Pro 越权（Max-only）
    _set_max(S)
    _grant_team(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    assert c.get("/api/demos/1/team/route?round_no=1", headers=h).status_code == 200   # Max


def test_team_route_cross_user_404(client):
    c, h, hb, _, _ = client
    assert c.get("/api/demos/1/team/route?round_no=1", headers=hb).status_code == 404


def test_team_route_round_validation(client):
    c, h, _, S, _ = client
    _set_max(S)
    _grant_team(S)
    assert c.get("/api/demos/1/team/route", headers=h).status_code == 422
    assert c.get("/api/demos/1/team/route?round_no=99", headers=h).status_code == 422


def test_team_route_side_auth_by_roundside_swap(client):
    """side 由 RoundSide + A/B 身份换算（换边正确），不用 players.team 最终阵营。"""
    c, h, _, S, _ = client
    _set_max(S)
    _grant_team(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    r1 = c.get("/api/demos/1/team/route?round_no=1", headers=h).json()
    sides1 = {p["player_id"]: (p["team_identity"], p["side"]) for p in r1["players"]}
    assert sides1[1] == ("A", "CT") and sides1[2] == ("B", "T")   # R1：A=CT，B=T
    r2 = c.get("/api/demos/1/team/route?round_no=2", headers=h).json()
    sides2 = {p["player_id"]: (p["team_identity"], p["side"]) for p in r2["players"]}
    assert sides2[1] == ("A", "T") and sides2[2] == ("B", "CT")   # R2 换边：A=T，B=CT
    # 稳定顺序：队伍身份 A 全部在前、B 全部在后（含 roster 补位玩家）
    idents = [p["team_identity"] for p in r1["players"]]
    seen_b = False
    for ident in idents:
        if ident == "B":
            seen_b = True
        elif ident == "A":
            assert not seen_b, "A 出现在 B 之后"
    assert "A" in idents and "B" in idents
    # 不返回 steam_id
    assert "steam_id" not in str(r1) and "steam" not in str(r1)


def test_team_route_no_steam_and_death_truncation(client):
    c, h, _, S, _ = client
    _set_max(S)
    _grant_team(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    r2 = c.get("/api/demos/1/team/route?round_no=2", headers=h).json()
    p1 = next(p for p in r2["players"] if p["player_id"] == 1)
    assert p1["outcome"] == "dead"
    assert p1["points"][-1]["event"] == "death"    # 死亡截断：最后点是 death
    assert p1["events"]["death"] is not None
    assert all("steam" not in str(p) for p in r2["players"])


def test_team_route_missing_trajectory_warning(client):
    c, h, _, S, _ = client
    _set_max(S)
    _grant_team(S)
    c.put("/api/demos/1/focus-player", headers=h, json={"player_id": 1})
    r1 = c.get("/api/demos/1/team/route?round_no=1", headers=h).json()
    p2 = next(p for p in r1["players"] if p["player_id"] == 2)   # artifact 无该玩家轨迹
    assert p2["outcome"] == "unknown"
    assert p2["points"] == []
    assert p2["integrity"]["warnings"]   # 保留警告，不伪造路线


def test_team_route_missing_artifact(client):
    c, h, _, S, _ = client
    _set_max(S)
    _grant_team(S)
    db = S(); db.query(RouteArtifact).delete(); db.commit(); db.close()
    r = c.get("/api/demos/1/team/route?round_no=1", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ROUTE_UNAVAILABLE"


def test_team_route_corrupt_artifact(client):
    c, h, _, S, fake = client
    _set_max(S)
    _grant_team(S)
    key = f"routes/1/{ROUTE_SCHEMA_VERSION}/routes.json.gz"
    stored = fake.get_bytes(key)
    fake.data[key] = stored + b"\x00"
    r = c.get("/api/demos/1/team/route?round_no=1", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ROUTE_UNAVAILABLE"


# ---------- 解析幂等 artifact（真实 demo 跑 process_demo） ----------

def test_process_demo_generates_idempotent_route_artifact(monkeypatch):
    from pathlib import Path

    from app.services import storage as storage_module
    from app.tasks import parse_tasks as tasks_mod

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()
    u = User(email="x@y.com", password_hash=hash_password("password123"), nickname="x")
    db.add(u)
    db.flush()
    d = Demo(user_id=u.id, file_name="a.dem", file_path="demos/a.dem", file_size=1, status="uploaded", map_name="de_mirage")
    db.add(d)
    db.commit()
    demo_id = d.id
    db.close()

    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "storage", fake)
    monkeypatch.setattr(tasks_mod, "storage", fake)
    monkeypatch.setattr(tasks_mod, "SessionLocal", S)

    demo_real = Path(__file__).resolve().parent / "demos" / "test_demo.dem"
    assert demo_real.exists()
    db = S()
    d2 = db.get(Demo, demo_id)
    d2.file_path = str(demo_real)
    d2.status = "uploaded"
    db.commit()
    db.close()

    from app.tasks.parse_tasks import process_demo

    r1 = process_demo(demo_id)
    assert "match_id" in r1

    db = S()
    d3 = db.get(Demo, demo_id)
    assert d3.status == "parsed"
    manifest = db.query(RouteArtifact).filter(RouteArtifact.match_id == d3.match_id).one()
    assert manifest.schema_version == ROUTE_SCHEMA_VERSION
    assert manifest.object_key.startswith("routes/")
    assert manifest.sha256
    key = manifest.object_key
    stored = fake.get_bytes(key)
    payload = json.loads(gzip.decompress(stored).decode("utf-8"))
    assert payload["schema_version"] == ROUTE_SCHEMA_VERSION
    assert payload["rounds"] and payload["rounds"][0]["trajectories"]
    sha_first = manifest.sha256
    db.close()

    # 幂等重跑：再执行一次，任务已解析 → already_parsed，artifact 不变
    r2 = process_demo(demo_id)
    assert r2["status"] == "already_parsed"
    db = S()
    m2 = db.query(RouteArtifact).filter(RouteArtifact.match_id == d3.match_id).one()
    assert m2.sha256 == sha_first
    db.close()
