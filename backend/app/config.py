"""应用配置：从环境变量读取，全部有本地开发默认值。

对应计划书 4.3 目录结构中的 config.py。
生产环境（Docker Compose）通过环境变量覆盖。

安全约束（P0-4）：非 debug 环境禁止使用公开默认 JWT_SECRET，
必须显式注入（防止伪造 token）。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 公开默认 secret（仅限本地开发/测试；生产/容器环境禁止使用）
DEFAULT_JWT_SECRET = "dev-secret-change-me-0123456789abcdef"
# 生产/容器（debug=False）必须满足的 JWT_SECRET 最小字节数（6R19：防弱/短 secret 被 HS256 暴力）
MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_name: str = "CS2 Demo Analyzer"
    debug: bool = False
    # 6R19B 部署环境：local（默认，跳过生产守卫）| production（强制生产守卫）。用显式 env 而非 debug 判定，
    # 使本地 dev 容器（debug 缺省 false）仍可启动并保留开发默认值，生产 overlay 才强制硬化。
    env: str = "local"

    # 数据库
    database_url: str = "postgresql+psycopg://cs2:cs2_dev_password@localhost:5432/cs2demo"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # 安全（JWT；生产必须显式注入随机 secret，≥32 字节）
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_expire_minutes: int = 60 * 24 * 7

    # 存储（Phase 1 起用 MinIO）
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "cs2minio"
    minio_secret_key: str = "cs2minio_secret"
    minio_bucket: str = "cs2demo"

    # 上传大小上限（MiB，MVR 验收默认 300；nginx client_max_body_size 需同步留余量）
    max_demo_size_mb: int = 300

    # AI 报告（Phase 5B：DeepSeek V4 adapter 已实现但默认关闭；Fake 为默认）
    ai_provider: str = "fake"          # fake | deepseek（决策门批准并配置 key 后才切 deepseek）
    allow_external_llm: bool = False   # 总开关：True 才允许真实外发（当前保持 false）
    deepseek_model: str = "deepseek-v4-flash"   # 2026-07-24 起 deepseek-chat/reasoner 已退役
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_max_input_tokens: int = 12_000      # 输入硬上限
    deepseek_max_output_tokens: int = 3000       # max_tokens
    deepseek_connect_timeout: float = 10.0       # connect timeout 秒
    deepseek_read_timeout: float = 120.0         # read timeout 秒
    deepseek_total_timeout: float = 130.0        # total timeout 秒
    # 成本保护（人民币，可配置默认保守值）
    max_report_cost_cny: float = 0.05            # 单份估算上限
    monthly_budget_cny: float = 10.0             # 全局月预算
    # 注意：DEEPSEEK_API_KEY 仅由环境变量注入（.env.example 有说明），
    # 本阶段不读取不写入；日志/代码不得打印其值。

    # Steam OpenID 2.0（6R14）：provider 固定官方端点；本阶段以 fake verifier 做自动门禁。
    steam_openid_enabled: bool = True
    steam_openid_provider: str = "https://steamcommunity.com/openid/"
    steam_openid_callback_path: str = "/api/auth/steam/callback"
    # 允许的 return_to/realm 主机白名单（逗号分隔；dev 含 localhost/testserver，生产必须 HTTPS 且按实际域名配置）
    steam_openid_allowed_hosts: str = "testserver,localhost,localhost:3080,localhost:8000,127.0.0.1,127.0.0.1:8000"
    # 生产（debug=False）强制 HTTPS 回调；dev 允许 http
    steam_openid_require_https: bool = False
    steam_openid_state_ttl_seconds: int = 300
    steam_openid_rebind_cooldown_days: int = 7
    steam_openid_recent_auth_seconds: int = 900   # 解绑/换绑最近密码再认证窗口（15 分钟）
    # 自动门禁开关：True 时 check_authentication 用 FakeVerifier（不访问真实 Steam），
    # 供本地/E2E 完成「fake provider」绑定闭环；生产必须 False。
    steam_openid_use_fake_verifier: bool = False

    # 6R19 应用层速率限制：总开关（Redis 可用时按 scope/IP 计数；Redis 不可用则受限 fallback）。
    rate_limit_enabled: bool = True
    # 可信代理（nginx/内部 LB）IP/CIDR，逗号分隔。只信任这些对端转发的 X-Forwarded-For / X-Real-IP；
    # 直连（对端不在该集）一律用 socket IP（防伪造）。空=不信任任何转发头，全部用 socket IP。
    rate_limit_trusted_proxies: str = ""


# 占位 / 开发标记（小写子串命中即拒）。6R19B：不能只看长度或单一精确值。
_LOW_VERACITY_MARKERS = (
    "replace", "changeme", "change_me", "changeit", "example", "sample", "your_",
    "tbd", "to_be_defined", "placeholder", "xxx", "dummy", "fake", "test_secret",
    "dev_", "dev-", "default", "cs2_dev_password", "cs2minio", "cs2_dev",
)


def _is_low_veracity(value: str) -> bool:
    """含占位/开发/示例/默认标记（大小写不敏感）或为空白 → 视为未注入真实秘密。"""
    if not value or not value.strip():
        return True
    v = value.strip().lower()
    return any(marker in v for marker in _LOW_VERACITY_MARKERS)


def _is_valid_cidr_or_ip(token: str) -> bool:
    """token 能否解析为合法 IP 或 CIDR。"""
    import ipaddress
    token = token.strip()
    if not token:
        return False
    try:
        ipaddress.ip_network(token, strict=False)
        return True
    except ValueError:
        pass
    try:
        ipaddress.ip_address(token)
        return True
    except ValueError:
        return False


def _validate_runtime(s: "Settings") -> None:
    """运行时安全守卫（6R19）：非 debug 环境拒绝弱/默认/危险配置。

    - JWT_SECRET：禁止公开默认值；必须 >=MIN_JWT_SECRET_BYTES 字节（防弱 secret 被暴力破解并伪造 sub）。
    - steam_openid_use_fake_verifier：禁止在非 debug 环境打开（否则 check_authentication 被绕过，
      可把任意 SteamID 绑到任意账号；6R14/6R19 认证门禁）。
    - 其余（database_url / minio 默认凭据属于本地/容器开发默认值）：生产请以秘密注入，见 phase6r19-gate-evidence。
    开发（debug=True）时不做以上限制，保证本地/测试可运行。
    """
    if s.env != "production":
        return
    if s.jwt_secret == DEFAULT_JWT_SECRET or _is_low_veracity(s.jwt_secret):
        raise RuntimeError("ENV=production：JWT_SECRET 禁止默认/占位/开发值或为空，请注入随机 secret（>=32B）。")
    if len(s.jwt_secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
        raise RuntimeError(
            f"ENV=production：JWT_SECRET 过弱（{len(s.jwt_secret.encode('utf-8'))}B < {MIN_JWT_SECRET_BYTES}B）。"
        )
    from urllib.parse import urlparse
    parsed = urlparse(s.database_url or "")
    db_user = parsed.username or ""
    db_pass = parsed.password or ""
    if not db_user or not db_pass or "cs2_dev_password" in s.database_url or \
            _is_low_veracity(db_user) or _is_low_veracity(db_pass):
        raise RuntimeError("ENV=production：DATABASE_URL 的用户/密码禁止默认/占位/开发值或为空，请以 secrets 注入。")
    if _is_low_veracity(s.minio_access_key) or _is_low_veracity(s.minio_secret_key):
        raise RuntimeError("ENV=production：MINIO_ACCESS_KEY/SECRET_KEY 禁止占位/开发默认/空值。")
    proxies = [x.strip() for x in (s.rate_limit_trusted_proxies or "").split(",") if x.strip()]
    if not proxies:
        raise RuntimeError("ENV=production：RATE_LIMIT_TRUSTED_PROXIES 必须配置（非空），否则无法按真实客户端限流。")
    for tok in proxies:
        if not _is_valid_cidr_or_ip(tok):
            raise RuntimeError(f"ENV=production：RATE_LIMIT_TRUSTED_PROXIES 含非法 IP/CIDR：{tok!r}。")
    if not s.steam_openid_require_https:
        raise RuntimeError("ENV=production：STEAM_OPENID_REQUIRE_HTTPS 必须为 true（回调必须 HTTPS）。")
    if s.steam_openid_use_fake_verifier:
        raise RuntimeError("ENV=production：STEAM_OPENID_USE_FAKE_VERIFIER 必须为 false（否则认证旁路）。")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    _validate_runtime(s)
    return s


settings = get_settings()
