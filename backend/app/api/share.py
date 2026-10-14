"""Owner-managed, revocable links for a strictly redacted match summary."""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.demo import _get_own_demo
from app.api.deps import get_current_user
from app.database import get_db
from app.errors import DEMO_NOT_PARSED, SHARE_NOT_FOUND, ApiError
from app.models import Demo, DemoShare, Match, Player, PlayerStats, User
from app.services.rating_pro import build_rating
from app.services.rate_limit import rate_limit


router = APIRouter(prefix="/api", tags=["shares"])
TOKEN_RE = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")
PUBLIC_HEADERS = {
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class CreateShare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expires_in_hours: int = Field(default=24, ge=1, le=168)


class ResolveShare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=128)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _share_status(share: DemoShare, now: datetime | None = None) -> str:
    now = now or _utc_now()
    if share.revoked_at is not None:
        return "revoked"
    if share.expires_at <= now:
        return "expired"
    return "active"


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


def _share_metadata(share: DemoShare) -> dict:
    return {
        "status": _share_status(share),
        "created_at": _iso_utc(share.created_at),
        "expires_at": _iso_utc(share.expires_at),
        "revoked_at": _iso_utc(share.revoked_at),
    }


def _number(value, digits: int | None = None):
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return round(value, digits) if digits is not None else value


def _missing_share() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"detail": {"code": SHARE_NOT_FOUND, "message": "分享不存在或已失效"}},
        headers=PUBLIC_HEADERS,
    )


def _public_payload(demo: Demo, share: DemoShare, db: Session) -> dict | None:
    """Allowlist serializer: never reuse owner serializers or expose identity/coordinates."""
    match = db.get(Match, demo.match_id)
    if match is None:
        return None
    players = db.scalars(select(Player).where(Player.match_id == match.id).order_by(Player.id)).all()
    stats = {row.player_id: row for row in db.scalars(select(PlayerStats).where(PlayerStats.match_id == match.id)).all()}
    public_players = []
    for index, player in enumerate(players, start=1):
        row = stats.get(player.id)
        if row is None:
            continue
        public_players.append({
            "player_key": f"p{index}",
            "stats": {
                "kills": row.kills,
                "deaths": row.deaths,
                "assists": row.assists,
                "hs_rate": _number(row.hs_rate, 4),
                "damage": row.damage,
                "adr": _number(row.adr, 1),
                "first_kills": row.first_kills,
            },
            # 6R10B：Rating Pro v1（主指标；匿名展示评分 + 分量 + 人话解释 + 参考范围）
            "rating_pro": build_rating(row.rating_raw),
        })
    return {
        "share": {"expires_at": _iso_utc(share.expires_at)},
        "match": {
            "map_name": match.map_name,
            "team_a_score": match.team_a_score if match.team_a_score is not None else match.score_ct,
            "team_b_score": match.team_b_score if match.team_b_score is not None else match.score_t,
            "rounds": match.rounds,
            "duration_seconds": match.duration_seconds,
        },
        "players": public_players,
    }


@router.post("/demos/{demo_id}/share")
def create_share(
    demo_id: int,
    body: CreateShare,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.status != "parsed" or demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成，不能分享")
    db.execute(select(Demo.id).where(Demo.id == demo_id).with_for_update()).scalar_one()

    token = secrets.token_urlsafe(32)
    now = _utc_now()
    share = db.scalar(select(DemoShare).where(DemoShare.demo_id == demo.id))
    if share is None:
        share = DemoShare(demo_id=demo.id, user_id=current_user.id, token_hash=_token_hash(token), expires_at=now)
        db.add(share)
    share.user_id = current_user.id
    share.token_hash = _token_hash(token)
    share.created_at = now
    share.expires_at = now + timedelta(hours=body.expires_in_hours)
    share.revoked_at = None
    db.commit()
    db.refresh(share)
    return {"token": token, **_share_metadata(share)}


@router.get("/demos/{demo_id}/share")
def get_share(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = _get_own_demo(demo_id, current_user, db)
    share = db.scalar(select(DemoShare).where(DemoShare.demo_id == demo.id, DemoShare.user_id == current_user.id))
    return {"share": _share_metadata(share) if share is not None else None}


@router.delete("/demos/{demo_id}/share")
def revoke_share(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = _get_own_demo(demo_id, current_user, db)
    share = db.scalar(select(DemoShare).where(DemoShare.demo_id == demo.id, DemoShare.user_id == current_user.id))
    if share is None or share.revoked_at is not None:
        raise ApiError(404, SHARE_NOT_FOUND, "分享不存在或已失效")
    share.revoked_at = _utc_now()
    db.commit()
    return {"revoked": True}


@router.post("/share/resolve")
def resolve_share(body: ResolveShare, db: Session = Depends(get_db),
                  _rl: None = Depends(rate_limit("share_resolve"))):
    if not TOKEN_RE.fullmatch(body.token):
        return _missing_share()
    digest = _token_hash(body.token)
    share = db.scalar(select(DemoShare).where(DemoShare.token_hash == digest))
    if share is None or not hmac.compare_digest(digest, share.token_hash) or _share_status(share) != "active":
        return _missing_share()
    demo = db.get(Demo, share.demo_id)
    if demo is None or demo.deleted_at is not None or demo.status != "parsed" or demo.match_id is None:
        return _missing_share()
    payload = _public_payload(demo, share, db)
    if payload is None:
        return _missing_share()
    return JSONResponse(content=payload, headers=PUBLIC_HEADERS)
