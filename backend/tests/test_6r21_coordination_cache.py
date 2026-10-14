"""6R21F-E6A-COORD-CACHE：/api/demos/{id}/coordination 后端确定性缓存（红测先行）。

覆盖：冷命中/热命中、版本失效、并发去重、异常恢复、响应等价、确定性 hash、用户/权限隔离。
- 服务级单测（COORD 缓存原语）：不碰 DB/HTTP，直接驱动 coordination_cache.*。
- API 集成单测：用与 test_6r16_area_api 一致的 client 夹具驱动真实 /coordination，
  以 spy 计数 build/asm 调用次数断言「冷一次、热不再算」。

注意：这些测试先写（red）——coordination_cache 模块尚不存在/端点尚未接入缓存，故导入即红。
"""
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import personal as personal_mod
from app.database import Base, get_db
from app.main import app
from app.models import Demo, DemoFocusPlayer, Event, Match, Player, RoundSide, TacticalArtifact, User
from app.services.routes import gzip_json
from app.services.security import hash_password
from app.services.tactical import TACTICAL_SCHEMA_VERSION, artifact_sha256, tactical_object_key


# ---------------------------------------------------------------------------
# 1) 服务级：coordination_cache 原语
# ---------------------------------------------------------------------------

def import_cache():
    # 目标模块尚不存在时让测试干净地红（导入错误），实现后转绿。
    from app.services import coordination_cache as cc
    return cc


def _key(cc, **over):
    base = dict(
        algo_schema="2.0", algo_thresholds="2.0.0",
        demo_id=19, match_id=59, map_name="de_mirage",
        artifact_sha="a" * 64, artifact_schema=TACTICAL_SCHEMA_VERSION,
        round_no=None, focus_player_id=None,
        identity_fp="idfp", team_names={"A": "CT", "B": "T"}, region_hash="rgh",
        events_fp="evf",
    )
    base.update(over)
    return cc.coordination_cache_key(**base)


def test_key_is_deterministic_for_same_inputs():
    cc = import_cache()
    assert _key(cc) == _key(cc), "同输入必须同 key"


@pytest.mark.parametrize("field,value", [
    ("algo_schema", "3.0"),
    ("algo_thresholds", "3.0.0"),
    ("artifact_sha", "b" * 64),
    ("artifact_schema", "9.9"),
    ("round_no", 3),
    ("focus_player_id", 531),
    ("identity_fp", "other-idfp"),
    ("region_hash", "other-rgh"),
    ("events_fp", "other-evf"),
])
def test_key_differs_when_covered_dimension_changes(field, value):
    """覆盖 demo/parser/schema/算法版本及相关输入：任一维度变化 → 新 key → 失效。"""
    cc = import_cache()
    base = dict(
        algo_schema="2.0", algo_thresholds="2.0.0",
        demo_id=19, match_id=59, map_name="de_mirage",
        artifact_sha="a" * 64, artifact_schema=TACTICAL_SCHEMA_VERSION,
        round_no=None, focus_player_id=None,
        identity_fp="idfp", team_names={"A": "CT", "B": "T"}, region_hash="rgh",
        events_fp="evf",
    )
    base[field] = value
    assert _key(cc, **base) != _key(cc), f"{field} 变化必须使 key 不同"


def test_cold_then_hot_computes_once():
    cc = import_cache()
    cc.coordination_cache_reset()
    calls = {"n": 0}

    def build_fn():
        calls["n"] += 1
        return b'{"content_hash":"c1"}'

    key = _key(cc)
    v1, s1 = cc.coordination_cache_get_or_build(key, build_fn)
    assert calls["n"] == 1, "冷命中应计算一次"
    assert v1 == b'{"content_hash":"c1"}'
    assert s1 in ("build", "cold")

    v2, s2 = cc.coordination_cache_get_or_build(key, build_fn)
    assert calls["n"] == 1, "热命中不得重复计算（缓存命中）"
    assert v2 == v1, "热命中响应应与冷命中响应等价"
    assert s2 in ("mem", "redis", "coalesced")


