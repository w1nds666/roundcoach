"""Phase 5A 测试：metrics 证据契约 / Provider 抽象与 gate / 质量校验 / API / 任务状态机。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import AIReport, Demo, Event, HeatmapCache, Match, Player, PlayerStats, User
from app.services import storage as storage_module
from app.services.security import hash_password
from app.api import demo as demo_api, ai_report as ai_report_api


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    class FakeStorage:
        def upload_bytes(self, key, data, content_type="image/png"):
            return key

        def upload_file(self, key, path, content_type="application/octet-stream"):
            return key

        def get_bytes(self, key):
            return b"png"

        def exists(self, key):
            return True

    monkeypatch.setattr(storage_module, "storage", FakeStorage())
    monkeypatch.setattr(demo_api, "storage", FakeStorage())

    # 避免测试环境连 Redis/真实 DB：mock 掉 Celery 投递（同步执行）并让任务用测试 session
    from app.tasks import ai_report_tasks
    from app.tasks.ai_report_tasks import generate_ai_report as _original_task

    fake_task = _FakeTask(_original_task)
    monkeypatch.setattr(ai_report_tasks, "generate_ai_report", fake_task)
    monkeypatch.setattr(ai_report_tasks, "SessionLocal", TestingSession)
    monkeypatch.setattr(ai_report_api, "generate_ai_report", fake_task)  # API 模块已绑定引用

    db = TestingSession()
    user = User(email="a@test.com", password_hash=hash_password("password123"), nickname="A")
    user_b = User(email="b@test.com", password_hash=hash_password("password123"), nickname="B")
    db.add_all([user, user_b])
    db.flush()
    demo = Demo(user_id=user.id, file_name="a.dem", file_path="demos/abc.dem", file_size=100,
                status="parsed", map_name="de_mirage",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2))
    db.add(demo)
    db.flush()
    match = Match(demo_id=demo.id, map_name="de_mirage", score_ct=8, score_t=2, duration_seconds=453, rounds=10)
    db.add(match)
    db.flush()
    demo.match_id = match.id
    p = Player(match_id=match.id, steam_id="1", name="P1", team="CT")
    p2 = Player(match_id=match.id, steam_id="2", name="P2", team="T")
    db.add_all([p, p2])
    db.flush()
    db.add(PlayerStats(match_id=match.id, player_id=p.id, kills=6, deaths=4, assists=2, hs_kills=2, hs_rate=0.33, damage=900, adr=90.0, first_kills=2, weapon_stats={}))
    db.add(PlayerStats(match_id=match.id, player_id=p2.id, kills=3, deaths=5, assists=1, hs_kills=1, hs_rate=0.33, damage=400, adr=40.0, first_kills=0, weapon_stats={}))
    for i, (tick, rnd, actor, victim, weapon) in enumerate([
        (100, 1, "1", "2", "ak47"), (200, 1, "2", "1", "awp"), (300, 2, "1", "2", "ak47"),
    ]):
        db.add(Event(match_id=match.id, tick=tick, round=rnd, event_type="kill", actor_steam=actor,
                     victim_steam=victim, weapon=weapon, x=1.0, y=2.0))
    db.add(Event(match_id=match.id, tick=400, round=2, event_type="grenade", actor_steam="1", grenade_type="smoke", x=1.0, y=2.0))
    db.add(HeatmapCache(match_id=match.id, map_name="de_mirage", mode="kills_all", heatmap_key="h/1.png"))
    db.commit()
    db.close()

    with TestClient(app) as c:
        token_a = c.post("/api/auth/login", json={"email": "a@test.com", "password": "password123"}).json()["access_token"]
        token_b = c.post("/api/auth/login", json={"email": "b@test.com", "password": "password123"}).json()["access_token"]
        yield c, {"Authorization": f"Bearer {token_a}"}, {"Authorization": f"Bearer {token_b}"}, TestingSession
    app.dependency_overrides.clear()


class _FakeTask:
    """同步执行 Celery 任务（delay = 直接调用原始任务函数）。"""
    def __init__(self, original):
        self._original = original

    def delay(self, *args, **kwargs):
        return self._original(*args, **kwargs)


def _set_subscription(TestingSession, email, tier, expire_days):
    db = TestingSession()
    u = db.query(User).filter(User.email == email).one()
    u.subscription_tier = tier
    u.subscription_expire = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expire_days) if expire_days else None
    db.commit()
    db.close()


# ---------- A: metrics builder ----------

def test_metrics_snapshot_deterministic_and_versioned(client):
    from app.database import SessionLocal as _unused  # noqa
    from app.services.ai_metrics import build_metrics_snapshot, compute_input_hash

    c, h, _, TestingSession = client
    db = TestingSession()
    s1 = build_metrics_snapshot(db, 1)
    s2 = build_metrics_snapshot(db, 1)
    db.close()
    assert s1["schema_version"] == "1.0"
    assert s1 == s2                                   # 确定性
    assert compute_input_hash(s1) == compute_input_hash(s2)
    assert len(compute_input_hash(s1)) == 64          # sha256
    assert s1["match"]["score_ct"] == 8
    assert s1["kills"]["total"] == 3
    assert s1["grenades"]["total"] == 1
    assert s1["grenades"]["by_type"][0]["type"] == "smoke"
    # 匿名玩家：无真实 steam id
    assert all("steam" not in p for p in s1["players"])
    # 未实现指标显式 null
    assert s1["unimplemented"]["team.trade_rate"] is None
    assert s1["unimplemented"]["team.clutch_winrate"] is None


def test_input_hash_changes_with_data(client):
    from app.services.ai_metrics import build_metrics_snapshot, compute_input_hash

    c, h, _, TestingSession = client
    db = TestingSession()
    s1 = build_metrics_snapshot(db, 1)
    h1 = compute_input_hash(s1)
    # 修改一条事件后快照应变化
    ev = db.query(Event).filter(Event.event_type == "kill").first()
    ev.round = 99
    db.commit()
    s2 = build_metrics_snapshot(db, 1)
    db.close()
    assert h1 != compute_input_hash(s2)


# ---------- C: provider ----------

def test_fake_provider_deterministic():
    from app.services.ai_metrics import build_metrics_snapshot
    from app.services.ai_providers import FakeAIReportProvider

    # 用独立内存库构造 metrics 太繁；直接验证 fake provider 的模板行为
    metrics = {
        "schema_version": "1.0",
        "match": {"map_name": "de_mirage", "score_ct": 8, "score_t": 2, "rounds": 10, "duration_seconds": 453},
        "teams": {"CT": {"kills": 9, "deaths": 6, "assists": 2, "damage": 1300, "adr": 130.0, "first_kills": 2},
                  "T": {"kills": 3, "deaths": 5, "assists": 1, "damage": 400, "adr": 40.0, "first_kills": 0}},
        "kills": {"total": 12, "top_weapons": [{"weapon": "ak47", "count": 6}], "by_round_max": [3, 5]},
        "grenades": {"total": 10, "by_type": [{"type": "smoke", "count": 4}]},
        "players": [{"player_index": 1, "team": "CT", "kills": 6, "deaths": 4, "assists": 2, "hs_rate": 0.33,
                     "damage": 900, "adr": 90.0, "first_kills": 2, "source": "player_stats"}],
        "unimplemented": {"team.trade_rate": None},
    }
    p = FakeAIReportProvider()
    r1 = p.generate("prompt", metrics)
    r2 = p.generate("prompt", metrics)
    assert r1.text == r2.text          # 确定性
    assert "## 摘要" in r1.text
    assert "de_mirage" in r1.text
    assert r1.cost_cents == 0


def test_deepseek_provider_gated():
    from app.services.ai_providers import DeepSeekAIReportProvider, ProviderDisabled

    with pytest.raises(ProviderDisabled):
        DeepSeekAIReportProvider()     # allow_external_llm=False → 构造即禁用


# ---------- D: quality ----------

def test_quality_valid_report_passes():
    from app.services.ai_report_quality import validate_report_structure

    md = """## 摘要

