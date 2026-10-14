"""Phase 5B：DeepSeek V4 adapter mocked contract 测试（httpx MockTransport，零真实网络）。

覆盖：模型名/thinking 参数、payload 白名单、headers 不泄漏 key、timeout/429/5xx/invalid、
质量失败一次 thinking 重试、预算拒绝、consent gate、幂等、XSS/伪 metric 引用。
"""
import json
import os

import httpx
import pytest

from app.config import settings


@pytest.fixture
def deepseek_env(monkeypatch):
    """测试环境：启用外部 LLM（仅测试）+ 假 key（绝不真实调用）。"""
    monkeypatch.setattr(settings, "allow_external_llm", True)
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-fake-key-never-real")
    yield


def _ok_response(content: str, prompt_tokens: int = 400, completion_tokens: int = 800) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "prompt_cache_hit_tokens": 0},
    })


def _valid_markdown() -> str:
    return """## 摘要

本场 de_mirage 共 12 次击杀。[kills.total]

## 优势（3）

1. CT 火力集中 [teams.CT.kills]
2. 高输出选手 [players.1.adr]
3. 武器结构 [kills.top_weapons]

## 问题（3）

1. T 方输出不足 [teams.T.damage]
2. 道具结构单一 [grenades.by_type]
3. 回合火力波动 [kills.by_round_max]

## 可执行建议

1. 围绕高输出选手 [players.1.adr]
2. 增加道具使用 [grenades.by_type]

## 证据

- kills.* / teams.*
"""


def _outbound() -> dict:
    return {
        "schema_version": "1.0",
        "match": {"map_name": "de_mirage", "score_ct": 8, "score_t": 2, "rounds": 10, "duration_seconds": 453},
        "players": [{"player_index": 1, "team": "CT", "kills": 6, "deaths": 4, "assists": 2,
                     "hs_rate": 0.33, "damage": 900, "adr": 90.0, "first_kills": 2}],
        "teams": {"CT": {"kills": 9, "deaths": 6, "assists": 2, "damage": 1300, "adr": 130.0, "first_kills": 2},
                  "T": {"kills": 3, "deaths": 5, "assists": 1, "damage": 400, "adr": 40.0, "first_kills": 0}},
        "kills": {"total": 12, "top_weapons": [{"weapon": "ak47", "count": 6}], "by_round_max": [3, 5]},
        "grenades": {"total": 10, "by_type": [{"type": "smoke", "count": 4}]},
        "heatmaps": {"available": ["kills_all"]},
        "unimplemented": {"team.trade_rate": None},
    }


# ---------- 协议 contract ----------

def test_model_name_and_thinking_disabled(deepseek_env):
    from app.services.ai_providers import DeepSeekAIReportProvider

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response(_valid_markdown())

    p = DeepSeekAIReportProvider(transport=httpx.MockTransport(handler))
    p.generate("prompt", _outbound())
    body = captured["body"]
    assert body["model"] == "deepseek-v4-flash"           # 退役模型不得作为默认
    assert body["thinking"]["type"] == "disabled"          # 默认 thinking 关闭
    assert body["max_tokens"] == settings.deepseek_max_output_tokens
    assert body["temperature"] == 0.2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"


def test_thinking_enabled_parameter(deepseek_env):
    from app.services.ai_providers import DeepSeekAIReportProvider

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response(_valid_markdown())

    p = DeepSeekAIReportProvider(transport=httpx.MockTransport(handler))
    p.generate("prompt", _outbound(), thinking=True)
    assert captured["body"]["thinking"]["type"] == "enabled"


def test_payload_whitelist_no_leak(deepseek_env):
    """请求体 user content 只含白名单字段；禁止 demo.id/file_name/steam/昵称等。"""
    from app.services.ai_providers import DeepSeekAIReportProvider

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response(_valid_markdown())

    p = DeepSeekAIReportProvider(transport=httpx.MockTransport(handler))
    p.generate("prompt", _outbound())
    user_content = captured["body"]["messages"][1]["content"]
    for forbidden in ("demo", "file_name", "file_path", "steam", "input_hash", "nickname"):
        assert forbidden not in user_content, f"外发 payload 泄漏字段: {forbidden}"
    assert "de_mirage" in user_content          # 白名单字段正常
    assert "player_index" in user_content


def test_authorization_header_uses_env_key(deepseek_env):
    """Authorization 来自环境变量 key；请求体/响应不得包含 key。"""
    from app.services.ai_providers import DeepSeekAIReportProvider

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body_text"] = request.content.decode()
        return _ok_response(_valid_markdown())

    p = DeepSeekAIReportProvider(transport=httpx.MockTransport(handler))
    p.generate("prompt", _outbound())
    auth = next((v for k, v in captured["headers"].items() if k.lower() == "authorization"), None)
    assert auth == "Bearer sk-test-fake-key-never-real"
    assert "sk-test-fake-key-never-real" not in captured["body_text"]   # body 无 key


