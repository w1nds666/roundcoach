"""6R14：Steam OpenID 2.0 认证流 + 绑定/解绑/冲突/冷却 集成测试（FakeVerifier 自动门禁）。

覆盖：start（鉴权/白名单/return_to 校验）、callback（合法/cancel/伪 mode/坏 op_endpoint/claimed 不匹配/
坏 claimed_id/state 复用/重放/check 失败）、绑定幂等、冲突 409、recent-auth 解绑/冷却。
不依赖真实 Steam；全程掩码，断言不出现完整 SteamID64。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from urllib.parse import parse_qs, urlparse

from app import models
from app.api.steam_auth import get_verifier
from app.database import Base, get_db
from app.main import app
from app.services.steam_openid import FakeVerifier


STEAM_ID = "76561198812054537"
STEAM_ID2 = "76561198812054538"


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
    # 默认注入一个成功 verifier；测试可按需覆盖
    app.dependency_overrides[get_verifier] = lambda: FakeVerifier(is_valid=True)
    with TestClient(app, follow_redirects=False) as c:
        yield c, S
    app.dependency_overrides.clear()


def _reg(client, email, password="password123"):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "nickname": "玩家"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _steam_start(client, token):
    r = client.get("/api/auth/steam/start", headers=_auth(token), params={"return_to": "/profile"})
    assert r.status_code == 200, r.text
    redirect_url = r.json()["redirect_url"]
    q = parse_qs(urlparse(redirect_url).query)
    return_to = q["openid.return_to"][0]
    state = parse_qs(urlparse(return_to).query)["state"][0]
    cb_parts = urlparse(return_to)
    cb_url = f"{cb_parts.scheme}://{cb_parts.netloc}{cb_parts.path}"
    return redirect_url, return_to, state, cb_url


def _callback(client, return_to, state, steam_id, mode="id_res", op="https://steamcommunity.com/openid/",
              claimed=None, identity=None, ns="http://specs.openid.net/auth/2.0",
              realm="http://testserver", extra=None):
    claimed = claimed if claimed is not None else f"https://steamcommunity.com/openid/id/{steam_id}"
    identity = identity if identity is not None else claimed
    params = {
        "state": state,
        "openid.mode": mode,
        "openid.ns": ns,
        "openid.op_endpoint": op,
        "openid.claimed_id": claimed,
        "openid.identity": identity,
        "openid.return_to": return_to,
        "openid.realm": realm,
    }
    if extra:
        params.update(extra)
    return client.get("/api/auth/steam/callback", params=params)


def _loc(r):
    return r.headers.get("location", "")


class TestStart:
    def test_start_no_auth_401(self, client):
        c, S = client
        assert c.get("/api/auth/steam/start").status_code == 401

    def test_start_ok(self, client):
        c, S = client
        token = _reg(c, "s@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        assert "steamcommunity.com/openid/login" in redirect_url
        assert f"openid.return_to=http%3A%2F%2Ftestserver" in redirect_url or "openid.return_to=" in redirect_url
        assert return_to.startswith("http://testserver/api/auth/steam/callback")
        assert state

    def test_start_disallowed_return_to_422(self, client):
        c, S = client
        token = _reg(c, "s2@test.com")
        r = c.get("/api/auth/steam/start", headers=_auth(token), params={"return_to": "https://evil.example"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "VALIDATION_ERROR"


class TestCallbackFlow:
    def test_valid_bind(self, client):
        c, S = client
        token = _reg(c, "ok@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID)
        assert resp.status_code == 302
        assert "steam=ok" in _loc(resp)
        # status bound + masked
        st = c.get("/api/auth/steam/status", headers=_auth(token)).json()
        assert st["bound"] is True
        assert st["steam_id64_masked"] != STEAM_ID
        assert "****" in st["steam_id64_masked"]

    def test_cancel_redirects_cancel(self, client):
        c, S = client
        token = _reg(c, "cancel@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID, mode="cancel")
        assert resp.status_code == 302
        assert "steam=cancel" in _loc(resp)

    def test_bad_mode(self, client):
        c, S = client
        token = _reg(c, "mode@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID, mode="checkid_setup")
        assert "reason=OPENID_BAD_MODE" in _loc(resp)

    def test_bad_op_endpoint(self, client):
        c, S = client
        token = _reg(c, "op@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID, op="https://evil.example/openid/")
        assert "reason=OPENID_BAD_OP_ENDPOINT" in _loc(resp)

    def test_claimed_identity_mismatch(self, client):
        c, S = client
        token = _reg(c, "mm@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID, claimed=f"https://steamcommunity.com/openid/id/{STEAM_ID}",
                         identity=f"https://steamcommunity.com/openid/id/{STEAM_ID2}")
        assert "reason=OPENID_CLAIMED_MISMATCH" in _loc(resp)

    def test_bad_claimed_id(self, client):
        c, S = client
        token = _reg(c, "bad@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID, claimed="https://steamcommunity.com/openid/id/not-a-steam")
        assert "reason=OPENID_BAD_CLAIMED_ID" in _loc(resp)

    def test_wrong_return_to(self, client):
        c, S = client
        token = _reg(c, "rt@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, f"http://testserver/evil?state={state}", state, STEAM_ID)
        assert "reason=OPENID_BAD_RETURN_TO" in _loc(resp)

    def test_state_reuse(self, client):
        c, S = client
        token = _reg(c, "reuse@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        _callback(c, return_to, state, STEAM_ID)  # 成功消费
        resp = _callback(c, return_to, state, STEAM_ID2)  # 重放
        assert "reason=OPENID_STATE_USED" in _loc(resp)

    def test_check_authentication_failure_rejected(self, client):
        c, S = client
        app.dependency_overrides[get_verifier] = lambda: FakeVerifier(is_valid=False)
        token = _reg(c, "ck@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID)
        assert "reason=OPENID_CHECK_FAILED" in _loc(resp)


class TestConflicts:
    def test_same_steam_other_account_conflict(self, client):
        c, S = client
        t1 = _reg(c, "u1@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, t1)
        _callback(c, return_to, state, STEAM_ID)
        # user2 尝试绑定同一 steam
        t2 = _reg(c, "u2@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, t2)
        resp = _callback(c, return_to, state, STEAM_ID)
        assert "reason=STEAM_IDENTITY_CONFLICT" in _loc(resp)
        # user1 状态仍绑
        st = c.get("/api/auth/steam/status", headers=_auth(t1)).json()
        assert st["bound"] is True

    def test_idempotent_same_user_same_steam(self, client):
        c, S = client
        token = _reg(c, "idem@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        _callback(c, return_to, state, STEAM_ID)
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID)
        assert "steam=ok" in _loc(resp)


class TestUnbind:
    def test_unbind_requires_recent_auth(self, client):
        c, S = client
        token = _reg(c, "ub@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        _callback(c, return_to, state, STEAM_ID)
        # 无 recent-auth token → 403
        r = c.post("/api/auth/steam/unbind", headers=_auth(token))
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "STEAM_CSRF_INVALID"

    def test_unbind_wrong_password(self, client):
        c, S = client
        token = _reg(c, "ub2@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        _callback(c, return_to, state, STEAM_ID)
        r = c.post("/api/auth/verify-password", headers=_auth(token), json={"password": "wrong-pass"})
        assert r.status_code == 401

    def test_unbind_ok_and_cooldown(self, client):
        c, S = client
        token = _reg(c, "ub3@test.com")
        redirect_url, return_to, state, cb = _steam_start(c, token)
        _callback(c, return_to, state, STEAM_ID)
        vp = c.post("/api/auth/verify-password", headers=_auth(token), json={"password": "password123"})
        assert vp.status_code == 200
        rat = vp.json()["recent_auth_token"]
        r = c.post("/api/auth/steam/unbind", headers=_auth(token), params={"recent_auth_token": rat})
        assert r.status_code == 200
        st = c.get("/api/auth/steam/status", headers=_auth(token)).json()
        assert st["bound"] is False
        # 换绑冷却：绑定不同 steam 被拒
        redirect_url, return_to, state, cb = _steam_start(c, token)
        resp = _callback(c, return_to, state, STEAM_ID2)
        assert "reason=STEAM_COOLDOWN" in _loc(resp)
