"""Steam OpenID 2.0 认证与绑定/解绑 API（6R14）。

- `/api/auth/steam/start`：为已登录用户生成一次性 state 并对 Steam 跳转（返回 redirect_url）。
- `/api/auth/steam/callback`：OpenID 回调，严格校验 + check_authentication，绑定，回跳 return_to。
- `/api/auth/steam/status`：只读绑定状态（掩码）。
- `/api/auth/verify-password`：recent-auth（密码再认证）返回短 TTL token（解绑/换绑 CSRF 凭证）。
- `/api/auth/steam/unbind`：最近 15 分钟密码再认证 + CSRF token 后解绑；权益暂停、审计、冷却。

永不输出完整 SteamID64；callback 参数/token/cookie 不落日志。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.errors import ApiError, RECENT_AUTH_REQUIRED, STEAM_CSRF_INVALID
from app.models import OpenIdState, User
from app.services import identity as identity_service
from app.services.rate_limit import rate_limit
from app.services.security import (
    create_recent_auth_token,
    decode_recent_auth_token,
    verify_password,
)
from app.services.steam_openid import (
    R_BAD_REALM,
    R_BAD_RETURN_TO,
    R_CANCEL,
    R_STATE_EXPIRED,
    R_STATE_USED,
    RealHttpVerifier,
    build_auth_url,
    new_state,
    validate_callback,
)
from app.services.steamid import mask_steamid64

router = APIRouter(prefix="/api/auth/steam", tags=["steam"])
recent_router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_verifier():
    """check_authentication verifier。

    默认真实实现；`STEAM_OPENID_USE_FAKE_VERIFIER=true`（本地/E2E 自动门禁）时用 FakeVerifier，
    不访问真实 Steam。测试可依赖注入 `app.dependency_overrides[get_verifier]`。
    """
    if settings.steam_openid_use_fake_verifier:
        from app.services.steam_openid import FakeVerifier

        return FakeVerifier(is_valid=True)
    return RealHttpVerifier()


def _req_origin(request: Request) -> tuple[str, str]:
    scheme = request.url.scheme
    netloc = request.url.netloc
    return scheme, netloc


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/start")
def steam_start(
    request: Request,
    return_to: str = Query(default="/profile", max_length=512),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("steam_start")),
):
    """生成一次性 state 并返回对 Steam 的跳转 URL。仅对已登录站内用户。"""
    scheme, netloc = _req_origin(request)
    from app.services.steam_openid import assert_host_allowed

    assert_host_allowed(scheme, netloc)  # 白名单 + 生产 HTTPS 检查
    # 前端回跳：只允许同源相对路径
    if not return_to.startswith("/") or "://" in return_to or return_to.startswith("//"):
        raise ApiError(422, "VALIDATION_ERROR", "return_to 必须是同源相对路径")
    callback_url = f"{scheme}://{netloc}{settings.steam_openid_callback_path}"
    realm = f"{scheme}://{netloc}"
    state_value = new_state()
    db.add(OpenIdState(state=state_value, user_id=current_user.id, realm=realm, return_to=return_to,
                       callback_url=callback_url,
                       expires_at=_now() + __import__("datetime").timedelta(seconds=settings.steam_openid_state_ttl_seconds)))
    db.commit()
    return {"redirect_url": build_auth_url(callback_url, realm, state_value)}


@router.get("/callback")
def steam_callback(
    request: Request,
    db: Session = Depends(get_db),
    verifier=Depends(get_verifier),
    _rl: None = Depends(rate_limit("steam_callback")),
):
    """OpenID 回调：校验并绑定后回跳 return_to。

    全部参数（含带「.」的 openid.* 键）从 request.query_params 捕获，随后严格校验。
    """
    params: dict[str, str] = {key: value for key, value in request.query_params.items()}

    state_row = db.scalar(select(OpenIdState).where(OpenIdState.state == params.get("state", "")))
    # cancel 统一处理（不当作 error 记审计攻击）
    if params.get("openid.mode") == "cancel":
        if state_row is not None:
            state_row.used = True
            db.commit()
        return _redirect_result(state_row, outcome="cancel")
    if state_row is None:
        return _redirect_result(None, outcome="error", reason="OPENID_STATE_INVALID")

    result = validate_callback(params, state_row, verifier)
    # 6R19：原子消费一次性 state（条件 used=false；Update 单条，DB 原子）——
    # 即使两个并发 callback 同时通过上一步的 used 读检查，也只有一个能消费成功，
    # 另一条因 rowcount==0 被判为重放（闭合并发双回调 TOCTOU）。
    consumed = db.execute(
        update(OpenIdState)
        .where(OpenIdState.state == state_row.state, OpenIdState.used.is_(False))
        .values(used=True, response_nonce=result.response_nonce or state_row.response_nonce)
    )
    if consumed.rowcount == 0:
        db.rollback()
        return _redirect_result(state_row, outcome="error", reason="OPENID_STATE_USED")
    db.commit()

    if result.ok:
        user = db.get(User, state_row.user_id)
        if user is None:
            return _redirect_result(state_row, outcome="error", reason="OPENID_STATE_INVALID")
        try:
            identity_service.bind_steam(db, user, result.steam_id64, persona_name=result.persona_name)
            return _redirect_result(state_row, outcome="ok")
        except ApiError as exc:
            return _redirect_result(state_row, outcome="error", reason=exc.code)
    # 失败
    outcome = "error"
    redirect_path = state_row.return_to
    return _redirect_result(state_row, outcome=outcome, reason=result.reason_code, forced_path=redirect_path)


def _redirect_result(state_row, outcome: str, reason: str | None = None, forced_path: str | None = None):
    target = (forced_path or (state_row.return_to if state_row else "/")) or "/profile"
    from urllib.parse import urlencode

    qs = urlencode({"steam": outcome, **({"reason": reason} if reason else {})})
    return RedirectResponse(url=f"{target}?{qs}", status_code=302)


@recent_router.post("/verify-password")
def verify_password_endpoint(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """recent-auth：校验密码并把 last_password_auth_at 置为当前；返回短 TTL token（解绑/换绑 CSRF 凭证）。"""
    password = str(body.get("password") or "")
    if not verify_password(password, current_user.password_hash):
        raise ApiError(401, "INVALID_CREDENTIALS", "密码错误")
    current_user.last_password_auth_at = _now()
    db.commit()
    return {"recent_auth_token": create_recent_auth_token(current_user.id), "expires_in": settings.steam_openid_recent_auth_seconds}


@router.get("/status")
def steam_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return identity_service.identity_status(db, current_user)


@router.post("/unbind")
def steam_unbind(
    recent_auth_token: str = Query(default="", description="recent-auth token（CSRF 凭证）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """解绑（安全敏感）：需最近 15 分钟密码再认证 token（兼作 CSRF 证明）。权益/画像暂停而非删除；审计；冷却。"""
    if not recent_auth_token:
        raise ApiError(403, STEAM_CSRF_INVALID, "缺少 recent-auth 凭证，请先重新验证密码")
    try:
        payload = decode_recent_auth_token(recent_auth_token)
    except Exception:  # noqa: BLE001
        raise ApiError(403, STEAM_CSRF_INVALID, "recent-auth 凭证无效或已过期")
    if int(payload.get("sub", -1)) != current_user.id:
        raise ApiError(403, STEAM_CSRF_INVALID, "recent-auth 凭证与登录账号不匹配")
    # 最近 15 分钟密码再认证窗口
    if current_user.last_password_auth_at is None or \
            (datetime.now(timezone.utc).replace(tzinfo=None) - current_user.last_password_auth_at) > \
            __import__("datetime").timedelta(seconds=settings.steam_openid_recent_auth_seconds):
        raise ApiError(403, RECENT_AUTH_REQUIRED, "需要最近 15 分钟内重新验证密码后再解绑")
    identity_service.unbind_steam(db, current_user)
    return {"unbound": True}
