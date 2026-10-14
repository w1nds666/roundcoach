"""6R19 上线前安全门禁回归：JWT 类型/强度、安全头、雷达穿越、上传魔数、邮箱归一化、int(sub)、AI 用户过滤。

这些用例在修复前表现为红（对应 6R19 各子代理审计发现的可复现行为），修复后为绿。
只读/无外部依赖；上传用例不触达 MinIO/Celery（在魔数校验点前即拒绝）。
"""
import os

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import DEFAULT_JWT_SECRET, Settings, _validate_runtime
from app.database import Base, get_db
from app.main import app
from app.services.security import create_recent_auth_token


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
    with TestClient(app) as c:
        yield c, S
    app.dependency_overrides.clear()


def _reg(client, email="sec@test.com", password="password123"):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "nickname": "SEC"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
class TestJwtTypeConfusion:
    def test_recent_auth_token_cannot_be_used_as_access(self, client):
        """SEC-02：recent_auth 短 TTL 凭证不得当作完整 access token。"""
        c, S = client
        token = _reg(c)
        uid = c.get("/api/auth/me", headers=_auth(token)).json()["id"]
        rat = create_recent_auth_token(uid)
        r = c.get("/api/auth/me", headers=_auth(rat))
        assert r.status_code == 401
        assert r.json()["detail"]["code"] in ("TOKEN_INVALID", "TOKEN_EXPIRED")


def _prod_settings(**kw):
    """ENV=production 下的合法设置（默认全强 + 合法可信代理）；测试可注入坏字段。"""
    base = dict(_env_file=None, env="production", jwt_secret="a" * 32,
                database_url="postgresql+psycopg://cs2:STRONG@postgres:5432/cs2demo",
                minio_access_key="StrongMinioAccessKey", minio_secret_key="STRONGMINIOKEY1234567890",
                rate_limit_trusted_proxies="10.0.0.0/8,203.0.113.0/24", steam_openid_require_https=True)
    base.update(kw)
    return Settings(**base)


class TestWeakSecretGuard:
    def test_short_secret_rejected_production(self):
        """SEC-03：ENV=production 下弱/短 JWT_SECRET 必须拒绝启动。"""
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(jwt_secret="short"))
        # 合法 ≥32 字节 + 生产字段完整 → 通过
        _validate_runtime(_prod_settings())

    def test_dev_default_secret_rejected_production(self):
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(jwt_secret=DEFAULT_JWT_SECRET))

    def test_placeholder_jwt_rejected(self):
        """6R19B：.env.example 的 REPLACE_* 占位（>32 字节）必须被拒，不能只看长度。"""
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(jwt_secret="REPLACE_WITH_RANDOM_SECRET_AT_LEAST_32_BYTES"))

    def test_fake_verifier_rejected_production(self):
        """OpenID SEC-002：ENV=production 下禁止打开 fake verifier（认证旁路）。"""
        with pytest.raises(RuntimeError):
            _validate_runtime(Settings(_env_file=None, env="production", jwt_secret="a" * 32,
                                       database_url="postgresql+psycopg://cs2:STRONG@postgres:5432/cs2demo",
                                       minio_access_key="StrongMinioAccessKey", minio_secret_key="STRONG",
                                       rate_limit_trusted_proxies="10.0.0.0/8",
                                       steam_openid_require_https=True,
                                       steam_openid_use_fake_verifier=True))


class TestProductionGuard:
    def test_rejects_dev_db_password(self):
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(database_url="postgresql+psycopg://cs2:cs2_dev_password@postgres:5432/cs2demo"))

    def test_rejects_dev_minio_secret(self):
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(minio_secret_key="cs2minio_secret"))

    def test_rejects_placeholder_db_user_and_pass(self):
        """6R19B：DATABASE_URL user/password 为 REPLACE_* 占位 → 拒绝。"""
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(database_url="postgresql+psycopg://REPLACE_DB_USER:REPLACE_DB_PASSWORD@postgres:5432/cs2demo"))

    def test_rejects_placeholder_minio(self):
        """6R19B：MinIO ACCESS/SECRET 为 REPLACE_* / 占位 → 拒绝。"""
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(minio_access_key="REPLACE_MINIO_USER", minio_secret_key="REPLACE_MINIO_SECRET"))

    def test_rejects_empty_trusted_proxies(self):
        """6R19B：生产留空 RATE_LIMIT_TRUSTED_PROXIES → 拒绝（防全用户按 socket IP 共桶）。"""
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(rate_limit_trusted_proxies=""))

    def test_rejects_invalid_trusted_proxy_cidr(self):
        """6R19B：生产含非法 IP/CIDR → 拒绝；不能自动信任任意私网。"""
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(rate_limit_trusted_proxies="not-a-cidr"))
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(rate_limit_trusted_proxies="999.999.1.1"))

    def test_env_example_placeholder_set_rejected(self):
        """6R19B：将 .env.example 的整套占位 env 视作生产 → 必须失败（占位即 NO-GO，不虚构）。"""
        from app.config import Settings as _S
        s = _S(_env_file=None, env="production",
               jwt_secret="REPLACE_WITH_RANDOM_SECRET_AT_LEAST_32_BYTES",
               database_url="postgresql+psycopg://REPLACE_DB_USER:REPLACE_DB_PASSWORD@postgres:5432/cs2demo",
               minio_access_key="REPLACE_MINIO_USER", minio_secret_key="REPLACE_MINIO_SECRET",
               steam_openid_require_https=True, steam_openid_use_fake_verifier=False)
        with pytest.raises(RuntimeError):
            _validate_runtime(s)

    def test_safe_set_accepted(self):
        """合法随机 secrets + HTTPS + 非空合法可信代理 → 通过。"""
        _validate_runtime(_prod_settings())

    def test_rejects_steam_https_false(self):
        with pytest.raises(RuntimeError):
            _validate_runtime(_prod_settings(steam_openid_require_https=False))

    def test_local_env_allows_dev_defaults(self):
        """ENV=local（dev）：即便用开发默认凭据也允许启动（本地可运行）。"""
        _validate_runtime(Settings(_env_file=None, env="local", jwt_secret=DEFAULT_JWT_SECRET,
                                   database_url="postgresql+psycopg://cs2:cs2_dev_password@postgres:5432/cs2demo",
                                   minio_secret_key="cs2minio_secret", steam_openid_require_https=False))


