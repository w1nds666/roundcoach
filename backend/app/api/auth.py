"""认证 API（Phase 3：注册/登录/JWT/me，bcrypt + 稳定错误码）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.errors import EMAIL_EXISTS, INVALID_CREDENTIALS, ApiError
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services.rate_limit import rate_limit
from app.services.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("register")),
):
    """注册：邮箱唯一 + bcrypt 密码哈希，成功直接返回 JWT（与 login 一致，计划书第六节）。"""
    exists = db.scalar(select(User).where(User.email == body.email))
    if exists:
        raise ApiError(409, EMAIL_EXISTS, "该邮箱已注册")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        nickname=body.nickname,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("login")),
):
    """登录：返回 JWT（默认 7 天有效）。"""
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(body.password, user.password_hash):
        raise ApiError(401, INVALID_CREDENTIALS, "邮箱或密码错误")

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    """当前用户信息 + 订阅状态。"""
    return current_user