def test_concurrency_same_key_computes_once():
    cc = import_cache()
    cc.coordination_cache_reset()
    calls = {"n": 0}

    def build_fn():
        calls["n"] += 1
        time.sleep(0.05)
        return b"payload"

    key = _key(cc)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda _: cc.coordination_cache_get_or_build(key, build_fn), range(8)))
    assert calls["n"] == 1, "并发同 key 必须去重（只计算一次）"
    assert all(r[0] == b"payload" for r in results)


def test_exception_does_not_pollute_cache():
    cc = import_cache()
    cc.coordination_cache_reset()
    calls = {"n": 0, "fail": True}

    def build_fn():
        calls["n"] += 1
        if calls["fail"]:
            raise RuntimeError("boom")
        return b"recovered"

    key = _key(cc)
    with pytest.raises(RuntimeError):
        cc.coordination_cache_get_or_build(key, build_fn)
    calls["fail"] = False
    v, _s = cc.coordination_cache_get_or_build(key, build_fn)
    assert calls["n"] == 2, "失败不得缓存；下次必须真正重算"
    assert v == b"recovered"


def test_build_statistics_recorded():
    cc = import_cache()
    cc.coordination_cache_reset()
    key = _key(cc)
    cc.coordination_cache_get_or_build(key, lambda: b"x")
    st = cc.coordination_cache_stats()
    assert st.get("builds", 0) >= 1, "应记录建数"
    assert "hits_mem" in st and "hits_redis" in st, "应暴露命中来源统计"


# ---------------------------------------------------------------------------
# 2) API 集成：真实 /coordination 端点接入缓存
# ---------------------------------------------------------------------------