class TestIntSub:
    def test_non_numeric_sub_rejected_401_not_500(self, client):
        """SEC-09：签名有效但 sub 非整数 → 稳定 401，而非未捕获 500。"""
        c, S = client
        from app.config import settings
        bad = jwt.encode({"sub": "abc", "typ": "access", "iat": 0, "exp": 9999999999},
                         settings.jwt_secret, algorithm="HS256")
        r = c.get("/api/auth/me", headers=_auth(bad))
        assert r.status_code == 401


class TestEmailNormalized:
    def test_register_normalizes_email(self, client):
        """SEC-06：注册邮箱归一化为小写，大小写变体不可重复注册。"""
        c, S = client
        r = c.post("/api/auth/register", json={"email": "MiXedCase@Test.com", "password": "password123", "nickname": "N"})
        assert r.status_code == 200
        assert r.json()["user"]["email"] == "mixedcase@test.com"
        # 再注册小写变体 → 409
        r2 = c.post("/api/auth/register", json={"email": "mixedcase@test.com", "password": "password123", "nickname": "N"})
        assert r2.status_code == 409
        assert r2.json()["detail"]["code"] == "EMAIL_EXISTS"


class TestSecurityHeaders:
    def test_global_security_headers_present(self, client):
        """注入 SEC-004：全局响应头（点击劫持/MIME 嗅探/Referrer）。"""
        c, S = client
        r = c.get("/health")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert r.headers.get("Referrer-Policy") == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers.get("Content-Security-Policy", "")


class TestRadarTraversal:
    def test_radar_allowlist_blocks_traversal(self, client):
        """上传/注入 SEC-004：雷达图 map_name 必须白名单，拒绝路径分隔与 `..`。"""
        c, S = client
        # 合法地图 → 200
        ok = c.get("/api/maps/de_mirage/radar")
        assert ok.status_code == 200
        # 穿越/非法名 → 404（不再拼路径读取任意 .png）
        for bad in ("../config/radars/de_mirage", "..%2f..%2fetc", "de_mirage/..", "/etc/passwd", ""):
            r = c.get(f"/api/maps/{bad}/radar")
            assert r.status_code in (404, 422), f"{bad} -> {r.status_code}"


class TestUploadMagic:
    def test_upload_rejects_7_byte_magic_without_null(self, client):
        """上传 SEC-003：魔数必须 8 字节 PBDEMS2\\x00；`PBDEMS2`+垃圾即 422。"""
        c, S = client
        token = _reg(c, "up@test.com")
        r = c.post("/api/demos/upload", headers=_auth(token),
                   files={"file": ("x.dem", b"PBDEMS2GARBAGEDATA_JUNK_1234567890", "application/octet-stream")})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "FILE_CONTENT_INVALID"


