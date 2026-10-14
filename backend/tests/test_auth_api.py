"""Phase 3 认证测试：注册/登录/me、重复邮箱、错误密码、过期/非法 token。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.security import create_access_token


@pytest.fixture
def client():
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _register(client, email="a@test.com", password="password123", nickname="玩家A"):
    return client.post("/api/auth/register", json={"email": email, "password": password, "nickname": nickname})


def test_register_ok(client):
    resp = _register(client)
    assert resp.status_code == 200
    body = resp.json()
    # P0-1：注册直接返回与 login 一致的 TokenResponse（计划书第六节）
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20
    assert body["user"]["email"] == "a@test.com"
    assert body["user"]["subscription_tier"] == "free"
    assert "password" not in body


def test_register_token_can_be_used_immediately(client):
    resp = _register(client, email="token@test.com")
    token = resp.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "token@test.com"


def test_access_token_tolerates_two_second_issued_at_skew_without_extending_expiry(client):
    """A tiny worker-clock correction must not log out a newly signed-in user."""
    import jwt
    from datetime import datetime, timedelta, timezone
    from app.config import settings

    user = _register(client, email="skew@test.com").json()["user"]
    now = datetime.now(timezone.utc)

    def signed(iat, exp):
        return jwt.encode(
            {"sub": str(user["id"]), "typ": "access", "iat": iat, "exp": exp},
            settings.jwt_secret, algorithm="HS256",
        )

    near = signed(now + timedelta(seconds=1), now + timedelta(hours=1))
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {near}"}).status_code == 200

    far = signed(now + timedelta(seconds=10), now + timedelta(hours=1))
    rejected = client.get("/api/auth/me", headers={"Authorization": f"Bearer {far}"})
    assert rejected.status_code == 401
    assert rejected.json()["detail"]["code"] == "TOKEN_INVALID"

    expired = signed(now + timedelta(seconds=1), now - timedelta(seconds=1))
    rejected = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert rejected.status_code == 401
    assert rejected.json()["detail"]["code"] == "TOKEN_EXPIRED"


def test_register_chinese_password_72_bytes_ok(client):
    # “密”=3 字节 × 24 = 72 字节（bcrypt 上限边界，可接受）
    pw = "密" * 24
    resp = _register(client, email="cn72@test.com", password=pw, nickname="中文")
    assert resp.status_code == 200
    # 且可用中文密码登录（verify 安全处理）
    login = client.post("/api/auth/login", json={"email": "cn72@test.com", "password": pw})
    assert login.status_code == 200


def test_register_password_73_bytes_422(client):
    # “密”×25 = 75 字节 > 72：稳定 422，绝不 500（P0-2）
    resp = _register(client, email="cn73@test.com", password="密" * 25, nickname="中文")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_register_emoji_72_bytes_ok(client):
    # emoji = 4 字节 × 18 = 72 字节（边界可接受）
    pw = "😀" * 18
    resp = _register(client, email="emoji@test.com", password=pw, nickname="emoji")
    assert resp.status_code == 200
    login = client.post("/api/auth/login", json={"email": "emoji@test.com", "password": pw})
    assert login.status_code == 200


def test_register_emoji_73_bytes_422(client):
    # emoji × 19 = 76 字节 > 72：稳定 422
    resp = _register(client, email="emoji2@test.com", password="😀" * 19, nickname="emoji")
    assert resp.status_code == 422


def test_login_oversize_password_no_500(client):
    # 登录不经过 Pydantic 字节校验：verify_password 必须安全返回 False（不 500）
    _register(client, email="safe@test.com")
    resp = client.post("/api/auth/login", json={"email": "safe@test.com", "password": "密" * 30})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_register_duplicate_email(client):
    assert _register(client).status_code == 200
    resp = _register(client)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "EMAIL_EXISTS"


def test_register_short_password(client):
    resp = _register(client, password="123")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_register_invalid_email(client):
    resp = _register(client, email="not-an-email")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_login_ok(client):
    _register(client, email="login@test.com")
    resp = client.post("/api/auth/login", json={"email": "login@test.com", "password": "password123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20
    assert body["user"]["email"] == "login@test.com"


def test_login_wrong_password(client):
    _register(client, email="wrong@test.com")
    resp = client.post("/api/auth/login", json={"email": "wrong@test.com", "password": "wrong-pass"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_login_unknown_email(client):
    resp = client.post("/api/auth/login", json={"email": "ghost@test.com", "password": "password123"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_me_ok(client):
    _register(client, email="me@test.com")
    token = client.post("/api/auth/login", json={"email": "me@test.com", "password": "password123"}).json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@test.com"
    assert resp.json()["subscription_tier"] == "free"


def test_me_invalid_token(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "TOKEN_INVALID"


def test_me_no_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_REQUIRED"


def test_me_expired_token(client):
    # 用负数过期时间签发一个立即过期的 token
    token = create_access_token_expired()
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "TOKEN_EXPIRED"


def create_access_token_expired():
    """签发已过期 token（jwt 直接构造，exp 在过去）。"""
    import jwt
    from app.config import settings
    from datetime import datetime, timedelta, timezone

    payload = {
        "sub": "1",
        "email": "x@test.com",
        "iat": datetime.now(timezone.utc) - timedelta(days=8),
        "exp": datetime.now(timezone.utc) - timedelta(days=1),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def test_subscription_expire_utc_rfc3339():
    """P1-1：后端统一输出 UTC RFC3339（带 Z），naive 历史数据按 UTC 解释。"""
    from datetime import datetime, timezone

    from app.schemas.auth import UserOut, utc_rfc3339

    # naive（历史数据，无时区）→ 按 UTC 输出带 Z
    naive = datetime(2026, 8, 20, 12, 0, 0)
    assert utc_rfc3339(naive) == "2026-08-20T12:00:00Z"

    # aware → 转 UTC 后带 Z
    aware = datetime(2026, 8, 20, 20, 0, 0, tzinfo=timezone.utc)
    assert utc_rfc3339(aware) == "2026-08-20T20:00:00Z"

    # None → None
    assert utc_rfc3339(None) is None

    # UserOut 序列化
    out = UserOut(id=1, email="a@b.com", nickname="x", platform="", rank="",
                  subscription_tier="pro", subscription_expire=naive)
    data = out.model_dump(mode="json")
    assert data["subscription_expire"] == "2026-08-20T12:00:00Z"
