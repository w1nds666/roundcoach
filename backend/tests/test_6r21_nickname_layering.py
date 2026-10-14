"""6R21：local nickname 与 Steam persona 分层持久化。

核心断言：注册用户本地昵称（如 boss）在绑定 / 解绑 / 换绑 / 幂等重绑定全过程**保持不变**；
Steam persona 名独立存于 steam_identities.persona_name（只读徽标），绝不写 users.nickname。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import SteamIdentity, User
from app.services import identity
from app.services.security import hash_password

STEAM_ME = "76561198812054537"
PERSONA = "MVR验收"


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)()


def _mk_user(db, nickname="boss"):
    u = User(email="boss@test.com", password_hash=hash_password("password123"), nickname=nickname,
             subscription_tier="pro",
             subscription_expire=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30))
    db.add(u)
    db.commit()
    return u


def test_bind_steam_preserves_local_nickname_and_stores_persona():
    db = _session()
    u = _mk_user(db, nickname="boss")
    identity.bind_steam(db, u, STEAM_ME, persona_name=PERSONA)
    db.refresh(u)
    ident = identity.get_active_identity(db, u)
    assert u.nickname == "boss"                      # 本地昵称未被 Steam persona 覆盖
    assert ident is not None and ident.persona_name == PERSONA
    st = identity.identity_status(db, u)
    assert st["bound"] is True and st["persona_name"] == PERSONA
    db.close()


def test_unbind_preserves_local_nickname():
    db = _session()
    u = _mk_user(db, nickname="boss")
    identity.bind_steam(db, u, STEAM_ME, persona_name=PERSONA)
    identity.unbind_steam(db, u)
    db.refresh(u)
    assert u.nickname == "boss"                      # 解绑后本地昵称 boss 仍保留
    # 历史行保留 persona（只读徽标信息不清空），但当前无 active 绑定
    assert identity.get_active_identity(db, u) is None
    st = identity.identity_status(db, u)
    assert st["bound"] is False and st["persona_name"] is None
    db.close()


def test_idempotent_rebind_and_persona_never_touches_nickname():
    db = _session()
    u = _mk_user(db, nickname="boss")
    identity.bind_steam(db, u, STEAM_ME, persona_name="MVR验收")
    identity.bind_steam(db, u, STEAM_ME, persona_name="MVR验收")   # 幂等重绑
    db.refresh(u)
    assert u.nickname == "boss"
    assert identity.get_active_identity(db, u).persona_name == "MVR验收"
    db.close()


def test_bind_without_persona_still_preserves_local_nickname():
    db = _session()
    u = _mk_user(db, nickname="boss")
    identity.bind_steam(db, u, STEAM_ME)             # 未提供 persona
    db.refresh(u)
    assert u.nickname == "boss"
    assert identity.get_active_identity(db, u).persona_name is None
    db.close()


def test_validate_callback_carries_sreg_persona():
    """Steam OpenID 回带的 openid.sreg.nickname 被提取为 persona_name（并非用户昵称）。"""
    from app.services.steam_openid import FakeVerifier, validate_callback

    class _StateRow:
        state = "state123"
        used = False
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
        callback_url = "http://localhost/api/auth/steam/callback"
        realm = "http://localhost"
        response_nonce = None

    params = {
        "openid.mode": "id_res",
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.op_endpoint": "https://steamcommunity.com/openid/",
        "openid.claimed_id": f"https://steamcommunity.com/openid/id/{STEAM_ME}",
        "openid.identity": f"https://steamcommunity.com/openid/id/{STEAM_ME}",
        "openid.return_to": "http://localhost/api/auth/steam/callback?state=state123",
        "openid.realm": "http://localhost",
        "openid.response_nonce": "nonce-1",
        "openid.sreg.nickname": PERSONA,
        "state": "state123",
    }
    res = validate_callback(params, _StateRow(), FakeVerifier(is_valid=True))
    assert res.ok is True
    assert res.persona_name == PERSONA
    assert res.steam_id64 == STEAM_ME