本场 de_mirage 共 12 次击杀。[kills.total]

## 优势（3）

1. CT 火力集中 [teams.ct.kills]
2. 高输出选手 [players.1.adr]
3. 武器结构 [kills.top_weapons]

## 问题（3）

1. T 方输出不足 [teams.t.damage]
2. 道具结构单一 [grenades.by_type]
3. 回合火力波动 [kills.by_round_max]

## 可执行建议

1. 围绕高输出选手组织进攻 [players.1.adr]
2. 增加道具使用 [grenades.by_type]

## 证据

- kills.* / teams.*
"""
    metrics = {
        "kills": {"total": 12, "top_weapons": [], "by_round_max": [3, 5]},
        "teams": {"ct": {"kills": 9}, "t": {"damage": 400}},
        "grenades": {"by_type": []},
        "players": [{"adr": 90.0}],
    }
    assert validate_report_structure(md, metrics) == []


def test_quality_fake_metric_key_rejected():
    from app.services.ai_report_quality import validate_report_structure

    md = "## 摘要\n\nx [kills.total]\n\n## 优势（3）\n\n1. a [fake.metric.key]\n2. b\n3. c\n\n## 问题（3）\n\n1. a\n2. b\n3. c\n\n## 可执行建议\n\n1. a\n\n## 证据\n\n- x\n"
    metrics = {"kills": {"total": 1}}
    violations = validate_report_structure(md, metrics)
    assert any("fake.metric.key" in v for v in violations)


def test_quality_hollow_and_forbidden_rejected():
    from app.services.ai_report_quality import validate_report_structure

    md = "## 摘要\n\n加油！\n\n## 优势（3）\n\n1. 相信你一定能 [kills.total]\n2. b\n3. c\n\n## 问题（3）\n\n1. a\n2. b\n3. c\n\n## 可执行建议\n\n1. a\n\n## 证据\n\n- x\n"
    metrics = {"kills": {"total": 1}}
    violations = validate_report_structure(md, metrics)
    assert any("空洞" in v or "越界" in v for v in violations)


def test_sanitize_markdown_strips_html():
    from app.services.ai_report_quality import sanitize_markdown

    md = '## 摘要\n\n<script>alert(1)</script><a href="https://evil">链接</a>![](https://evil/x.png)正常文本'
    out = sanitize_markdown(md)
    assert "<script>" not in out
    assert "href=" not in out
    assert "![](http" not in out
    assert "正常文本" in out


# ---------- B: 任务状态机 + API ----------

def test_create_report_requires_pro(client):
    c, h, _, _ = client
    # free：entitlement 明确锁定
    ent = c.get("/api/demos/1/ai-report/entitlement", headers=h).json()
    assert ent == {"can_generate": False, "reason": "subscription_required", "single_purchase_available": False}
    resp = c.post("/api/demos/1/ai-report", headers=h)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"


def test_ai_report_lifecycle_and_idempotent(client):
    c, h, _, TestingSession = client
    _set_subscription(TestingSession, "a@test.com", "pro", 30)
    ent = c.get("/api/demos/1/ai-report/entitlement", headers=h).json()
    assert ent["can_generate"] is True

    # 创建（同步任务执行 → 直接 succeeded）
    resp = c.post("/api/demos/1/ai-report", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reused"] is False
    report = body["report"]
    assert report["status"] == "succeeded", report.get("error")
    assert report["provider"] == "fake"
    assert report["cost_cents"] == 0
    # 6R11.2 真实性门禁：fake provider → 报告不可信（不得当作正式 AI 结论）
    assert report["trust"]["trusted"] is False
    assert "fake_provider" in report["trust"]["reasons"]

    # 幂等：再次创建返回同一报告
    resp2 = c.post("/api/demos/1/ai-report", headers=h).json()
    assert resp2["reused"] is True
    assert resp2["report"]["id"] == report["id"]

    # 读取详情：markdown 非空且已消毒
    detail = c.get(f"/api/demos/1/ai-report/{report['id']}", headers=h).json()
    assert detail["status"] == "succeeded"
    assert detail["markdown"] and "## 摘要" in detail["markdown"]
    assert "<" not in detail["markdown"]

    # 列表
    lst = c.get("/api/demos/1/ai-report", headers=h).json()
    assert len(lst) == 1
    assert lst[0]["trust"]["trusted"] is False   # 列表同样带 trust


def test_report_trust_fake_and_template_untrusted(client):
    """provider=fake 或 模板 model → 不可信（即使 hash 匹配、markdown 干净）。"""
    from types import SimpleNamespace
    from app.api.ai_report import _trust_status, _metrics_summary
    from app.services.ai_metrics import build_metrics_snapshot, compute_input_hash
    c, h, _, TestingSession = client
    db = TestingSession()
    cur = compute_input_hash(build_metrics_snapshot(db, 1))
    db.close()
    r = SimpleNamespace(
        provider="fake", model="deterministic-template-v1",
        input_hash=cur, markdown="## 摘要\n\n本场结果。",
        metrics_snapshot={"demo": {"id": 1, "map_name": "de_mirage"}, "match": {"map_name": "de_mirage", "score_ct": 8, "score_t": 2, "rounds": 10}},
    )
    st = _trust_status(r, cur)
    assert st["trusted"] is False
    assert "fake_provider" in st["reasons"]
    assert "template_provider" in st["reasons"]
    assert "stale_snapshot" not in st["reasons"]
    assert _metrics_summary(r) == {"map_name": "de_mirage", "score_ct": 8, "score_t": 2, "rounds": 10, "demo_id": 1}


def test_report_trust_unresolved_placeholders_untrusted(client):
    """markdown 含未解析 `[a.b]` 模板键 → 不可信（即使 provider 非 fake）。"""
    from types import SimpleNamespace
    from app.api.ai_report import _trust_status
    from app.services.ai_metrics import build_metrics_snapshot, compute_input_hash
    c, h, _, TestingSession = client
    db = TestingSession()
    cur = compute_input_hash(build_metrics_snapshot(db, 1))
    db.close()
    r = SimpleNamespace(
        provider="llm-run", model="summary-v2", input_hash=cur,
        markdown="## 摘要\n\n本场 [match.map_name] 共 [kills.total] 次击杀。[teams.ct.kills]",
        metrics_snapshot={},
    )
    st = _trust_status(r, cur)
    assert st["trusted"] is False
    assert "unresolved_placeholders" in st["reasons"]
    assert "fake_provider" not in st["reasons"]


def test_report_trust_stale_snapshot_untrusted(client):
    """input_hash 与当前 demo 不一致（数据已变）→ stale 不可信。"""
    from types import SimpleNamespace
    from app.api.ai_report import _trust_status
    from app.services.ai_metrics import build_metrics_snapshot, compute_input_hash
    c, h, _, TestingSession = client
    db = TestingSession()
    cur = compute_input_hash(build_metrics_snapshot(db, 1))
    db.close()
    r = SimpleNamespace(
        provider="llm-run", model="summary-v2", input_hash="deadbeef" * 8,
        markdown="## 摘要\n\n本场结果。", metrics_snapshot={},
    )
    st = _trust_status(r, cur)
    assert st["trusted"] is False
    assert "stale_snapshot" in st["reasons"]


def test_report_trust_real_matching_trusted(client):
    """真实且匹配（非 fake、非模板、hash 一致、无模板键）→ 可信任。"""
    from types import SimpleNamespace
    from app.api.ai_report import _trust_status
    from app.services.ai_metrics import build_metrics_snapshot, compute_input_hash
    c, h, _, TestingSession = client
    db = TestingSession()
    cur = compute_input_hash(build_metrics_snapshot(db, 1))
    db.close()
    r = SimpleNamespace(
        provider="llm-run", model="summary-v2", input_hash=cur,
        markdown="## 摘要\n\n本场结果正常，无未解析键。", metrics_snapshot={},
    )
    st = _trust_status(r, cur)
    assert st["trusted"] is True
    assert st["reasons"] == []


def test_ai_report_cross_user_404(client):
    c, h_a, h_b, _ = client
    assert c.get("/api/demos/1/ai-report/entitlement", headers=h_b).status_code == 404
    assert c.post("/api/demos/1/ai-report", headers=h_b).status_code == 404
    assert c.get("/api/demos/1/ai-report", headers=h_b).status_code == 404


def test_ai_report_retry_failed(client, monkeypatch):
    c, h, _, TestingSession = client
    _set_subscription(TestingSession, "a@test.com", "pro", 30)

    from app.tasks import ai_report_tasks as tasks_mod

    # fixture 已把 generate_ai_report patch 为 _FakeTask（持有原始任务）
    original_task = tasks_mod.generate_ai_report._original

    def failing_delay(report_id):
        db = TestingSession()
        r = db.get(AIReport, report_id)
        r.status = "failed"
        r.error = "报告生成失败，请稍后重试"
        db.commit()
        db.close()

    class FailingTask:
        def delay(self, rid):
            failing_delay(rid)

    monkeypatch.setattr(tasks_mod, "generate_ai_report", FailingTask())
    monkeypatch.setattr(ai_report_api, "generate_ai_report", FailingTask())
    resp = c.post("/api/demos/1/ai-report", headers=h).json()
    assert resp["report"]["status"] == "failed"

    # retry：恢复同步执行 → succeeded
    monkeypatch.setattr(tasks_mod, "generate_ai_report", _FakeTask(original_task))
    monkeypatch.setattr(ai_report_api, "generate_ai_report", _FakeTask(original_task))
    rid = resp["report"]["id"]
    ret = c.post(f"/api/demos/1/ai-report/{rid}/retry", headers=h)
    assert ret.status_code == 200
    assert ret.json()["report"]["status"] == "succeeded"
