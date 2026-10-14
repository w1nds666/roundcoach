"""6R14 测试共享 helper：为「团队付费数据端点」测试赋予满足 `require_team_demo_entitlement` 的 workspace/roster。

门禁条件：唯一 active workspace + active roster 5-7 人 + 调用者本人在 demo + demo 与 roster 重合 ≥3。
本 helper 在测试已有的 demo/match 基础上：
- 确保 match 至少有 3 名 Player（不足则补占位玩家，strean 为合法 17 位）。
- 把调用用户绑定到该 demo 首个玩家（steam）。
- 建一个 workspace（owner=调用用户）并把 roster 凑到 5 人（owner + 4），其中 ≥3 名成员 steam 来自 demo 玩家、且含调用者。
幂等：可重复调用；不破坏既有 Player/Stats 数据，只新增占位玩家与 roster 成员。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Demo, Match, Player, SteamIdentity, TeamMembership, TeamWorkspace, User

_FILLER_BASE = 76561198812079900
_EXTRA_BASE = 76561198812079800


def _future() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)


def _bind_active(db: Session, steam: str, user_id: int | None) -> None:
    """幂等地让某个 steam 处于 active 绑定到 user_id（存在则复用，不重复插入）。"""
    row = db.scalar(select(SteamIdentity).where(SteamIdentity.steam_id64 == steam))
    if row is None:
        db.add(SteamIdentity(provider="steam", subject=steam, steam_id64=steam, user_id=user_id, status="active"))
    else:
        row.status = "active"
        row.user_id = user_id
        row.bound_at = row.bound_at
        row.unbound_at = None
        row.version += 1


def grant_team_entitlement(db: Session, email: str, demo_id: int) -> None:
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None, f"用户不存在 {email}"
    demo = db.get(Demo, demo_id)
    assert demo is not None and demo.match_id is not None, "demo 未解析"
    match = db.get(Match, demo.match_id)

    # 1) 玩家列表（含补位，确保 >=3）
    players = list(db.scalars(select(Player).where(Player.match_id == match.id)).all())
    existing = [p.steam_id for p in players]
    while len(existing) < 3:
        sid = str(_EXTRA_BASE + len(existing))
        db.add(Player(match_id=match.id, steam_id=sid, name=f"GT{len(existing)}",
                      team="CT", team_identity="A"))
        existing.append(sid)

    # 2) 调用用户绑定到首个 demo 玩家
    first = existing[0]
    for ident in db.scalars(select(SteamIdentity).where(SteamIdentity.user_id == user.id)).all():
        ident.status = "unbound"
        ident.user_id = None
    _bind_active(db, first, user.id)

    # 3) workspace（owner=调用用户）
    ws = db.scalar(select(TeamWorkspace).where(TeamWorkspace.owner_user_id == user.id))
    if ws is None:
        ws = TeamWorkspace(name="6R14 Team", owner_user_id=user.id)
        db.add(ws)
        db.flush()

    # 4) owner seat + 再 4 名成员（凑满 5；优先 demo 玩家 steam 保证 >=3 重叠）
    db.add(TeamMembership(team_id=ws.id, user_id=user.id, steam_id64=first,
                          role="owner", slot_type="main", status="active"))
    extra_steams = existing[1:3]
    while len(extra_steams) < 4:
        extra_steams.append(str(_FILLER_BASE + len(extra_steams)))
    for i, sid in enumerate(extra_steams, start=1):
        u = db.scalar(select(User).where(User.email == f"grant{i}@test.com"))
        if u is None:
            u = User(email=f"grant{i}@test.com", password_hash="x", nickname=f"G{i}",
                     subscription_tier="max", subscription_expire=_future())
            db.add(u)
            db.flush()
        _bind_active(db, sid, u.id)
        db.add(TeamMembership(team_id=ws.id, user_id=u.id, steam_id64=sid, role="member",
                              slot_type="main", status="active"))
    db.commit()