def _artifact_rounds(**kw):
    base = {
        "schema_version": TACTICAL_SCHEMA_VERSION, "match_id": 1, "map_name": "de_mirage", "tick_rate": 64.0,
        "rounds": [{
            "round": 1, "start_tick": 65, "end_tick": 10000, "freeze_end_tick": 65,
            "winner_side": 2, "winner_team": "A", "team_side_map": {"A": 2, "B": 3},
            "players": {
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


class FakeStorage:
    def __init__(self, data):
        self.data = {tactical_object_key(1, TACTICAL_SCHEMA_VERSION): data}

    def get_bytes(self, key):
        if key not in self.data:
            raise FileNotFoundError(key)
        return self.data[key]


@pytest.fixture(autouse=True)
def _clear_cache():
    cc = import_cache()
    cc.coordination_cache_reset()
    yield
    cc.coordination_cache_reset()


@pytest.fixture
def client(monkeypatch):
    from app.services import storage as storage_module

    tmp = tempfile.NamedTemporaryFile(prefix="coordcache-", suffix=".db", delete=False)
    tmp.close()
    engine = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False, "timeout": 30})
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
    engine.dispose()
    for _ in range(20):
        try:
            os.unlink(tmp.name)
            break
        except PermissionError:
            time.sleep(0.05)


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


def _spy_compute(monkeypatch):
    """拦截 build_coordination_analysis + _build_area_state_machine_payload，计数。"""
    orig_build = personal_mod.build_coordination_analysis
    orig_asm = personal_mod._build_area_state_machine_payload
    calls = {"n": 0}

    def spy_build(*a, **k):
        calls["n"] += 1
        return orig_build(*a, **k)

    def spy_asm(*a, **k):
        calls["n"] += 1
        return orig_asm(*a, **k)

    monkeypatch.setattr(personal_mod, "build_coordination_analysis", spy_build)
    monkeypatch.setattr(personal_mod, "_build_area_state_machine_payload", spy_asm)
    return calls


@pytest.mark.parametrize("workers", [2, 6])
def test_endpoint_response_equivalent_and_compute_once(monkeypatch, client, workers):
    """冷+热等价；且同一 (demo, round, focus) 并发下 compute 只发生一次（去重）。"""
    c, h, S = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    calls = _spy_compute(monkeypatch)

    def fetch():
        return c.get("/api/demos/1/coordination", headers=h)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        responses = [f.result() for f in [ex.submit(fetch) for _ in range(workers)]]
    for r in responses:
        assert r.status_code == 200
    bodies = [r.json() for r in responses]
    # 响应等价 + 确定性 hash
    assert bodies[0]["content_hash"] == bodies[-1]["content_hash"]
    assert bodies[0] == bodies[-1], "并发冷/热响应应完全一致"
    assert len(bodies[-1]["area_state_machine"]) > 0
    # 去重：整场并发只发生一次 compute（build+asm 各一次）
    assert calls["n"] == 2, f"并发 {workers} 次应只计算 1 次（build+asm=2 次工具），实为 {calls['n']}"


def test_endpoint_cold_then_hot_no_recompute(monkeypatch, client):
    c, h, S = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    calls = _spy_compute(monkeypatch)

    r1 = c.get("/api/demos/1/coordination", headers=h)
    assert r1.status_code == 200
    n_after_cold = calls["n"]
    assert n_after_cold == 2, "冷命中应 build+asm 各一次"

    r2 = c.get("/api/demos/1/coordination", headers=h)
    assert r2.status_code == 200
    assert r2.json()["content_hash"] == r1.json()["content_hash"]
    assert r2.json() == r1.json()
    assert r2.content == r1.content, "原始 coordination 响应（缓存命中）应与冷命中逐字节一致（响应等价）"
    assert calls["n"] == n_after_cold, "热命中不得重复计算"


def test_endpoint_focus_isolation_and_no_leak(monkeypatch, client):
    """不同 focus_player_id / 不同用户 → 不同 key，互不串缓存（player 段不跨用户泄漏）。"""
    c, h, S = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    calls = _spy_compute(monkeypatch)

    # focus A = user 的存续 focus (player 1)
    b1 = c.get("/api/demos/1/coordination", headers=h).json()
    n1 = calls["n"]

    # 换 focus → 新 key → 必须重算；且 player 段与 b1 不同（隔离）
    db = S()
    dfp = db.query(DemoFocusPlayer).filter(DemoFocusPlayer.user_id == 1).one()
    dfp.player_id = 2
    db.commit(); db.close()
    b2 = c.get("/api/demos/1/coordination", headers=h).json()
    assert calls["n"] > n1, "focus 变化必须失效并重算"
    assert b2["player"]["focus_player_id"] == 2
    assert b1["player"]["focus_player_id"] == 1
    assert b1["player"] != b2["player"], "不得把 A 的 player 段缓存串给 B"


def test_endpoint_unauthorized_never_populates_cache(monkeypatch, client):
    """未授权（非 max 或未授予团队）必须 4xx，且不得写缓存（compute 不因未授权而跑）。"""
    c, h, S = client
    # 不设 tier/entitlement → 403
    calls = _spy_compute(monkeypatch)
    r = c.get("/api/demos/1/coordination", headers=h)
    assert r.status_code == 403
    assert calls["n"] == 0, "未授权不得触发 compute/写缓存（鉴权先于缓存）"


def test_endpoint_cross_tenant_no_leak(client):
    """其它用户（非 owner）不得命中 owner 的缓存（ownership 先于缓存 → 404，绝不泄漏）。"""
    c, h, S = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    r1 = c.get("/api/demos/1/coordination", headers=h)
    assert r1.status_code == 200

    db = S()
    db.add(User(email="b@test.com", password_hash=hash_password("password123"), nickname="B"))
    db.commit(); db.close()
    tb = c.post("/api/auth/login", json={"email": "b@test.com", "password": "password123"}).json()["access_token"]
    rb = c.get("/api/demos/1/coordination", headers={"Authorization": f"Bearer {tb}"})
    assert rb.status_code == 404, "跨用户不得从缓存拿到他人 demo 数据"


def test_endpoint_algorithm_version_invalidation(monkeypatch, client):
    """算法/口径版本变化 → 新 key → 失效并重算（TDD：缓存 key 覆盖算法版本）。"""
    from app.services import coordination_cache as cc_mod

    c, h, S = client
    _set_tier(S, "a@test.com", "max")
    _grant_team(S)
    calls = _spy_compute(monkeypatch)
    r1 = c.get("/api/demos/1/coordination", headers=h)
    assert r1.status_code == 200
    n1 = calls["n"]
    monkeypatch.setattr(cc_mod, "COORDINATION_SCHEMA_VERSION", "3.0")
    r2 = c.get("/api/demos/1/coordination", headers=h)
    assert r2.status_code == 200
    assert calls["n"] > n1, "算法版本变化必须失效并重算"
