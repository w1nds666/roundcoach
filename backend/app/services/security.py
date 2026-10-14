"""安全工具：bcrypt 密码哈希 + JWT（计划书第十二节第 6 条安全底线、Phase 3 用户系统）。"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """bcrypt 哈希，返回 str 以便入库。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码与 bcrypt 哈希。

    安全处理：bcrypt 对 >72 字节或非法输入会抛 ValueError/TypeError，
    一律返回 False（不泄露内部异常，也不允许 500）。
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, email: str) -> str:
    """签发 JWT（HS256，含过期时间）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """解码 JWT。抛出 jwt.ExpiredSignatureError（过期）或 jwt.InvalidTokenError（非法）。

    6R19：显式要求 `typ == "access"` 且拒绝 `scope == "recent_auth"`，防止 recent_auth
    这种短 TTL 凭证被当作完整 access token 用于任意需登录接口（token 类型混淆）。
    """
    # A host clock correction can put a freshly signed token fractionally ahead of
    # another worker. Tolerate at most two seconds on *iat only*: expiry and nbf
    # keep PyJWT's strict defaults, so this does not extend an expired session.
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[ALGORITHM],
        options={"verify_iat": False},
    )
    if "iat" in payload:
        try:
            issued_at = int(payload["iat"])
        except (TypeError, ValueError) as exc:
            raise jwt.InvalidIssuedAtError("Issued At claim (iat) must be an integer") from exc
        if issued_at > datetime.now(timezone.utc).timestamp() + 2:
            raise jwt.ImmatureSignatureError("The token is not yet valid (iat)")
    if payload.get("scope") == "recent_auth":
        raise jwt.InvalidTokenError("recent_auth token 不能作为 access token 使用")
    if payload.get("typ") is not None and payload.get("typ") != "access":
        raise jwt.InvalidTokenError("token 类型不是 access")
    return payload


def create_recent_auth_token(user_id: int) -> str:
    """recent-auth 令牌（6R14 解绑/换绑）：短 TTL（recent_auth_seconds），scope=recent_auth。

    同时充当 CSRF 证明：必须由调用方显式携带（放在自定义 header），跨站表单/请求不会自动带上。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "typ": "recent_auth",
        "scope": "recent_auth",
        "iat": now,
        "exp": now + timedelta(seconds=settings.steam_openid_recent_auth_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_recent_auth_token(token: str) -> dict:
    """解码 recent-auth token；scope 必须为 recent_auth，否则抛 InvalidTokenError。"""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    if payload.get("scope") != "recent_auth":
        raise jwt.InvalidTokenError("scope 不是 recent_auth")
    return payload