class TestRateLimit:
    """B.8 / 6R19B：应用层限流——原子计数不绕过、可信 proxy/IP、Redis 故障受限 fallback。"""

    @staticmethod
    def _fake_redis(limit_window=60):
        """线程安全、语义对齐 Lua INCR+EXPIRE 的假 Redis（atomic via lock）。"""
        import threading as _t
        import time as _time

        class FakeRedis:
            def __init__(self):
                self.data = {}
                self.exp = {}
                self.lock = _t.Lock()
            def ping(self):
                return True
            def eval(self, script, numkeys, key, window):
                with self.lock:
                    now = _time.time()
                    c = self.data.get(key, 0) + 1
                    if c == 1:
                        self.exp[key] = now + int(window)
                    self.data[key] = c
                    ttl = max(0, int(self.exp[key] - now))
                    return [c, ttl]

        return FakeRedis()

    @staticmethod
    def _req(host="1.2.3.4", headers=None):
        import types
        return types.SimpleNamespace(client=types.SimpleNamespace(host=host),
                                     headers=headers or {})

    def _enable(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "rate_limit_enabled", True)

    def test_login_throttled_after_limit(self, client, monkeypatch):
        """顺序：超过 limit 次 → 429。"""
        import app.services.rate_limit as rl
        from app.services.rate_limit import RATE_LIMIT_EXCEEDED, rate_limit
        self._enable(monkeypatch)
        fake = self._fake_redis()
        monkeypatch.setattr(rl, "_redis", lambda: fake)
        monkeypatch.setenv("RATE_LIMIT_LOGIN", "3")
        dep = rate_limit("login")
        req = self._req()
        dep(req); dep(req); dep(req)   # 1~3 放行
        with pytest.raises(RATE_LIMIT_EXCEEDED):
            dep(req)                    # 第 4 次超限

    def test_concurrent_first_requests_no_bypass(self, client, monkeypatch):
        """并发首请求：原子 INCR+EXPIRE，N 个并发不能全部通过（恰好 limit 个通过）。"""
        import threading
        import app.services.rate_limit as rl
        from app.services.rate_limit import RATE_LIMIT_EXCEEDED, rate_limit
        self._enable(monkeypatch)
        limit = 10
        fake = self._fake_redis()
        monkeypatch.setattr(rl, "_redis", lambda: fake)
        monkeypatch.setenv("RATE_LIMIT_LOGIN", str(limit))
        dep = rate_limit("login")
        req = self._req(host="9.9.9.9")
        results = []
        lock = threading.Lock()

        def hit():
            try:
                dep(req)
                ok = "pass"
            except RATE_LIMIT_EXCEEDED:
                ok = "throttled"
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=hit) for _ in range(limit + 5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        passed = results.count("pass")
        assert passed == limit, f"恰好 {limit} 个并发通过，实际 {passed}（原子计数无旁路）"

    def test_forged_xforwarded_ignored_from_untrusted_peer(self, client, monkeypatch):
        """直连/不可信对端为 socket IP，忽略伪造 X-Forwarded-For（不能绕出自己桶）。"""
        import app.services.rate_limit as rl
        from app.config import settings
        from app.services.rate_limit import _client_ip
        monkeypatch.setattr(settings, "rate_limit_trusted_proxies", "127.0.0.1")
        # 对端 127.0.0.1 可信 → 取 XFF 最右非信任 IP（真实客户端）
        req = self._req("127.0.0.1", {"x-forwarded-for": "9.9.9.9, 10.0.0.1", "x-real-ip": "10.0.0.1"})
        assert _client_ip(req, ["127.0.0.1"]) == "10.0.0.1"
        # 对端 9.9.9.9 不可信（直连）→ 用 socket IP，忽略伪造 XFF
        req2 = self._req("9.9.9.9", {"x-forwarded-for": "1.1.1.1", "x-real-ip": "1.1.1.1"})
        assert _client_ip(req2, ["127.0.0.1"]) == "9.9.9.9"

    def test_outage_fallback_still_throttles(self, client, monkeypatch):
        """Redis 不可用（_redis 返回 None）→ 进程内受限 fallback 仍限流，非纯放行。"""
        import app.services.rate_limit as rl
        from app.services.rate_limit import RATE_LIMIT_EXCEEDED, rate_limit
        self._enable(monkeypatch)
        monkeypatch.setattr(rl, "_redis", lambda: None)   # Redis 故障（outage）
        monkeypatch.setenv("RATE_LIMIT_LOGIN", "3")
        dep = rate_limit("login")
        req = self._req(host="5.5.5.5")
        dep(req); dep(req); dep(req)   # 窗口内 3 次放行
        with pytest.raises(RATE_LIMIT_EXCEEDED):
            dep(req)                     # 第 4 次受限（fallback 生效，非放行）


class TestAiReportUserFilter:
    def test_ai_report_scoped_to_owner(self, client):
        """BOLA SEC-001：AIReport 读取以 demo 归属 + 账号级 user_id 双因子收口；
        非属主访问他人 demo 的 AI 报告 → 404（不泄露存在性）。"""
        c, S = client
        from sqlalchemy import select
        from app.models import Demo
        token_a = _reg(c, "ownera@test.com")
        uid_a = c.get("/api/auth/me", headers=_auth(token_a)).json()["id"]
        with S() as db:
            demo = Demo(user_id=uid_a, file_name="d.dem", file_path="demos/x.dem", status="parsed")
            db.add(demo)
            db.flush()
            did = demo.id
            db.commit()
        # 属主本人（无报告）→ 200 空列表（用户过滤查询正常）
        r = c.get(f"/api/demos/{did}/ai-report", headers=_auth(token_a))
        assert r.status_code == 200, r.text
        # 跨用户 B 访问 A 的 demo 报告 → 404
        token_b = _reg(c, "otherb@test.com")
        r2 = c.get(f"/api/demos/{did}/ai-report", headers=_auth(token_b))
        assert r2.status_code == 404
        assert r2.json()["detail"]["code"] == "DEMO_NOT_FOUND"
