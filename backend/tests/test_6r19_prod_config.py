"""6R19B P0 生产配置硬化门禁：compose 无硬编码凭据、MinIO 不暴露 0.0.0.0、镜像 pin、非 root、生产 overlay/守卫。

只读静态断言 + `docker compose config` 的 exit code 校验（输出含 secret，故绝不打印）。
"""
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
COMPOSE_PROD = ROOT / "docker-compose.prod.yml"
DOCKERFILE = ROOT / "backend" / "Dockerfile"
ENV_EXAMPLE = ROOT / ".env.example"


class _Loader(yaml.SafeLoader):
    pass


def _reset_constructor(loader, node):
    """docker compose 的 !reset 覆盖：重置列表为空（PyYAML 不识别，需注册）。"""
    return []


_Loader.add_constructor("!reset", _reset_constructor)


def _load(p: Path) -> dict:
    return yaml.load(p.read_text(encoding="utf-8"), Loader=_Loader)


def test_compose_has_no_hardcoded_secrets():
    """compose / prod overlay 不得包含开发默认凭据字面量（应一律 ${VAR:?} 注入）。"""
    for f in (COMPOSE, COMPOSE_PROD):
        text = f.read_text(encoding="utf-8")
        assert "cs2_dev_password" not in text, f"{f.name} 含硬编码 DB 凭据"
        assert "cs2minio_secret" not in text, f"{f.name} 含硬编码 MinIO 凭据"
        assert "cs2:cs2_dev_password" not in text


def test_compose_minio_not_exposed_broadcast():
    """MinIO 端口不得绑定 0.0.0.0/::，只允许 127.0.0.1 或内部。"""
    c = _load(COMPOSE)
    ports = c["services"]["minio"]["ports"]
    for p in ports:
        assert p.startswith("127.0.0.1:"), f"MinIO 端口应仅绑定 127.0.0.1：{p}"


def test_compose_images_pinned_digest():
    """MinIO / mc 镜像必须 pin 到 digest（不依赖 latest 浮动）。"""
    c = _load(COMPOSE)
    assert "@sha256:" in c["services"]["minio"]["image"]
    assert "@sha256:" in c["services"]["minio-init"]["image"]


def test_prod_overlay_hardening():
    """生产 overlay：ENV=production、read_only、tmpfs、端口 !reset、非 root 依赖 Dockerfile USER。"""
    p = _load(COMPOSE_PROD)
    assert p["services"]["backend"]["environment"]["ENV"] == "production"
    assert p["services"]["worker"]["environment"]["ENV"] == "production"
    assert p["services"]["backend"].get("read_only") is True
    assert p["services"]["worker"].get("read_only") is True
    assert "tmpfs" in p["services"]["backend"]
    assert "!reset" in COMPOSE_PROD.read_text(encoding="utf-8")  # 已覆盖端口
    # Dockerfile 非 root
    df = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^\s*USER\s+app\s*$", df, re.M), "Dockerfile 缺非 root USER"


def test_env_example_is_secret_free():
    """.env.example 只含占位，不含任何开发/真实凭据值。"""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "cs2_dev_password" not in text and "cs2minio_secret" not in text
    assert "REPLACE" in text  # 占位标识


def test_prod_disables_docs_and_guards():
    """main.py 在生产 env 下禁用 docs/openapi；config.py 在生产 env 下强制守卫。"""
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert 'docs_url=None if settings.env == "production" else "/docs"' in main
    assert 'openapi_url=None if settings.env == "production" else "/openapi.json"' in main
    cfg = (ROOT / "backend" / "app" / "config.py").read_text(encoding="utf-8")
    assert 's.env != "production"' in cfg  # 守卫在 production 才强制（守卫行为由 TestProductionGuard 验证）


def _compose_config(args):
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def test_compose_config_resolves_no_output():
    """`docker compose config`：base 可解析；prod overlay 缺可信代理则 fail-closed（非 0），注入后才通过。输出含 secret，绝不打印。"""
    r = _compose_config(["docker", "compose", "config"])
    assert r.returncode == 0, "docker compose config 解析失败（base）"
    # prod overlay 缺 RATE_LIMIT_TRUSTED_PROXIES → config 必须失败（fail-closed，防留空共桶）
    r2 = _compose_config(["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml", "config"])
    assert r2.returncode != 0, "production overlay 在缺少可信代理时必须 fail-closed（config 退出非 0）"
    # 注入合法可信代理 → config 解析通过
    import os
    r3 = subprocess.run(["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml", "config"],
                        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=120, env={**os.environ, "RATE_LIMIT_TRUSTED_PROXIES": "10.0.0.0/8,203.0.113.0/24"})
    assert r3.returncode == 0, "注入合法可信代理后 production overlay config 应可解析"


def test_prod_guard_rejects_placeholder_env(monkeypatch):
    """6R19B：production 下 .env.example 占位整组必须使 _validate_runtime 抛错（占位即 NO-GO）。"""
    from app.config import Settings, _validate_runtime
    s = Settings(_env_file=None, env="production",
                 jwt_secret="REPLACE_WITH_RANDOM_SECRET_AT_LEAST_32_BYTES",
                 database_url="postgresql+psycopg://REPLACE_DB_USER:REPLACE_DB_PASSWORD@postgres:5432/cs2demo",
                 minio_access_key="REPLACE_MINIO_USER", minio_secret_key="REPLACE_MINIO_SECRET",
                 steam_openid_require_https=True, steam_openid_use_fake_verifier=False)
    import pytest
    with pytest.raises(RuntimeError):
        _validate_runtime(s)


def test_prod_guard_accepts_safe_env(monkeypatch):
    """6R19B：合法随机 secrets + HTTPS + 非空合法可信代理 → 通过。"""
    from app.config import Settings, _validate_runtime
    s = Settings(_env_file=None, env="production", jwt_secret="f" * 48,
                 database_url="postgresql+psycopg://cs2:S9uP3rRand0m@postgres:5432/cs2demo",
                 minio_access_key="MinioAccessK9", minio_secret_key="MinioSecretK9",
                 rate_limit_trusted_proxies="10.0.0.0/8,203.0.113.0/24", steam_openid_require_https=True)
    _validate_runtime(s)  # 不抛错即通过
