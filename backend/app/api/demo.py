"""demo API（计划书第六节：上传/列表/状态/报告/热力图）。

Phase 3：
- 全部接口需登录（JWT）
- 对象级权限隔离：非本人 demo 一律 404（不泄露存在性）
- 免费额度：每天 1 个上传（429）
- 订阅鉴权：route_* 热力图与 advanced 报告需有效 pro（403 + 机器码 + 中文提示）
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, time, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.errors import (
    DEMO_NOT_FOUND,
    DEMO_NOT_PARSED,
    DEMO_ALREADY_DELETED,
    DEMO_NOT_DELETED,
    DEMO_SOURCE_MISSING,
    BATCH_SIZE_INVALID,
    DUPLICATE_DEMO,
    FILE_CONTENT_INVALID,
    FILE_TOO_LARGE,
    FILE_TYPE_INVALID,
    FREE_DAILY_LIMIT,
    QUEUE_UNAVAILABLE,
    UPLOAD_FAILED,
    SUBSCRIPTION_REQUIRED,
    ApiError,
)
from app.models import Demo, DemoFocusPlayer, DemoShare, Event, HeatmapCache, Match, Player, PlayerStats, RoundSide, User
from app.services.storage import storage
from app.services.rate_limit import rate_limit
from app.tasks.parse_tasks import process_demo

router = APIRouter(prefix="/api/demos", tags=["demos"])

# 上传大小上限（配置化：settings.max_demo_size_mb，默认 300MiB；MVR 验收）
MAX_DEMO_SIZE = settings.max_demo_size_mb * 1024 * 1024
# CS2 demo 魔数（parse_header 实测 demo_file_stamp='PBDEMS2\x00'）——8 字节。
# 6R19：与后端解析器（services/parser.py 用 head[:8] != b"PBDEMS2\x00"）对齐为 8 字节，
# 防止「PBDEMS2 + 任意垃圾」通过上传关卡进入解析（降低解析资源耗尽攻击的门槛）。
DEMO_MAGIC = b"PBDEMS2\x00"

# 需订阅的热力图模式（路线分析属进阶功能）
PRO_ONLY_HEATMAP_MODES = ("route_all", "route_ct", "route_t")

PRO_UPGRADE_HINT = "该功能属于进阶版，升级后解锁路线分析、死亡复盘、团队配合与 AI 诊断报告"
SOURCE_KEY_RE = re.compile(r"\Ademos/[0-9a-f]{32}\.dem\Z")


def _is_free_tier(user: User) -> bool:
    """免费/过期 pro/max 视为 free（6R1：统一走 tier.effective_tier）。"""
    from app.services.tier import effective_tier

    return effective_tier(user) == "free"


def _require_subscription(user: User, required: str = "pro", hint: str = PRO_UPGRADE_HINT) -> None:
    """有效 pro/max 放行；free/过期统一 403（6R1：集中 tier.require_tier）。"""
    from app.services.tier import require_tier

    require_tier(user, required, feature_hint=hint)


class DemoPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = None
    is_favorite: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("display_name", "notes", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("必须是字符串")
        value = value.strip()
        return value or None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        if value is None:
            raise ValueError("tags 不能为 null，请使用空数组清空")
        if not isinstance(value, list) or len(value) > 10:
            raise ValueError("标签最多 10 个")
        out = []
        for tag in value:
            if not isinstance(tag, str):
                raise ValueError("标签必须是字符串")
            tag = tag.strip()
            if not 1 <= len(tag) <= 32:
                raise ValueError("标签长度必须为 1-32")
            if tag not in out: out.append(tag)
        return out

    @field_validator("is_favorite", mode="before")
    @classmethod
    def require_bool(cls, value):
        if value is None:
            raise ValueError("is_favorite 不能为 null")
        return value


def _get_own_demo(demo_id: int, user: User, db: Session, include_deleted: bool = False) -> Demo:
    """取本人 demo；不存在或非本人一律 404（不泄露存在性）。"""
    demo = db.get(Demo, demo_id)
    if demo is None or demo.user_id != user.id or (demo.deleted_at is not None and not include_deleted):
        raise ApiError(404, DEMO_NOT_FOUND, "demo 不存在")
    return demo


def _source_content_disposition(file_name: str, demo_id: int) -> str:
    """Build a header-safe filename with ASCII and RFC 5987 UTF-8 variants."""
    leaf = (file_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    leaf = "".join(ch for ch in leaf if ch not in "\r\n\0" and ord(ch) >= 32).strip(" .")
    if not leaf:
        leaf = f"demo-{demo_id}.dem"
    if not leaf.lower().endswith(".dem"):
        leaf += ".dem"

    ascii_name = leaf.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", ascii_name).strip(" .")
    if not ascii_name or not ascii_name.lower().endswith(".dem"):
        ascii_name = f"demo-{demo_id}.dem"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(leaf, safe="")}'


@router.get("")
def list_demos(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    q: str | None = None,
    status: str | None = None,
    map_name: str | None = None,
    sort: str = "newest",
    limit: int = 20,
    offset: int = 0,
    view: str = "active",
    tag: str | None = None,
):
    """当前用户自己的 demo 列表（筛选/排序/分页；不泄露他人对象）。"""
    if view not in ("active", "trash", "all"):
        raise ApiError(422, "VALIDATION_ERROR", "view 仅支持 active/trash/all")
    if sort not in ("newest", "oldest"):
        raise ApiError(422, "VALIDATION_ERROR", "sort 仅支持 newest/oldest")
    if status and status not in ("uploaded", "parsing", "parsed", "failed"):
        raise ApiError(422, "VALIDATION_ERROR", "status 参数无效")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    stmt = select(Demo).where(Demo.user_id == current_user.id)
    if view == "active": stmt = stmt.where(Demo.deleted_at.is_(None))
    elif view == "trash": stmt = stmt.where(Demo.deleted_at.is_not(None))
    if q: stmt = stmt.where(or_(Demo.file_name.ilike(f"%{q.strip()}%"), Demo.display_name.ilike(f"%{q.strip()}%")))
    if status: stmt = stmt.where(Demo.status == status)
    if map_name: stmt = stmt.where(Demo.map_name == map_name)
    if tag is not None:
        tag = tag.strip()
        if not 1 <= len(tag) <= 32:
            raise ApiError(422, "VALIDATION_ERROR", "tag 长度必须为 1-32")
        if db.bind and db.bind.dialect.name == "sqlite":
            json_tags = func.json_each(Demo.tags).table_valued("value")
            stmt = stmt.where(exists(select(1).select_from(json_tags).where(json_tags.c.value == tag)))
        else:
            stmt = stmt.where(Demo.tags.contains([tag]))
    stmt = stmt.order_by(Demo.created_at.desc() if sort == "newest" else Demo.created_at.asc()).limit(limit).offset(offset)
    rows = db.scalars(stmt).all()
    matches = {m.demo_id: m for m in db.scalars(select(Match).where(Match.demo_id.in_([d.id for d in rows]))).all()} if rows else {}
    return [
        {
            "id": d.id,
            "file_name": d.file_name,
            "status": d.status,
            "map_name": d.map_name,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "file_size": d.file_size,
            "team_a_score": matches[d.id].team_a_score if d.id in matches else None,
            "team_b_score": matches[d.id].team_b_score if d.id in matches else None,
            "rounds": matches[d.id].rounds if d.id in matches else None,
            "duration_seconds": matches[d.id].duration_seconds if d.id in matches else None,
            "error_msg": d.error_msg,
            "parse_warnings": d.parse_warnings,
            "heatmap_warnings": d.heatmap_warnings,
            "source": d.source,
            "source_match_id": d.source_match_id,
            "display_name": d.display_name,
            "tags": d.tags or [],
            "is_favorite": d.is_favorite,
            "notes": d.notes,
            "deleted_at": d.deleted_at.isoformat() if d.deleted_at else None,
        }
        for d in rows
    ]


async def _upload_one(
    file: UploadFile,
    current_user: User,
    db: Session,
):
    """上传 .dem（≤配置上限，默认 300MiB，魔数校验）→ MinIO → Demo 记录 → 投递 Celery 解析。

    - 免费额度：free/过期 pro 每天 1 个（429）；有效 pro 绕过
    - 分块读取写入临时文件（不全量进内存），超限立即终止并清理（P1-1）
    - MinIO 流式上传；Celery 投递失败 → demo 置 failed + 503，不谎称已开始解析（P1-3）
    """
    # 1. 扩展名校验
    if not (file.filename or "").lower().endswith(".dem"):
        raise ApiError(415, FILE_TYPE_INVALID, "仅支持 .dem 文件")

    # 2. 日上传额度（6R1：按档位矩阵 free=1/pro=5/max=20）
    from app.services.tier import effective_tier, entitlement_matrix

    daily_limit = entitlement_matrix(current_user)["upload_daily_limit"]
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min)
    today_count = db.scalar(
        select(func.count(Demo.id)).where(
            Demo.user_id == current_user.id,
            Demo.created_at >= today_start,
        )
    )
    if today_count >= daily_limit:
        raise ApiError(429, FREE_DAILY_LIMIT,
                       f"今日上传额度已用尽（{daily_limit} 个/天），升级档位解锁更多额度")

    # 3. 分块读取：先验魔数（前 7 字节），再写入临时文件并计数，超限立即终止
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".dem", delete=False)
    tmp_path = tmp.name
    size = 0
    digest = hashlib.sha256()
    try:
        head = await file.read(len(DEMO_MAGIC))
        if len(head) < len(DEMO_MAGIC) or head != DEMO_MAGIC:
            raise ApiError(422, FILE_CONTENT_INVALID, "文件内容不是合法的 CS2 demo")
        tmp.write(head)
        digest.update(head)
        size += len(head)

        while True:
            chunk = await file.read(1024 * 1024)  # 1MiB 分块
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_DEMO_SIZE:  # 超过配置上限立即终止（流式，临时文件由 finally 清理）
                raise ApiError(413, FILE_TOO_LARGE, f"demo 文件超过 {settings.max_demo_size_mb}MiB 上限")
            tmp.write(chunk)
            digest.update(chunk)
        tmp.flush()
        tmp.close()

        # 4. MinIO 流式上传 + Demo 记录
        key = f"demos/{uuid.uuid4().hex}.dem"
        content_sha256 = digest.hexdigest()
        existing = db.scalar(select(Demo).where(Demo.user_id == current_user.id, Demo.content_sha256 == content_sha256))
        if existing is not None:
            raise ApiError(409, DUPLICATE_DEMO, "内容已上传，不能重复上传", {"existing_demo_id": existing.id, "existing_deleted": existing.deleted_at is not None})
        storage.upload_file(key, tmp_path)
        demo = Demo(user_id=current_user.id, file_name=file.filename, file_path=key, file_size=size,
                    status="uploaded", content_sha256=content_sha256, source="upload")
        db.add(demo)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            try:
                storage.delete(key)
            except Exception:
                # 清理失败不覆盖重复上传结论；随机 key 不会指向已有对象。
                pass
            existing = db.scalar(select(Demo).where(Demo.user_id == current_user.id, Demo.content_sha256 == content_sha256))
            if existing is not None:
                raise ApiError(409, DUPLICATE_DEMO, "内容已上传，不能重复上传", {"existing_demo_id": existing.id, "existing_deleted": existing.deleted_at is not None}) from exc
            raise
        db.refresh(demo)

        # 5. 投递 Celery；失败补偿：demo 置 failed + 稳定错误，返回 503
        try:
            process_demo.delay(demo.id)
        except Exception as exc:  # noqa: BLE001
            db.refresh(demo)
            demo.status = "failed"
            demo.error_msg = "解析任务投递失败：队列暂不可用，请联系管理员"
            db.commit()
            raise ApiError(503, QUEUE_UNAVAILABLE, "解析队列暂不可用，请稍后重试") from exc

        return {"demo_id": demo.id, "status": "uploaded", "message": "上传成功，开始解析"}
    finally:
        # 临时文件必须清理（成功/失败均执行）。
        # Windows 原生运行时：先安全 close（幂等）再 unlink，
        # 避免 unlink 仍打开的文件导致残留（Linux/Docker 行为一致）。
        import os

        try:
            tmp.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.post("/upload")
async def upload_demo(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("upload")),
):
    """兼容的单文件上传契约；核心处理由 batch 复用。"""
    return await _upload_one(file, current_user, db)


@router.post("/upload/batch")
async def upload_batch(
    files: list[UploadFile] | None = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("upload")),
):
    """顺序处理批次；单项失败隔离，配额按成功创建的文件实时消耗。"""
    if not files or len(files) > 20:
        raise ApiError(422, BATCH_SIZE_INVALID, "批量上传文件数必须为 1-20 个")
    results = []
    for index, file in enumerate(files):
        item = {"index": index, "file_name": file.filename or ""}
        try:
            item.update({"ok": True, **(await _upload_one(file, current_user, db))})
        except ApiError as exc:
            db.rollback()
            item.update({"ok": False, "error": {"code": exc.code, "message": exc.message, **exc.details}})
        except Exception:
            db.rollback()
            item.update({"ok": False, "error": {"code": UPLOAD_FAILED, "message": "上传失败，请稍后重试"}})
        results.append(item)
    succeeded = sum(1 for r in results if r["ok"])
    return {"total": len(files), "succeeded": succeeded, "failed": len(files) - succeeded, "results": results}


@router.get("/{demo_id}/status")
def demo_status(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = _get_own_demo(demo_id, current_user, db)
    return {
        "id": demo.id,
        "status": demo.status,
        "progress": 100 if demo.status in ("parsed", "failed") else 0,
        "error_msg": demo.error_msg,
        "parse_warnings": demo.parse_warnings,
        "heatmap_warnings": demo.heatmap_warnings,
        "source": demo.source,
        "source_match_id": demo.source_match_id,
    }


@router.patch("/{demo_id}")
def update_demo_metadata(
    demo_id: int,
    body: DemoPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = _get_own_demo(demo_id, current_user, db)
    if not body.model_fields_set:
        raise ApiError(422, "VALIDATION_ERROR", "至少提供一个要更新的字段")
    for field in body.model_fields_set:
        setattr(demo, field, getattr(body, field))
    db.commit()
    db.refresh(demo)
    return {"id": demo.id, "display_name": demo.display_name, "tags": demo.tags or [], "is_favorite": demo.is_favorite, "notes": demo.notes}


@router.delete("/{demo_id}")
def delete_demo(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = db.scalar(select(Demo).where(Demo.id == demo_id, Demo.user_id == current_user.id))
    if demo is None:
        raise ApiError(404, DEMO_NOT_FOUND, "demo 不存在")
    if demo.deleted_at is not None:
        raise ApiError(409, DEMO_ALREADY_DELETED, "demo 已在回收站")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    demo.deleted_at = now
    share = db.scalar(select(DemoShare).where(DemoShare.demo_id == demo.id, DemoShare.revoked_at.is_(None)))
    if share is not None:
        share.revoked_at = now
    db.commit()
    return {"id": demo.id, "deleted": True}


@router.post("/{demo_id}/restore")
def restore_demo(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = db.scalar(select(Demo).where(Demo.id == demo_id, Demo.user_id == current_user.id))
    if demo is None:
        raise ApiError(404, DEMO_NOT_FOUND, "demo 不存在")
    if demo.deleted_at is None:
        raise ApiError(409, DEMO_NOT_DELETED, "demo 不在回收站")
    demo.deleted_at = None
    db.commit()
    return {"id": demo.id, "restored": True}


@router.post("/{demo_id}/retry")
def retry_demo(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = db.scalar(select(Demo).where(Demo.id == demo_id, Demo.user_id == current_user.id).with_for_update())
    if demo is None or demo.deleted_at is not None:
        raise ApiError(404, DEMO_NOT_FOUND, "demo 不存在")
    if demo.status != "failed":
        raise ApiError(409, "DEMO_NOT_RETRYABLE", "仅解析失败的 demo 可以重新解析")
    if demo.file_path.startswith("demos/"):
        source_exists = storage.exists(demo.file_path)
    else:
        source_exists = __import__("pathlib").Path(demo.file_path).exists()
    if not source_exists:
        raise ApiError(409, DEMO_SOURCE_MISSING, "原始 demo 文件不存在，无法重新解析")
    demo.status = "uploaded"
    demo.error_msg = None
    demo.parse_warnings = None
    demo.heatmap_warnings = None
    db.commit()
    try:
        process_demo.delay(demo.id)
    except Exception as exc:
        db.refresh(demo)
        demo.status = "failed"
        demo.error_msg = "解析任务投递失败：队列暂不可用，请稍后重试"
        db.commit()
        raise ApiError(503, QUEUE_UNAVAILABLE, "解析队列暂不可用，请稍后重试") from exc
    return {"id": demo.id, "status": "uploaded", "message": "已重新加入解析队列"}


def _round_rows(demo: Demo, db: Session, round_no: int | None = None) -> list[dict]:
    """RoundSide/Event 聚合；winner 明确为 None，因为当前模型没有胜方字段。"""
    match = db.get(Match, demo.match_id)
    if match is None:
        return []
    sides = {r.round: r for r in db.scalars(select(RoundSide).where(RoundSide.match_id == demo.match_id)).all()}
    events = db.scalars(select(Event).where(Event.match_id == demo.match_id).order_by(Event.round, Event.tick)).all()
    grouped: dict[int, list[Event]] = {}
    for e in events: grouped.setdefault(e.round, []).append(e)
    numbers = [round_no] if round_no is not None else list(range(1, match.rounds + 1))
    side_label = lambda value: "CT" if value == 2 else "T" if value == 3 else None
    out = []
    for number in numbers:
        rs = sides.get(number)
        evs = grouped.get(number, [])
        out.append({
            "round": number,
            "side_a": side_label(rs.side_a) if rs else None,
            "side_b": side_label(rs.side_b) if rs else None,
            "start_tick": min((e.tick for e in evs), default=None),
            "end_tick": max((e.tick for e in evs), default=None),
            "kills_count": sum(e.event_type == "kill" for e in evs),
            "grenades_count": sum(e.event_type == "grenade" for e in evs),
            "winner": None,
        })
    return out


def _focus_side_map(demo: Demo, db: Session, user: User) -> dict[int, str]:
    focus = db.scalar(select(DemoFocusPlayer).where(DemoFocusPlayer.demo_id == demo.id, DemoFocusPlayer.user_id == user.id))
    if focus is None:
        raise ApiError(409, "FOCUS_REQUIRED", "传入 CT/T 筛选前请先选择「我」对应的玩家")
    player = db.get(Player, focus.player_id)
    if player is None or player.match_id != demo.match_id:
        raise ApiError(409, "FOCUS_REQUIRED", "传入 CT/T 筛选前请先选择「我」对应的玩家")
    if player.team_identity not in ("A", "B"):
        raise ApiError(409, "FOCUS_REQUIRED", "焦点玩家缺少有效队伍身份，请重新选择「我」对应的玩家")
    out = {}
    for r in db.scalars(select(RoundSide).where(RoundSide.match_id == demo.match_id)).all():
        raw = r.side_a if player.team_identity == "A" else r.side_b
        if raw in (2, 3): out[r.round] = "CT" if raw == 2 else "T"
    return out


@router.get("/{demo_id}/rounds")
def demo_rounds(
    demo_id: int,
    round_no: int | None = None,
    side: str = "all",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None or demo.status != "parsed": raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    if side not in ("all", "CT", "T"): raise ApiError(422, "VALIDATION_ERROR", "side 仅支持 all/CT/T")
    match = db.get(Match, demo.match_id)
    if round_no is not None and (round_no < 1 or round_no > match.rounds): raise ApiError(422, "VALIDATION_ERROR", "round_no 必须在有效回合范围内")
    focus_sides = _focus_side_map(demo, db, current_user) if side != "all" else {}
    rows = _round_rows(demo, db, round_no)
    if side != "all": rows = [r for r in rows if focus_sides.get(r["round"]) == side]
    return {"side_filter": side, "side_semantics": "CT/T 表示 focus player 在该回合的真实阵营；all 不依赖 focus", "rounds": rows}


@router.get("/{demo_id}/events")
def demo_events(
    demo_id: int,
    event_type: str = "all",
    player_id: int | None = None,
    round_no: int | None = None,
    weapon: str | None = None,
    tick_from: int | None = None,
    tick_to: int | None = None,
    limit: int = 100,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None or demo.status != "parsed": raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    match = db.get(Match, demo.match_id)
    if event_type not in ("all", "kill", "grenade"): raise ApiError(422, "VALIDATION_ERROR", "event_type 仅支持 all/kill/grenade")
    if round_no is not None and (round_no < 1 or round_no > match.rounds): raise ApiError(422, "VALIDATION_ERROR", "round_no 必须在有效回合范围内")
    if tick_from is not None and tick_to is not None and tick_from > tick_to: raise ApiError(422, "VALIDATION_ERROR", "tick_from 不能大于 tick_to")
    limit, offset = min(max(limit, 1), 100), max(offset, 0)
    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    by_id, by_steam = {p.id: p for p in players}, {p.steam_id: p for p in players}
    if player_id is not None and player_id not in by_id: raise ApiError(404, "PLAYER_NOT_FOUND", "玩家不存在或不属于该比赛")
    stmt = select(Event).where(Event.match_id == demo.match_id)
    if event_type != "all": stmt = stmt.where(Event.event_type == event_type)
    if round_no is not None: stmt = stmt.where(Event.round == round_no)
    if weapon is not None: stmt = stmt.where(Event.weapon == weapon)
    if tick_from is not None: stmt = stmt.where(Event.tick >= tick_from)
    if tick_to is not None: stmt = stmt.where(Event.tick <= tick_to)
    if player_id is not None:
        sid = by_id[player_id].steam_id
        stmt = stmt.where(or_(Event.actor_steam == sid, Event.victim_steam == sid))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Event.tick).limit(limit).offset(offset)).all()
    def person(sid):
        p = by_steam.get(sid or "")
        return {"player_id": p.id, "name": p.name} if p else None
    return {"total": total, "limit": limit, "offset": offset, "events": [
        {"id": e.id, "event_type": e.event_type, "tick": e.tick, "round": e.round, "weapon": e.weapon,
         "grenade_type": e.grenade_type, "actor": person(e.actor_steam), "victim": person(e.victim_steam),
         "x": _clean_float(e.x), "y": _clean_float(e.y), "actor_x": _clean_float(e.actor_x), "actor_y": _clean_float(e.actor_y),
         "victim_x": _clean_float(e.victim_x), "victim_y": _clean_float(e.victim_y)} for e in rows]}


def _basic_report_payload(demo: Demo, db: Session) -> dict:
    match = db.get(Match, demo.match_id)
    players = db.scalars(select(Player).where(Player.match_id == match.id)).all()
    stats_rows = db.scalars(select(PlayerStats).where(PlayerStats.match_id == match.id)).all()
    stat_of = {s.player_id: s for s in stats_rows}
    heatmaps = db.scalars(select(HeatmapCache).where(HeatmapCache.match_id == match.id)).all()
    return {
        "demo": {"id": demo.id, "file_name": demo.file_name, "map_name": demo.map_name},
        "match": {
            "map_name": match.map_name,
            # 6R0：队伍比分（换边正确）；score_ct/score_t 为阵营胜局（兼容，UI 不再作为最终比分）
            "team_a_score": match.team_a_score,
            "team_b_score": match.team_b_score,
            "team_a_name": match.team_a_name or "队伍 A",
            "team_b_name": match.team_b_name or "队伍 B",
            "score_ct": match.score_ct,
            "score_t": match.score_t,
            "duration_seconds": match.duration_seconds,
            "rounds": match.rounds,
            "parsed_at": match.parsed_at.isoformat() if match.parsed_at else None,
        },
        "players": [
            {
                "player_id": p.id,          # 6R1：opaque 内部 ID（绝不返回完整 steam_id）
                "name": p.name,
                "team": p.team,
                "stats": {
                    "kills": s.kills,
                    "deaths": s.deaths,
                    "assists": s.assists,
                    "hs_rate": s.hs_rate,
                    "damage": s.damage,
                    "adr": s.adr,
                    "first_kills": s.first_kills,
                },
            }
            for p in players
            if (s := stat_of.get(p.id)) is not None
        ],
        "heatmaps": [
            {"mode": h.mode, "url": f"/api/demos/{demo.id}/heatmap/{h.mode}"}
            for h in heatmaps
        ],
    }


@router.get("/{demo_id}/report/basic")
def basic_report(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """基础版报告（免费）：概览 + 玩家统计 + 热力图清单。"""
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.status != "parsed" or demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    return _basic_report_payload(demo, db)


@router.get("/{demo_id}/report/advanced")
def advanced_report(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """进阶报告（需有效 pro）。内容在 Phase 4/5 补充，此处先完成鉴权闭环。"""
    _require_subscription(current_user)
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.status != "parsed" or demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    payload = _basic_report_payload(demo, db)
    payload["advanced"] = {"note": "进阶分析（路线/道具/团队/AI 报告）将在后续版本提供"}
    return payload


@router.get("/{demo_id}/heatmap/{mode}")
def get_heatmap(
    demo_id: int,
    mode: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """热力图 PNG。kills_all 免费；route_* 需有效 pro。"""
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")

    if mode in PRO_ONLY_HEATMAP_MODES:
        _require_subscription(current_user)

    entry = db.scalar(
        select(HeatmapCache).where(
            HeatmapCache.match_id == demo.match_id,
            HeatmapCache.mode == mode,
        )
    )
    if entry is None:
        raise ApiError(404, DEMO_NOT_FOUND, f"热力图 {mode} 不存在")
    png = storage.get_bytes(entry.heatmap_key)
    return Response(content=png, media_type="image/png")


# ---------- Phase 4B：报告页只读聚合 ----------

GRENADE_LABELS = {
    "smoke": "烟雾弹", "flash": "闪光弹", "he": "手雷",
    "molotov": "燃烧瓶", "incendiary": "燃烧弹", "decoy": "诱饵弹",
}


def _name_of(db: Session, match_id: int) -> dict[str, str]:
    """steam_id → 玩家名 安全映射（未知 steam 显示截断 id）。"""
    rows = db.scalars(select(Player).where(Player.match_id == match_id)).all()
    return {p.steam_id: p.name for p in rows}


def _clean_float(v) -> float | None:
    """NaN/Inf 坐标 → None（JSON 安全；真实 demo 可能含无效坐标）。"""
    import math

    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 1) if math.isfinite(f) else None


@router.get("/{demo_id}/kills")
def kill_timeline(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """击杀时间线（免费）：按 tick 排序，击杀者/被击杀者/武器/回合。"""
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    names = _name_of(db, demo.match_id)
    rows = db.scalars(
        select(Event).where(
            Event.match_id == demo.match_id,
            Event.event_type == "kill",
        ).order_by(Event.tick)
    ).all()
    return [
        {
            "tick": e.tick,
            "round": e.round,
            "weapon": e.weapon,
            "killer": names.get(e.actor_steam, e.actor_steam[:12]),
            "victim": names.get(e.victim_steam or "", (e.victim_steam or "")[:12]),
            "x": _clean_float(e.x),
            "y": _clean_float(e.y),
        }
        for e in rows
    ]


@router.get("/{demo_id}/grenades")
def grenade_stats(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """道具统计（需 pro）：按类型/玩家/回合聚合 + 事件列表。"""
    _require_subscription(current_user)
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    names = _name_of(db, demo.match_id)
    rows = db.scalars(
        select(Event).where(
            Event.match_id == demo.match_id,
            Event.event_type == "grenade",
        ).order_by(Event.tick)
    ).all()

    by_type: dict[str, int] = {}
    by_player: dict[str, int] = {}
    by_round: dict[int, int] = {}
    for e in rows:
        t = e.grenade_type or "other"
        by_type[t] = by_type.get(t, 0) + 1
        by_player[names.get(e.actor_steam, e.actor_steam[:12])] = by_player.get(names.get(e.actor_steam, e.actor_steam[:12]), 0) + 1
        by_round[e.round] = by_round.get(e.round, 0) + 1

    return {
        "total": len(rows),
        "by_type": [{"type": k, "label": GRENADE_LABELS.get(k, k), "count": v}
                    for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
        "by_player": [{"name": k, "count": v} for k, v in sorted(by_player.items(), key=lambda kv: -kv[1])],
        "by_round": [{"round": k, "count": v} for k, v in sorted(by_round.items())],
        "events": [
            {
                "tick": e.tick,
                "round": e.round,
                "type": e.grenade_type,
                "label": GRENADE_LABELS.get(e.grenade_type or "", e.grenade_type or ""),
                "player": names.get(e.actor_steam, e.actor_steam[:12]),
                "x": _clean_float(e.x),
                "y": _clean_float(e.y),
            }
            for e in rows
        ],
    }


@router.get("/{demo_id}/team-stats")
def team_stats(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """团队汇总（需 Max）：仅严格可计算的合计（总击杀/死亡/助攻/伤害/ADR/首杀）。"""
    from app.services.tier import require_tier

    # 对象归属先于套餐：跨用户 demo 一律 404（不泄露存在性），本人才进入档位检查
    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "max", "团队分析属于高阶版（Max）功能，升级后解锁")
    from app.services.team import require_team_demo_entitlement

    require_team_demo_entitlement(db, current_user, demo)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")

    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    stats_rows = db.scalars(select(PlayerStats).where(PlayerStats.match_id == demo.match_id)).all()
    by_player = {s.player_id: s for s in stats_rows}
    team_of = {p.id: p.team for p in players}

    teams = {"CT": {"kills": 0, "deaths": 0, "assists": 0, "damage": 0, "first_kills": 0, "players": 0, "adr_sum": 0.0},
             "T": {"kills": 0, "deaths": 0, "assists": 0, "damage": 0, "first_kills": 0, "players": 0, "adr_sum": 0.0}}
    for pid, s in by_player.items():
        team = team_of.get(pid)
        if team not in teams:
            continue
        t = teams[team]
        t["kills"] += s.kills
        t["deaths"] += s.deaths
        t["assists"] += s.assists
        t["damage"] += s.damage
        t["first_kills"] += s.first_kills
        t["players"] += 1
        t["adr_sum"] += s.adr

    match = db.get(Match, demo.match_id)
    rounds = max(match.rounds, 1) if match else 1
    out = []
    for team, t in teams.items():
        out.append({
            "team": team,
            "players": t["players"],
            "kills": t["kills"],
            "deaths": t["deaths"],
            "assists": t["assists"],
            "damage": t["damage"],
            "team_adr": round(t["adr_sum"], 1),
            "first_kills": t["first_kills"],
        })
    return {"rounds": rounds, "teams": out}


@router.get("/{demo_id}/source")
def download_demo_source(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stream the authenticated owner's original uploaded demo from private storage."""
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.source != "upload" or not SOURCE_KEY_RE.fullmatch(demo.file_path or ""):
        raise ApiError(404, DEMO_SOURCE_MISSING, "原始 demo 文件不可用")
    try:
        size = storage.stat_size(demo.file_path)
        stream = storage.iter_bytes(demo.file_path)
    except Exception as exc:  # Storage details must never escape through the API.
        raise ApiError(404, DEMO_SOURCE_MISSING, "原始 demo 文件不可用") from exc

    return StreamingResponse(
        stream,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _source_content_disposition(demo.file_name, demo.id),
            "Content-Length": str(size),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{demo_id}/export.json")
def export_demo_json(
    demo_id: int,
    sections: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载结构化报告；请求 Pro section 时整体拒绝，不做静默部分泄露。"""
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.status != "parsed" or demo.match_id is None: raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    allowed = {"match", "players", "rounds", "kills", "grenades"}
    wanted = set(s.strip() for s in sections.split(",")) if sections else allowed
    if not wanted or not wanted.issubset(allowed): raise ApiError(422, "VALIDATION_ERROR", "sections 仅支持 match,players,rounds,kills,grenades")
    if "grenades" in wanted: _require_subscription(current_user)
    match = db.get(Match, demo.match_id)
    payload = {}
    if "match" in wanted: payload["match"] = {"demo_id": demo.id, "file_name": demo.file_name, "map_name": match.map_name, "team_a_score": match.team_a_score, "team_b_score": match.team_b_score, "rounds": match.rounds, "duration_seconds": match.duration_seconds}
    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    stats = {s.player_id: s for s in db.scalars(select(PlayerStats).where(PlayerStats.match_id == demo.match_id)).all()}
    if "players" in wanted: payload["players"] = [{"player_id": p.id, "name": p.name, "team_identity": p.team_identity, "stats": {"kills": stats[p.id].kills, "deaths": stats[p.id].deaths, "assists": stats[p.id].assists} if p.id in stats else {}} for p in players]
    if "rounds" in wanted: payload["rounds"] = _round_rows(demo, db)
    events = db.scalars(select(Event).where(Event.match_id == demo.match_id).order_by(Event.tick)).all()
    by_steam = {p.steam_id: p for p in players}
    def export_event(e):
        actor, victim = by_steam.get(e.actor_steam), by_steam.get(e.victim_steam or "")
        return {"event_type": e.event_type, "tick": e.tick, "round": e.round, "weapon": e.weapon, "grenade_type": e.grenade_type, "actor": {"player_id": actor.id, "name": actor.name} if actor else None, "victim": {"player_id": victim.id, "name": victim.name} if victim else None, "x": _clean_float(e.x), "y": _clean_float(e.y), "actor_x": _clean_float(e.actor_x), "actor_y": _clean_float(e.actor_y), "victim_x": _clean_float(e.victim_x), "victim_y": _clean_float(e.victim_y)}
    if "kills" in wanted: payload["kills"] = [export_event(e) for e in events if e.event_type == "kill"]
    if "grenades" in wanted: payload["grenades"] = [export_event(e) for e in events if e.event_type == "grenade"]
    import json
    return Response(content=json.dumps(payload, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="demo-{demo.id}.json"'})


@router.get("/{demo_id}/export.xlsx")
def export_demo_xlsx(
    demo_id: int,
    sections: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """轻量 XLSX 导出；write_only workbook，列中不包含 SteamID。"""
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.status != "parsed" or demo.match_id is None: raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    allowed = {"match", "players", "rounds", "kills", "grenades"}
    wanted = set(s.strip() for s in sections.split(",")) if sections else allowed
    if not wanted or not wanted.issubset(allowed): raise ApiError(422, "VALIDATION_ERROR", "sections 仅支持 match,players,rounds,kills,grenades")
    if "grenades" in wanted: _require_subscription(current_user)
    from io import BytesIO
    from openpyxl import Workbook
    match = db.get(Match, demo.match_id)
    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    stats = {s.player_id: s for s in db.scalars(select(PlayerStats).where(PlayerStats.match_id == demo.match_id)).all()}
    events = db.scalars(select(Event).where(Event.match_id == demo.match_id).order_by(Event.tick)).all()
    wb = Workbook(write_only=True)
    def xlsx_text(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    def sheet(name, headers, rows):
        ws = wb.create_sheet(name); ws.append(headers)
        for row in rows: ws.append([xlsx_text(v) for v in row])
    if "match" in wanted: sheet("Match", ["demo_id", "file_name", "map_name", "team_a_score", "team_b_score", "rounds", "duration_seconds"], [[demo.id, xlsx_text(demo.file_name), xlsx_text(match.map_name), match.team_a_score, match.team_b_score, match.rounds, match.duration_seconds]])
    if "players" in wanted: sheet("Players", ["player_id", "name", "team_identity", "kills", "deaths", "assists"], [[p.id, xlsx_text(p.name), xlsx_text(p.team_identity), stats[p.id].kills if p.id in stats else 0, stats[p.id].deaths if p.id in stats else 0, stats[p.id].assists if p.id in stats else 0] for p in players])
    if "rounds" in wanted:
        rs = _round_rows(demo, db)
        sheet("Rounds", ["round", "side_a", "side_b", "start_tick", "end_tick", "kills_count", "grenades_count", "winner"], [[r[k] for k in ("round", "side_a", "side_b", "start_tick", "end_tick", "kills_count", "grenades_count", "winner")] for r in rs])
    by_steam = {p.steam_id: p for p in players}
    def event_row(e):
        a, v = by_steam.get(e.actor_steam), by_steam.get(e.victim_steam or "")
        return [e.tick, e.round, xlsx_text(e.weapon), a.id if a else None, xlsx_text(a.name) if a else None, v.id if v else None, xlsx_text(v.name) if v else None, _clean_float(e.x), _clean_float(e.y)]
    if "kills" in wanted: sheet("Kills", ["tick", "round", "weapon", "actor_player_id", "actor_name", "victim_player_id", "victim_name", "x", "y"], [event_row(e) for e in events if e.event_type == "kill"])
    if "grenades" in wanted: sheet("Grenades", ["tick", "round", "grenade_type", "actor_player_id", "actor_name", "x", "y"], [[e.tick, e.round, xlsx_text(e.grenade_type), by_steam[e.actor_steam].id if e.actor_steam in by_steam else None, xlsx_text(by_steam[e.actor_steam].name) if e.actor_steam in by_steam else None, _clean_float(e.x), _clean_float(e.y)] for e in events if e.event_type == "grenade"])
    out = BytesIO(); wb.save(out)
    return Response(content=out.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="demo-{demo.id}.xlsx"'})


@router.get("/{demo_id}/density/{mode}")
def density_grid(
    demo_id: int,
    mode: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """密度网格 JSON（前端 Canvas 交互渲染）。kills_all 免费；route_* 需 pro。

    与 PNG 同源：解析任务生成时同时存 MinIO（heatmaps/{match}/{mode}.json）。
    """
    import json

    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    if mode in PRO_ONLY_HEATMAP_MODES:
        _require_subscription(current_user)

    entry = db.scalar(
        select(HeatmapCache).where(
            HeatmapCache.match_id == demo.match_id,
            HeatmapCache.mode == mode,
        )
    )
    if entry is None:
        raise ApiError(404, DEMO_NOT_FOUND, f"热力图 {mode} 不存在")

    grid_key = entry.heatmap_key.replace(".png", ".json")
    if not storage.exists(grid_key):
        raise ApiError(404, DEMO_NOT_FOUND, f"密度数据 {mode} 不存在")
    data = json.loads(storage.get_bytes(grid_key).decode("utf-8"))
    data["mode"] = mode
    return data
