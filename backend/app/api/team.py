"""Max 团队工作区 API（6R14）。

只输出掩码 steam_id64 / player 信息，绝不输出完整 SteamID。owner 转移/删除不做破坏性自动化。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.errors import ApiError
from app.models import TeamMembership, User
from app.services import team as team_service
from app.services.steamid import mask_steamid64
from app.services.tier import require_tier

router = APIRouter(prefix="/api/team", tags=["team"])


class CreateTeam(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)


class AddMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int
    slot_type: str = Field(default="main", pattern="^(main|sub)$")


def _member_view(m: TeamMembership) -> dict:
    return {
        "user_id": m.user_id,
        "team_id": m.team_id,
        "steam_id64_masked": mask_steamid64(m.steam_id64),
        "role": m.role,
        "slot_type": m.slot_type,
        "status": m.status,
        "joined_at": m.joined_at.isoformat() if m.joined_at else None,
    }


@router.post("")
def create_team(
    body: CreateTeam,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建团队工作区（Max）；一个 Max 账号最多一个 active 团队。"""
    require_tier(current_user, "max", "团队工作区属于高级版（Max）功能，升级后解锁")
    ws = team_service.create_workspace(db, current_user, body.name)
    members = team_service.active_members(db, ws.id)
    return {"team": {"id": ws.id, "name": ws.name, "owner_user_id": ws.owner_user_id}, "members": [_member_view(m) for m in members]}


@router.get("")
def get_team(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """当前用户的团队工作区与 roster（掩码）。"""
    ws = team_service.active_workspace_for_user(db, current_user)
    if ws is None:
        return {"team": None, "members": []}
    members = team_service.active_members(db, ws.id)
    return {"team": {"id": ws.id, "name": ws.name, "owner_user_id": ws.owner_user_id}, "members": [_member_view(m) for m in members]}


@router.post("/members")
def add_member(body: AddMember, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.get(User, body.user_id)
    if target is None:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在")
    m = team_service.add_member(db, current_user, target, body.slot_type)
    return {"member": _member_view(m)}


@router.delete("/members/{user_id}")
def remove_member(user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team_service.remove_member(db, current_user, user_id)
    return {"removed": True}


@router.get("/gate/{demo_id}")
def team_gate(demo_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """团队 Max 报告门禁检查（供前端显示）；不做改动。"""
    from app.api.demo import _get_own_demo

    demo = _get_own_demo(demo_id, current_user, db)
    return team_service.team_gate_for_demo(db, current_user, demo)
