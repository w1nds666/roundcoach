"""配置接线回归（Codex P1 返工）：compose 两服务 AI 变量齐全且默认安全；.env.example 模型正确。

只读取 docker-compose.yml 与 .env.example 静态文件，不读取真实 .env、不接触任何 key 值。
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent

KEY_AI_VARS = [
    "AI_PROVIDER", "ALLOW_EXTERNAL_LLM", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
    "DEEPSEEK_BASE_URL", "DEEPSEEK_MAX_INPUT_TOKENS", "DEEPSEEK_MAX_OUTPUT_TOKENS",
    "DEEPSEEK_CONNECT_TIMEOUT", "DEEPSEEK_READ_TIMEOUT", "DEEPSEEK_TOTAL_TIMEOUT",
    "MAX_REPORT_COST_CNY", "MONTHLY_BUDGET_CNY",
]


def _compose() -> dict:
    with open(ROOT / "docker-compose.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _env_example() -> str:
    return (ROOT / ".env.example").read_text(encoding="utf-8")


def test_compose_both_services_have_ai_vars():
    """backend 与 worker 均注入全部关键 AI 变量。"""
    compose = _compose()
    for service in ("backend", "worker"):
        env = compose["services"][service]["environment"]
        for var in KEY_AI_VARS:
            assert var in env, f"{service} 缺少 {var}"
        assert "DEEPSEEK_API_KEY" in env, f"{service} 必须声明 DEEPSEEK_API_KEY（空默认）"


def test_compose_defaults_safe():
    """默认值：AI_PROVIDER=fake、ALLOW_EXTERNAL_LLM=false、模型 v4-flash、key 空默认。"""
    compose = _compose()
    for service in ("backend", "worker"):
        env = compose["services"][service]["environment"]
        assert env["AI_PROVIDER"] == "${AI_PROVIDER:-fake}"
        assert env["ALLOW_EXTERNAL_LLM"] == "${ALLOW_EXTERNAL_LLM:-false}"
        assert env["DEEPSEEK_API_KEY"] == "${DEEPSEEK_API_KEY:-}"   # 空默认，绝不输出
        assert env["DEEPSEEK_MODEL"] == "${DEEPSEEK_MODEL:-deepseek-v4-flash}"


def test_env_example_model_is_v4_flash():
    """.env.example 模型为 deepseek-v4-flash（退役模型不得出现为默认）。"""
    text = _env_example()
    assert "DEEPSEEK_MODEL=deepseek-v4-flash" in text
    assert "DEEPSEEK_MODEL=deepseek-chat" not in text
    assert "deepseek-reasoner" not in text.split("DEEPSEEK_MODEL=")[1].split("\n")[0]


def test_env_example_covers_all_ai_vars():
    """.env.example 补齐全部 Phase5B 可配置项。"""
    text = _env_example()
    for var in KEY_AI_VARS:
        assert f"{var}=" in text, f".env.example 缺少 {var}"


def test_compose_key_placeholder_never_leaks_value():
    """compose 中 key 引用必须是空默认占位，不允许出现具体 key 值。"""
    compose = _compose()
    for service in ("backend", "worker"):
        env = compose["services"][service]["environment"]
        key_val = env["DEEPSEEK_API_KEY"]
        assert key_val == "${DEEPSEEK_API_KEY:-}"
        # 静态文件本身不得包含任何形似 key 的值
        compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "sk-" not in compose_text


def test_public_share_resolver_is_post_body_only_and_rate_limited():
    nginx = (ROOT / "deploy" / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    assert "location = /api/share/resolve" in nginx
    assert "limit_req zone=share_resolve" in nginx
    assert "limit_req_status 429" in nginx
    assert "client_max_body_size 2k" in nginx
    assert "SHARE_RATE_LIMITED" in nginx
    assert "/api/share/{" not in nginx
