"""Max 团队工作区与反滥用 MVP（6R14）。

规则：
- Max 账号最多一个 active 团队工作区（服务层强校验）。
- 成员必须先完成 Steam OpenID 验证，以 steam identity 占 seat；不允许 owner 手填 SteamID。
- 座位：5 主力 + 最多 2 替补（5-7 seats）；唯一 Steam 同一时刻最多属于一个 active 付费团队。
- 团队 Max 报告门禁：调用者须为 workspace 成员，且 demo roster 匹配 ≥3 名 active 成员并含调用者本人。
- roster 变更审计 + 7 天冷却；owner 转移/删除只设计状态机与测试（不做破坏性自动化）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import (
    TEAM_CALLER_NOT_IN_DEMO,
    TEAM_COOLDOWN,
    TEAM_GATE_LOW,
    TEAM_MAX_ONE,
    TEAM_NOT_MEMBER,
    TEAM_ROSTER_SIZE,
    TEAM_SEAT_CONFLICT,
    TEAM_SEAT_FULL,
    TEAM_VERIFY_REQUIRED,
    ApiError,
)
from app.models import Demo, Player, SteamIdentity, TeamAudit, TeamMembership, TeamWorkspace, User

TEAM_MAX_MAIN = 5
TEAM_MAX_SUB = 2
TEAM_MAX_SEATS = TEAM_MAX_MAIN + TEAM_MAX_SUB


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit(db: Session, team_id: int | None, user_id: int | None, action: str, detail: str | None = None) -> None:
    db.add(TeamAudit(team_id=team_id, user_id=user_id, action=action, detail=detail))


def active_workspace_for_user(db: Session, user: User) -> TeamWorkspace | None:
    """当前用户作为 active 成员所在的团队工作区（一个用户最多一个 active 团队）。"""
    row = db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id, TeamMembership.status == "active"))
    if row is None:
        return None
    return db.get(TeamWorkspace, row.team_id)


def active_members(db: Session, team_id: int) -> list[TeamMembership]:
    return list(db.scalars(select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.status == "active")).all())


def create_workspace(db: Session, user: User, name: str) -> TeamWorkspace:
    """创建团队工作区（Max）：一个 Max 账号最多一个 active 团队。owner 作为主力占座。

    6R19：对 owner 行加悲观锁串行化并发建队；DB 唯一约束兜底并发竞态 → 409（而非 500）。
    """
    db.execute(select(User).where(User.id == user.id).with_for_update())
    existing = db.scalar(select(TeamWorkspace).where(TeamWorkspace.owner_user_id == user.id))
    if existing is not None:
        raise ApiError(409, TEAM_MAX_ONE, "一个 Max 账号最多创建一个团队工作区")
    ws = TeamWorkspace(name=name, owner_user_id=user.id)
    db.add(ws)
    db.flush()
    db.add(TeamMembership(team_id=ws.id, user_id=user.id, steam_id64=_required_steam(db, user),
                          role="owner", slot_type="main", status="active"))
    _audit(db, ws.id, user.id, "create", "创建团队工作区")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, TEAM_MAX_ONE, "一个 Max 账号最多创建一个团队工作区") from exc
    db.refresh(ws)
    return ws


def _required_steam(db: Session, user: User) -> str:
    """成员必须已有 Steam 验证；未绑 → 拒绝（不允许 owner 手填）。"""
    ident = db.scalar(select(SteamIdentity).where(SteamIdentity.user_id == user.id, SteamIdentity.status == "active"))
    if ident is None:
        raise ApiError(403, TEAM_VERIFY_REQUIRED, "成员必须先绑定（验证）Steam 才能加入团队")
    return ident.steam_id64


def add_member(db: Session, caller: User, target: User, slot_type: str = "main") -> TeamMembership:
    """邀请/加入：基于 target 已验证的 Steam identity 占 seat。

    - caller 必须为 workspace 成员（owner/admin）。
    - seat 容量（5 主力 + 2 替补）校验。
    - 唯一 Steam 同一时刻最多属于一个 active 付费团队（跨团队冲突校验）。
    - 审计；7 天冷却只对变更频繁的腾挪收口（由调用方在 remove/leave 后做变更冷却校验）。
    """
    ws = active_workspace_for_user(db, caller)
    if ws is None:
        raise ApiError(403, TEAM_NOT_MEMBER, "你不是任何团队的成员")
    # 6R19：对 workspace 行加悲观锁，串行化同一团队的并发加座（seat 容量/唯一 Steam 读改写原子化）。
    db.execute(select(TeamWorkspace).where(TeamWorkspace.id == ws.id).with_for_update())
    caller_m = db.scalar(select(TeamMembership).where(TeamMembership.team_id == ws.id, TeamMembership.user_id == caller.id))
    if caller_m is None or caller_m.role not in ("owner", "admin"):
        raise ApiError(403, TEAM_NOT_MEMBER, "仅 owner/admin 可添加成员")

    if slot_type not in ("main", "sub"):
        raise ApiError(422, "VALIDATION_ERROR", "slot_type 仅支持 main/sub")
    # 目标必须是已验证账号
    target_steam = _required_steam(db, target)
    # seat 容量
    members = active_members(db, ws.id)
    used_main = sum(1 for m in members if m.slot_type == "main")
    used_sub = sum(1 for m in members if m.slot_type == "sub")
    if slot_type == "main" and used_main >= TEAM_MAX_MAIN:
        raise ApiError(409, TEAM_SEAT_FULL, "主力名额已满（最多 5 人）")
    if slot_type == "sub" and used_sub >= TEAM_MAX_SUB:
        raise ApiError(409, TEAM_SEAT_FULL, "替补名额已满（最多 2 人）")
    # 唯一 Steam 单团队
    seat = db.scalar(select(TeamMembership).where(TeamMembership.steam_id64 == target_steam, TeamMembership.status == "active"))
    if seat is not None and seat.team_id != ws.id:
        _audit(db, ws.id, target.id, "conflict", "该 Steam 已属于其他 active 团队")
        db.commit()
        raise ApiError(409, TEAM_SEAT_CONFLICT, "该 Steam 账号已属于另一个团队，不能重复占座")

    # 同团队重复 → 幂等
    dup = db.scalar(select(TeamMembership).where(TeamMembership.team_id == ws.id, TeamMembership.steam_id64 == target_steam))
    if dup is not None:
        if dup.status != "active":
            dup.status = "active"
            dup.removed_at = None
            dup.joined_at = _now()
            _audit(db, ws.id, target.id, "accept", "重新加入激活")
            db.commit()
        return dup

    row = TeamMembership(team_id=ws.id, user_id=target.id, steam_id64=target_steam, role="member",
                         slot_type=slot_type, status="active")
    db.add(row)
    _audit(db, ws.id, target.id, "invite", f"加入（{slot_type}）")
    try:
        db.commit()
    except IntegrityError as exc:
        # 6R19：跨团队唯一 Steam 部分唯一索引兜底并发竞态 → 稳定 409，而非 500。
        db.rollback()
        raise ApiError(409, TEAM_SEAT_CONFLICT, "该 Steam 账号已属于另一个团队，不能重复占座") from exc
    db.refresh(row)
    return row


def remove_member(db: Session, caller: User, target_user_id: int) -> None:
    """移除成员（owner/admin）：标记 removed 并设置 7 天变更冷却（防频繁换人共享）。"""
    ws = active_workspace_for_user(db, caller)
    if ws is None:
        raise ApiError(403, TEAM_NOT_MEMBER, "你不是任何团队的成员")
    caller_m = db.scalar(select(TeamMembership).where(TeamMembership.team_id == ws.id, TeamMembership.user_id == caller.id))
    if caller_m is None or caller_m.role not in ("owner", "admin"):
        raise ApiError(403, TEAM_NOT_MEMBER, "仅 owner/admin 可移除成员")
    row = db.scalar(select(TeamMembership).where(TeamMembership.team_id == ws.id, TeamMembership.user_id == target_user_id, TeamMembership.status == "active"))
    if row is None:
        raise ApiError(404, TEAM_NOT_MEMBER, "成员不存在")
    row.status = "removed"
    row.removed_at = _now()
    row.version += 1
    # roster 变更冷却
    caller.steam_cooldown_until = _now() + timedelta(days=settings.steam_openid_rebind_cooldown_days)
    _audit(db, ws.id, target_user_id, "remove", "移除成员")
    db.commit()


def team_gate_for_demo(db: Session, user: User, demo: Demo) -> dict:
    """团队 Max 报告门禁（只读检查）。

    要求：调用者为 workspace 成员；demo roster 至少匹配 3 名 active 成员且包含调用者本人。
    返回 {ok, team_id, matched, matched_required, caller_included, reason}。
    """
    ws = active_workspace_for_user(db, user)
    if ws is None:
        return {"ok": False, "team_id": None, "matched": 0, "matched_required": 3,
                "caller_included": False, "reason": "非团队成员：请先加入团队工作区"}
    members = active_members(db, ws.id)
    roster_steam = {m.steam_id64 for m in members}
    if demo.match_id is None:
        return {"ok": False, "team_id": ws.id, "matched": 0, "matched_required": 3,
                "caller_included": False, "reason": "demo 尚未解析完成"}
    player_steam = {p.steam_id for p in db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()}
    matched = roster_steam & player_steam
    caller_ident = db.scalar(select(SteamIdentity).where(SteamIdentity.user_id == user.id, SteamIdentity.status == "active"))
    caller_included = bool(caller_ident and caller_ident.steam_id64 in matched)
    ok = len(matched) >= 3 and caller_included
    reason = None if ok else (
        "该 demo 匹配到团队 roster 成员不足 3 人，或未包含你本人，无法生成团队深度报告"
    )
    return {"ok": ok, "team_id": ws.id, "matched": len(matched), "matched_required": 3,
            "caller_included": caller_included, "reason": reason}


def require_team_demo_entitlement(db: Session, user: User, demo: Demo) -> dict:
    """团队付费数据端点的服务端强制门禁（统一依赖，禁止前端先调 gate 代替）。

    硬性条件（任一不满足 → 403 + 稳定 code/reason，不依赖前端/API 参数/缓存/手改 focus）：
    1. 调用者属于**唯一 active workspace**；否则 403 TEAM_NOT_MEMBER。
    2. active roster 为 **5-7 人**（5 主力 + 最多 2 替补）；否则 403 TEAM_ROSTER_SIZE。
    3. **调用者本人**在该 demo 阵容；否则 403 TEAM_CALLER_NOT_IN_DEMO。
    4. demo 与 verified active roster **至少重合 3 人**；否则 403 TEAM_GATE_LOW。

    成功返回 {workspace_id, roster_size, matched, caller_included}。
    """
    ws = active_workspace_for_user(db, user)
    if ws is None:
        raise ApiError(403, TEAM_NOT_MEMBER, "非团队工作区成员：请先创建或加入团队工作区")
    members = active_members(db, ws.id)
    roster_steam = {m.steam_id64 for m in members}
    roster_size = len(roster_steam)
    if not (TEAM_MAX_MAIN <= roster_size <= TEAM_MAX_SEATS):
        raise ApiError(403, TEAM_ROSTER_SIZE,
                       f"团队实名 roster 需为 5-7 人（5 主力 + 最多 2 替补），当前 {roster_size} 人")
    if demo.match_id is None:
        raise ApiError(409, "DEMO_NOT_PARSED", "demo 尚未解析完成")
    player_steam = {p.steam_id for p in db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()}
    matched = roster_steam & player_steam
    caller_ident = db.scalar(select(SteamIdentity).where(SteamIdentity.user_id == user.id, SteamIdentity.status == "active"))
    caller_steam = caller_ident.steam_id64 if caller_ident else None
    caller_included = bool(caller_steam and caller_steam in matched)
    if not caller_included:
        raise ApiError(403, TEAM_CALLER_NOT_IN_DEMO, "你本人不在该 demo 阵容中，无法生成团队付费分析")
    if len(matched) < 3:
        raise ApiError(403, TEAM_GATE_LOW,
                       f"该 demo 匹配到团队 roster 成员不足 3 人（当前 {len(matched)} 人），无法生成团队深度报告")
    return {"workspace_id": ws.id, "roster_size": roster_size, "matched": len(matched),
            "caller_included": caller_included}