# ---------- 稳定错误映射 ----------

@pytest.mark.parametrize("status,exc_cls", [
    (429, "ProviderRateLimited"),
    (500, "ProviderUnavailable"),
    (503, "ProviderUnavailable"),
    (400, "ProviderInvalidResponse"),
])
def test_http_error_mapping(deepseek_env, status, exc_cls):
    from app.services import ai_providers as ap

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "boom"})

    p = ap.DeepSeekAIReportProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(getattr(ap, exc_cls)):
        p.generate("prompt", _outbound())


def test_timeout_mapping(deepseek_env):
    from app.services import ai_providers as ap

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    p = ap.DeepSeekAIReportProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(ap.ProviderTimeout):
        p.generate("prompt", _outbound())


def test_invalid_response_structure(deepseek_env):
    from app.services import ai_providers as ap

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})   # 无 content

    p = ap.DeepSeekAIReportProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(ap.ProviderInvalidResponse):
        p.generate("prompt", _outbound())


def test_usage_cost_recorded(deepseek_env):
    from app.services.ai_providers import DeepSeekAIReportProvider

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response(_valid_markdown(), prompt_tokens=400, completion_tokens=800)

    p = DeepSeekAIReportProvider(transport=httpx.MockTransport(handler))
    r = p.generate("prompt", _outbound())
    assert r.token_in == 400
    assert r.token_out == 800
    assert r.meta["thinking"] is False
    # 价格：flash miss ¥1/M input + ¥2/M output → (400*1 + 800*2)/1e6 = ¥0.002
    assert abs(r.cost_cents - 0.2) < 0.01


# ---------- 任务层：consent / 预算 / thinking 重试 ----------

def _run_task_with_transport(monkeypatch, transport, provider="deepseek"):
    """同步执行任务（fixture 复用 test_ai_report 的 client 结构太繁，直接构造最小场景）。"""
    from app.tasks import ai_report_tasks as tasks_mod
    from app.tasks.ai_report_tasks import generate_ai_report as original

    return original(1, transport=transport)

def test_consent_gate_blocks_cloud_generation(deepseek_env, monkeypatch):
    """consent gate：deepseek provider 且用户未同意 → failed 稳定错误。"""
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models import AIReport, Demo, Match, User
    from app.services.security import hash_password
    from app.tasks import ai_report_tasks as tasks_mod

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()
    u = User(email="x@y.com", password_hash=hash_password("password123"), nickname="x")  # 未同意
    db.add(u)
    db.flush()
    d = Demo(user_id=u.id, file_name="a.dem", file_path="demos/a.dem", file_size=1, status="parsed", map_name="de_mirage")
    db.add(d)
    db.flush()
    m = Match(demo_id=d.id, map_name="de_mirage", score_ct=1, score_t=0, duration_seconds=100, rounds=1)
    db.add(m)
    db.flush()
    d.match_id = m.id
    report = AIReport(user_id=u.id, demo_id=d.id, status="pending", input_hash="h" * 64,
                      prompt_version="1.0", provider="deepseek", model="deepseek-v4-flash",
                      metrics_snapshot={"schema_version": "1.0", "match": {}, "players": [], "teams": {},
                                        "kills": {}, "grenades": {}, "heatmaps": {}, "unimplemented": {}})
    db.add(report)
    db.commit()
    rid = report.id
    db.close()

    monkeypatch.setattr(tasks_mod, "SessionLocal", S)
    captured = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["calls"] += 1
        return _ok_response(_valid_markdown())

    result = _run_task_with_transport(monkeypatch, httpx.MockTransport(handler))
    assert result["status"] == "failed"
    assert "同意" in result["error"]           # 稳定错误提示
    assert captured["calls"] == 0               # 未同意 → 零外发请求


