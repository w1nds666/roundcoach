"""身份服务（6R14）：Steam 绑定/解绑/换绑、冲突、冷却、审计、自动识别的「我」与个人 Pro 门禁。

信任边界：身份只来源于服务端 OpenID check_authentication 结果（steamid.py 规范化后存储）；
前端/USER/URL/API 参数都不能决定身份。个人 Pro/Max 付费内容只对「绑定 SteamID == demo 该 Player。steam_id」
开放；DemoFocusPlayer 保留为免费探索/兼容，但不得覆盖绑定身份。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import (
    STEAM_COOLDOWN,
    STEAM_IDENTITY_CONFLICT,
    STEAM_IDENTITY_NOT_IN_DEMO,
    STEAM_IDENTITY_REQUIRED,
    ApiError,
)
from app.models import Demo, IdentityAudit, Player, SteamIdentity, User
from app.services.steamid import mask_steamid64


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_audit(db: Session, user_id: int | None, steam_id64: str | None, action: str, detail: str | None = None) -> None:
    db.add(IdentityAudit(user_id=user_id, steam_id64=steam_id64, action=action, detail=detail))


def get_active_identity(db: Session, user: User) -> SteamIdentity | None:
    """当前用户 активactive Steam 绑定。"""
    if user is None:
        return None
    return db.scalar(select(SteamIdentity).where(SteamIdentity.user_id == user.id, SteamIdentity.status == "active"))


def active_identity_for(db: Session, steam_id64: str) -> SteamIdentity | None:
    """某 Steam 当前活跃绑定（跨用户）。"""
    return db.scalar(select(SteamIdentity).where(SteamIdentity.steam_id64 == steam_id64, SteamIdentity.status == "active"))


def identity_status(db: Session, user: User) -> dict:
    """只读绑定状态（掩码 id + 时间 + provider + persona_name；不输出完整 SteamID64）。"""
    ident = get_active_identity(db, user)
    if ident is None:
        return {"bound": False, "provider": None, "steam_id64_masked": None,
                "bound_at": None, "verified_at": None, "last_verified_at": None, "persona_name": None}
    return {
        "bound": True,
        "provider": ident.provider,
        "steam_id64_masked": mask_steamid64(ident.steam_id64),
        "bound_at": ident.bound_at.isoformat() if ident.bound_at else None,
        "verified_at": ident.verified_at.isoformat() if ident.verified_at else None,
        "last_verified_at": ident.last_verified_at.isoformat() if ident.last_verified_at else None,
        "persona_name": ident.persona_name,   # 6R21：只读徽标，与本地昵称分层
    }


def bind_steam(db: Session, user: User, steam_id64: str, persona_name: str | None = None) -> SteamIdentity:
    """绑定 Steam 到当前登录用户。

    - 同一 Steam 已绑其他账号（active 且 user_id != 当前用户）→ 409（不泄露对方信息）。
    - 用户已绑同一 Steam → idempotent（刷新 last_verified_at）。
    - 用户已绑不同的 Steam（换绑）→ 冷却检查后把旧身份置 unbound，再绑新的。
    - 无 active 绑定：若处于换绑冷却期，则 409；否则绑定（首次无冷却）。
    - **persona_name 只写 steam_identities.persona_name（只读徽标），绝不写 users.nickname**——
      本地昵称是用户设置，绑定/解绑/取消/回退都保持不变（6R21 分层持久化）。
    - 全程审计；提交事务。
    """
    now = _now()
    # 1) 该 Steam 当前是否已被其他账号占用
    existing = active_identity_for(db, steam_id64)
    if existing is not None and existing.user_id != user.id:
        _make_audit(db, user.id, steam_id64, "conflict", "steam 已被其他账号绑定")
        db.commit()
        raise ApiError(409, STEAM_IDENTITY_CONFLICT, "该 Steam 账号已绑定其他账号，无法重复绑定")

    # 2) 当前用户已有的 active 绑定
    cur = get_active_identity(db, user)
    if cur is not None:
        if cur.steam_id64 == steam_id64:
            # idempotent：刷新验证时间
            cur.last_verified_at = now
            cur.verified_at = now
            _make_audit(db, user.id, steam_id64, "bind", "重复绑定同一 Steam（幂等刷新）")
            db.commit()
            return cur
        # 换绑：冷却检查
        _check_cooldown(user)
        cur.status = "unbound"
        cur.unbound_at = now
        cur.user_id = None
        _make_audit(db, user.id, cur.steam_id64, "rebind", "换绑：旧 Steam 解绑")
        user.steam_cooldown_until = now + timedelta(days=settings.steam_openid_rebind_cooldown_days)

    # 3) 无 active 绑定：若处于冷却期则拒绝（unbind 后的重绑/换绑）
    if cur is None:
        _check_cooldown(user)

    # 4) 绑定（激活或创建行）
    row = db.scalar(select(SteamIdentity).where(SteamIdentity.steam_id64 == steam_id64))
    if row is None:
        row = SteamIdentity(provider="steam", subject=steam_id64, steam_id64=steam_id64,
                            user_id=user.id, status="active", bound_at=now, verified_at=now,
                            persona_name=persona_name)
        db.add(row)
    else:
        row.status = "active"
        row.user_id = user.id
        row.bound_at = now
        row.verified_at = now
        row.unbound_at = None
        if persona_name:
            row.persona_name = persona_name
        row.version += 1
    _make_audit(db, user.id, steam_id64, "bind", "绑定成功")
    # 6R19：并发绑定同一 Steam 到不同账号时，服务层 SELECT 检查可能都通过，
    # 由 DB 唯一约束（steam_id64 unique）兜底抛 IntegrityError——捕获后转稳定 409，而非 500。
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _make_audit(db, user.id, steam_id64, "conflict", "并发绑定冲突：steam 已被占用")
        db.commit()
        raise ApiError(409, STEAM_IDENTITY_CONFLICT, "该 Steam 账号已绑定其他账号，无法重复绑定") from exc
    db.refresh(row)
    return row


def _check_cooldown(user: User) -> None:
    """换绑/重绑冷却：7 天内拒绝（管理员不得通过前端绕过）。"""
    if user.steam_cooldown_until is not None and user.steam_cooldown_until > _now():
        raise ApiError(409, STEAM_COOLDOWN, "Steam 换绑处于 7 天冷却期，暂不能重新绑定")


def unbind_steam(db: Session, user: User) -> SteamIdentity:
    """解绑（安全敏感）：调用方必须先验证 recent-auth + CSRF；权益/画像暂停而非删除历史；审计。

    - 解绑后 status=unbound、user_id=NULL（历史行保留），设置 7 天换绑冷却。
    - 若已解绑（无 active）→ 幂等返回。
    """
    now = _now()
    cur = get_active_identity(db, user)
    if cur is None:
        # 幂等：无 active 绑定
        return cur
    cur.status = "unbound"
    cur.unbound_at = now
    cur.user_id = None
    cur.version += 1
    user.steam_cooldown_until = now + timedelta(days=settings.steam_openid_rebind_cooldown_days)
    _make_audit(db, user.id, cur.steam_id64, "unbind", "用户主动解绑")
    db.commit()
    db.refresh(cur)
    return cur


def resolve_bound_focus(db: Session, demo: Demo, user: User) -> tuple[str, Player | None]:
    """按绑定 Steam 自动解析 demo 中的「我」。

    返回 (status, player)：
    - unbound：用户未绑定 Steam
    - unparsed：demo 未解析
    - not_in_demo：绑定 Steam 不在本场 roster（「这场没有识别到你」）
    - identified：命中绑定玩家（player 非 None）
    DemoFocusPlayer（手动选择）不影响该结果；绑定身份优先。
    """
    ident = get_active_identity(db, user)
    if ident is None:
        return ("unbound", None)
    if demo.match_id is None:
        return ("unparsed", None)
    player = db.scalar(select(Player).where(Player.match_id == demo.match_id, Player.steam_id == ident.steam_id64))
    if player is None:
        return ("not_in_demo", None)
    return ("identified", player)


def require_pro_focus(db: Session, demo: Demo, user: User) -> Player:
    """个人 Pro/Max 付费功能门禁：必须绑定 Steam 且该绑定玩家在本场 demo roster。

    手动选他人/未绑定/不在 roster 都拒绝（清晰 reason），绝不把他人 demo 数据当作本人画像。
    """
    status, player = resolve_bound_focus(db, demo, user)
    if status == "unbound":
        raise ApiError(403, STEAM_IDENTITY_REQUIRED, "请先绑定 Steam 后再使用个人进阶复盘")
    if status == "unparsed":
        raise ApiError(409, "DEMO_NOT_PARSED", "demo 尚未解析完成")
    if status == "not_in_demo":
        raise ApiError(403, STEAM_IDENTITY_NOT_IN_DEMO,
                       "这场没有识别到你（你的 Steam 不在本场玩家名单），无法生成个人画像")
    return player


def matches_identity(db: Session, demo: Demo, user: User, player: Player | None) -> bool:
    """该 focus player 是否就是绑定本人的玩家（服务端权威重判），用于 report 打「这是你」标记。"""
    if player is None:
        return False
    ident = get_active_identity(db, user)
    if ident is None:
        return False
    return player.steam_id == ident.steam_id64
