"""FastAPI 依赖：当前登录用户（Bearer JWT）。"""
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import (
    AUTH_REQUIRED,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
    ApiError,
)
from app.models import User
from app.services.security import decode_access_token


def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise ApiError(401, AUTH_REQUIRED, "请先登录")
    return auth[7:].strip()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """解析 JWT 并返回当前用户；过期/非法 token 返回稳定错误码。"""
    token = _extract_token(request)
    try:
        payload = decode_access_token(token)
    except Exception as exc:  # noqa: BLE001
        cls = type(exc).__name__
        if cls == "ExpiredSignatureError":
            raise ApiError(401, TOKEN_EXPIRED, "登录已过期，请重新登录") from exc
        raise ApiError(401, TOKEN_INVALID, "登录凭证无效，请重新登录") from exc

    # 6R19：sub 必须是合法非负整数，否则返回稳定 401（避免 int() 抛错导致 500）。
    try:
        sub = int(payload.get("sub", 0))
        if sub <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ApiError(401, TOKEN_INVALID, "登录凭证无效，请重新登录") from None

    user = db.get(User, sub)
    if user is None:
        raise ApiError(401, TOKEN_INVALID, "登录凭证无效，请重新登录")
    return user