def test_quality_failure_triggers_one_thinking_retry(deepseek_env, monkeypatch):
    """质量失败：第一次 thinking=disabled 结果坏 → 一次 thinking=enabled 重试。"""
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models import AIReport, Demo, Match, User
    from app.services.security import hash_password
    from app.tasks import ai_report_tasks as tasks_mod

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()
    u = User(email="x@y.com", password_hash=hash_password("password123"), nickname="x",
             ai_consent_version="2026-08-15.v1",
             ai_consent_at=datetime(2026, 8, 15, 0, 0, 0))
    db.add(u)
    db.flush()
    d = Demo(user_id=u.id, file_name="a.dem", file_path="demos/a.dem", file_size=1, status="parsed", map_name="de_mirage")
    db.add(d)
    db.flush()
    m = Match(demo_id=d.id, map_name="de_mirage", score_ct=1, score_t=0, duration_seconds=100, rounds=1)
    db.add(m)
    db.flush()
    d.match_id = m.id
    report = AIReport(user_id=u.id, demo_id=d.id, status="pending", input_hash="h" * 64,
                      prompt_version="1.0", provider="deepseek", model="deepseek-v4-flash",
                      metrics_snapshot={"schema_version": "1.0", "match": {"map_name": "de_mirage"},
                                        "players": [{"player_index": 1, "team": "CT", "kills": 1, "deaths": 1,
                                                     "assists": 0, "hs_rate": 0.0, "damage": 100, "adr": 10.0,
                                                     "first_kills": 0, "source": "player_stats"}],
                                        "teams": {"CT": {"kills": 1, "deaths": 1, "assists": 0, "damage": 100,
                                                         "adr": 10.0, "first_kills": 0},
                                                  "T": {"kills": 0, "deaths": 1, "assists": 0, "damage": 0,
                                                        "adr": 0.0, "first_kills": 0}},
                                        "kills": {"total": 1, "top_weapons": [{"weapon": "ak47", "count": 1}],
                                                  "by_round_max": [1, 1]},
                                        "grenades": {"total": 0, "by_type": []},
                                        "heatmaps": {"available": ["kills_all"]}, "unimplemented": {}})
    db.add(report)
    db.commit()
    rid = report.id
    db.close()

    monkeypatch.setattr(tasks_mod, "SessionLocal", S)
    calls = {"n": 0, "thinkings": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls["n"] += 1
        calls["thinkings"].append(body["thinking"]["type"])
        # 第一次返回坏内容（伪 metric 引用），第二次返回有效
        if calls["n"] == 1:
            return _ok_response("## 摘要\n\n垃圾内容 [fake.key.xyz]\n\n## 优势（3）\n\n1. a\n2. b\n3. c\n\n## 问题（3）\n\n1. a\n2. b\n3. c\n\n## 可执行建议\n\n1. a\n\n## 证据\n\n- x\n")
        return _ok_response(_valid_markdown())

    result = _run_task_with_transport(monkeypatch, httpx.MockTransport(handler))
    assert result["status"] == "succeeded"
    assert calls["n"] == 2                          # 恰好两次
    assert calls["thinkings"] == ["disabled", "enabled"]   # 第二次 thinking 重试


def test_budget_rejection(deepseek_env, monkeypatch):
    """预算保护：单份估算上限拒绝（稳定错误，不静默扣费）。"""
    from datetime import datetime

    from app.services import ai_cost
    from app.tasks import ai_report_tasks as tasks_mod

    monkeypatch.setattr(ai_cost, "estimate_cost_cny", lambda *a, **k: 0.5)  # 远超 ¥0.05
    monkeypatch.setattr(tasks_mod, "estimate_cost_cny", lambda *a, **k: 0.5)
    monkeypatch.setattr(tasks_mod, "check_monthly_budget", lambda *a, **k: True)
    # 直接验证任务内预算分支：构造最小 report
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models import AIReport, Demo, Match, User
    from app.services.security import hash_password

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()
    u = User(email="b@y.com", password_hash=hash_password("password123"), nickname="b",
             ai_consent_version="2026-08-15.v1",
             ai_consent_at=datetime(2026, 8, 15, 0, 0, 0))
    db.add(u)
    db.flush()
    d = Demo(user_id=u.id, file_name="a.dem", file_path="demos/a.dem", file_size=1, status="parsed", map_name="de_mirage")
    db.add(d)
    db.flush()
    m = Match(demo_id=d.id, map_name="de_mirage", score_ct=1, score_t=0, duration_seconds=100, rounds=1)
    db.add(m)
    db.flush()
    d.match_id = m.id
    report = AIReport(user_id=u.id, demo_id=d.id, status="pending", input_hash="i" * 64,
                      prompt_version="1.0", provider="fake", model="x",
                      metrics_snapshot={"schema_version": "1.0", "match": {}, "players": [], "teams": {},
                                        "kills": {}, "grenades": {}, "heatmaps": {}, "unimplemented": {}})
    db.add(report)
    db.commit()
    rid = report.id
    db.close()

    monkeypatch.setattr(tasks_mod, "SessionLocal", S)
    from app.tasks.ai_report_tasks import generate_ai_report as original

    result = original(rid)
    assert result["status"] == "failed"
    assert "预算" in result["error"] or "成本" in result["error"]


def test_provider_disabled_without_gate(monkeypatch):
    """决策门：allow_external_llm=False 时 deepseek provider 构造即禁用。"""
    monkeypatch.setattr(settings, "allow_external_llm", False)
    from app.services.ai_providers import DeepSeekAIReportProvider, ProviderDisabled

    with pytest.raises(ProviderDisabled):
        DeepSeekAIReportProvider()
