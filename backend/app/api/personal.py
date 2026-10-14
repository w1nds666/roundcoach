"""6R1：entitlement + focus player + 个人分析（summary / combat map）。

- 所有接口登录 + 对象权限（非本人 demo → 404）
- 完整 steam_id 绝不返回（选择列表/个人接口仅 opaque player_id）
- focus 未选择 → 稳定机器码 FOCUS_REQUIRED + 可操作提示
- personal summary / combat map 需 pro/max（tier.require_tier 集中判定）
"""
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
import hashlib
import json
import math

from app.api.demo import _get_own_demo
from app.api.deps import get_current_user
from app.database import get_db
from app.errors import DEMO_NOT_PARSED, ROUTE_UNAVAILABLE, TACTICAL_UNAVAILABLE, ApiError
from app.models import DemoFocusPlayer, Event, Match, Player, PlayerStats, RoundSide, RouteArtifact, TacticalArtifact, User
from app.services import storage as storage_module
from app.services.coordination import build_coordination_analysis
from app.services.coordination_cache import coordination_cache_get_or_build, coordination_cache_key
from app.services.area_state import build_region_state_machine_for_round
from app.services.region_geometry import load_region_geometry
from app.services.heatmap import load_maps_config
from app.services.places import translate_place
from app.services.primitives import compute_primitives
from app.services.rating_pro import build_rating
from app.services.routes import ROUTE_SCHEMA_VERSION, artifact_sha256, gunzip_json
from app.services.tactical import TACTICAL_SCHEMA_VERSION
from app.services.tier import entitlement_matrix, require_tier
from app.services.heatmap import generate_heatmap_grid
from app.services.identity import require_pro_focus
from app.services.team import require_team_demo_entitlement

router = APIRouter(prefix="/api", tags=["personal"])
FOCUS_REQUIRED = "FOCUS_REQUIRED"
FOCUS_HINT = "请先在报告页选择「我」对应的玩家（焦点玩家），再查看个人分析"


def _clean_coord(value: object) -> float | None:
    """JSON-safe coordinate: preserve legal zero, reject NaN/Inf/bad values."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, 1) if math.isfinite(value) else None


@router.get("/entitlement")
def get_entitlement(current_user: User = Depends(get_current_user)):
    """只读 entitlement（前端按服务端结果显示/锁定，不靠前端隐藏）。"""
    return entitlement_matrix(current_user)


@router.get("/demos/{demo_id}/players")
def list_players(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """玩家选择列表（opaque player_id + 名称 + 队伍；绝不返回完整 steam_id）。

    6R14：标记 is_you（绑定本人）；不泄露任何完整 SteamID。
    """
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    rows = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    stats_rows = db.scalars(select(PlayerStats).where(PlayerStats.match_id == demo.match_id)).all()
    stat_of = {s.player_id: s for s in stats_rows}
    from app.services.identity import matches_identity

    return [
        {
            "player_id": p.id,
            "name": p.name,
            "team": p.team,
            "team_identity": p.team_identity,
            "is_you": matches_identity(db, demo, current_user, p),
            "kills": stat_of[p.id].kills if p.id in stat_of else 0,
            "deaths": stat_of[p.id].deaths if p.id in stat_of else 0,
        }
        for p in rows
    ]


@router.get("/demos/{demo_id}/identity")
def demo_identity(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """demo 的身份解析结果（供前端显示「这是你/未识别到你/身份冲突/未绑定」）。

    只输出掩码 id 与 resolution，不输出完整 SteamID64。
    """
    demo = _get_own_demo(demo_id, current_user, db)
    from app.services import identity as identity_service

    status, player = identity_service.resolve_bound_focus(db, demo, current_user)
    ident = identity_service.get_active_identity(db, current_user)
    return {
        "resolution": status,
        "is_you": status == "identified",
        "you_player_id": player.id if player else None,
        "bound": ident is not None,
        "steam_id64_masked": identity_service.identity_status(db, current_user).get("steam_id64_masked"),
    }


@router.get("/demos/{demo_id}/focus-player")
def get_focus_player(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当前焦点玩家（未选择返回 null）。"""
    demo = _get_own_demo(demo_id, current_user, db)
    row = db.scalar(select(DemoFocusPlayer).where(
        DemoFocusPlayer.user_id == current_user.id,
        DemoFocusPlayer.demo_id == demo.id,
    ))
    if row is None:
        return {"player_id": None, "name": None}
    p = db.get(Player, row.player_id)
    return {"player_id": p.id, "name": p.name}


@router.put("/demos/{demo_id}/focus-player")
def set_focus_player(
    demo_id: int,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """选择「我」（幂等）：验证 player 属于该 demo 的 match、demo 属于当前用户。"""
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    player_id = int(body.get("player_id") or 0)
    player = db.get(Player, player_id)
    if player is None or player.match_id != demo.match_id:
        raise ApiError(404, "PLAYER_NOT_FOUND", "玩家不存在或不属于该比赛")

    existing = db.scalar(select(DemoFocusPlayer).where(
        DemoFocusPlayer.user_id == current_user.id,
        DemoFocusPlayer.demo_id == demo.id,
    ))
    if existing is None:
        db.add(DemoFocusPlayer(user_id=current_user.id, demo_id=demo.id, player_id=player.id))
    else:
        existing.player_id = player.id
    db.commit()
    return {"player_id": player.id, "name": player.name, "ok": True}


def _require_focus(db: Session, demo: object, user: User) -> Player:
    """取焦点玩家；未选择 → 稳定机器码 + 可操作提示。

    6R19：此函数原为「前端 focus 授权」的死代码（全库无调用点），一旦被误用会重新引入
    「改 focus 读他人画像」的身份绕过。现改为严格委托给服务端绑定身份门禁 require_pro_focus，
    即个人 Pro/Max 授权一律以「绑定 Steam 在本场 roster」为准，绝不采用前端 focus 参数。
    如确需「用户手动切双视角/看队友」，请在 require_pro_focus 已通过后，仅用于展示层选择展示对象，
    而不要改变数据主体。
    """
    from app.services.identity import require_pro_focus

    return require_pro_focus(db, demo, user)


@router.get("/demos/{demo_id}/personal/summary")
def personal_summary(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人摘要（pro/max）：K/D、交战转化率 K/(K+D)（含公式）、ADR、首杀/首死、存活回合。"""
    require_tier(current_user, "pro", "个人复盘属于进阶版功能，升级后解锁")
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    player = require_pro_focus(db, demo, current_user)

    stat = db.scalar(select(PlayerStats).where(PlayerStats.player_id == player.id))
    kills = stat.kills if stat else 0
    deaths = stat.deaths if stat else 0
    kd = round(kills / deaths, 2) if deaths else (float(kills) if kills else 0.0)
    engagement = round(kills / (kills + deaths), 3) if (kills + deaths) else 0.0

    # 6R10B：Rating Pro v1 为主指标（透明分量 + 人话解释 + 置信度/覆盖/参考范围）
    rating_pro = build_rating(stat.rating_raw if stat else None)

    # 首死：focus 作为 victim 且为该回合最早死亡事件的次数；存活回合 = 回合数 - 死亡回合数。
    # 6R10A：仅统计正式回合（round 1..match.rounds），备战/场外死亡（round=0）不计。
    match = db.get(Match, demo.match_id)
    max_round = match.rounds if match else 0
    death_events = db.scalars(select(Event).where(
        Event.match_id == demo.match_id,
        Event.event_type == "kill",
        Event.victim_steam == player.steam_id,
        Event.round >= 1,
        (Event.round <= max_round) if max_round else Event.round == -1,   # 无正式回合则查无
    )).all()
    first_deaths = 0
    death_rounds = set()
    earliest_by_round: dict[int, int] = {}
    for e in death_events:
        death_rounds.add(e.round)
        if e.round not in earliest_by_round or e.tick < earliest_by_round[e.round]:
            earliest_by_round[e.round] = e.tick
    for rnd, tick in earliest_by_round.items():
        # 该回合最早死亡事件是否为 focus（victim）
        first = db.scalar(select(Event).where(
            Event.match_id == demo.match_id,
            Event.event_type == "kill",
            Event.round == rnd,
        ).order_by(Event.tick).limit(1))
        if first is not None and first.victim_steam == player.steam_id:
            first_deaths += 1

    return {
        "player": {"player_id": player.id, "name": player.name, "team_identity": player.team_identity},
        "kills": kills,
        "deaths": deaths,
        "kd": kd,
        "engagement_rate": engagement,
        "engagement_formula": "K/(K+D)",
        "adr": round(stat.adr, 1) if stat else 0.0,
        "first_kills": stat.first_kills if stat else 0,
        "first_deaths": first_deaths,
        "rounds_alive": match.rounds - len(death_rounds),
        "rounds_total": match.rounds,
        "survival_rate": round((match.rounds - len(death_rounds)) / match.rounds, 3) if match.rounds else 0.0,
        # 6R10B：Rating Pro v1 主指标（含分量/解释/置信度/覆盖/参考范围；pro/max 可见完整，Free 展示锁定）
        "rating_pro": rating_pro,
    }


@router.get("/demos/{demo_id}/personal/rating")
def personal_rating(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人 Rating Pro v1（pro/max）：总分、版本、分量、人话解释、置信度/覆盖、参考范围、technical 口径。

    - 仅使用权威字段（击杀/首杀/多杀、存活、KAST 含可证明交易、ADR、补枪贡献）。
    - 经济影响本版未引入（无法从现字段权威证明 → unavailable，绝不补假数据）。
    - 不宣称精确复刻 HLTV Rating 2.0/3.0；reference_scope 为本批验收样本内试验性基线。
    """
    require_tier(current_user, "pro", "个人 Rating Pro 属于进阶版功能，升级后解锁")
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    player = require_pro_focus(db, demo, current_user)
    stat = db.scalar(select(PlayerStats).where(PlayerStats.player_id == player.id))
    rating = build_rating(stat.rating_raw if stat else None)
    # 补充技术口径（可展开）：公式/权重/校准常数与不可用分量原因
    from app.services.rating_pro import CAL, COMP_FORMULA, WEIGHTS

    rating["technical"] = {
        "schema_version": "rating_pro_v1",
        "weights": WEIGHTS,
        "formulas": COMP_FORMULA,
        "calibration": CAL,
        "note": "标定为验收样本内均值约 1.00，非行业/全球基线；修复前后对比见 docs/phase6r10-calibration.md",
    }
    rating["player"] = {"player_id": player.id, "name": player.name}
    return rating


@router.get("/demos/{demo_id}/personal/combat-map")
def personal_combat_map(
    demo_id: int,
    side: str = "all",          # all | CT | T（focus 玩家该回合真实阵营）
    round_no: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人交战图（pro/max）：kills[]/deaths[] 分离，含 round/tick/side/x/y/weapon。"""
    require_tier(current_user, "pro", "个人复盘属于进阶版功能，升级后解锁")
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    player = require_pro_focus(db, demo, current_user)
    if side not in ("all", "CT", "T"):
        raise ApiError(422, "VALIDATION_ERROR", "side 仅支持 all/CT/T")

    # 地图元数据（用于服务端权威 rx/ry，前端不得自行猜换算）
    match = db.get(Match, demo.match_id)
    try:
        maps = load_maps_config()
    except Exception:  # noqa: BLE001
        maps = {}
    meta = maps.get((match.map_name if match else None) or demo.map_name)

    def _px(x, y):
        if meta is None:
            return None, None
        return _route_xy(x, y, meta)

    # focus 玩家每回合真实阵营（round_sides + team_identity）
    side_by_round: dict[int, str] = {}
    rs_rows = db.scalars(select(RoundSide).where(RoundSide.match_id == demo.match_id)).all()
    for rs in rs_rows:
        side_of_focus = rs.side_a if player.team_identity == "A" else rs.side_b
        side_by_round[rs.round] = "CT" if side_of_focus == 2 else "T"

    def _side_of(e: Event) -> str | None:
        return side_by_round.get(e.round)

    kills_rows = db.scalars(select(Event).where(
        Event.match_id == demo.match_id,
        Event.event_type == "kill",
        Event.round >= 1,
        Event.actor_steam == player.steam_id,
    ).order_by(Event.tick)).all()
    deaths_rows = db.scalars(select(Event).where(
        Event.match_id == demo.match_id,
        Event.event_type == "kill",
        Event.round >= 1,
        Event.victim_steam == player.steam_id,
    ).order_by(Event.tick)).all()

    kills = []
    for e in kills_rows:
        s = _side_of(e)
        if side != "all" and s != side:
            continue
        if round_no is not None and e.round != round_no:
            continue
        x, y = _clean_coord(e.actor_x), _clean_coord(e.actor_y)
        rx, ry = _px(x, y)
        kills.append({
            "round": e.round, "tick": e.tick, "side": s,
            "x": x, "y": y, "rx": rx, "ry": ry,
            "weapon": e.weapon,
        })
    deaths = []
    for e in deaths_rows:
        s = _side_of(e)
        if side != "all" and s != side:
            continue
        if round_no is not None and e.round != round_no:
            continue
        x, y = _clean_coord(e.victim_x), _clean_coord(e.victim_y)
        rx, ry = _px(x, y)
        deaths.append({
            "round": e.round, "tick": e.tick, "side": s,
            "x": x, "y": y, "rx": rx, "ry": ry,
            "weapon": e.weapon,
        })

    return {
        "player": {"player_id": player.id, "name": player.name},
        "side_filter": side,
        "round_filter": round_no,
        "kills": kills,
        "deaths": deaths,
        "note": "kills 坐标来自攻击者位置（actor_x/y），deaths 坐标来自受害者位置（victim_x/y）；side 为该回合真实阵营",
    }


@router.get("/demos/{demo_id}/personal/combat-density")
def personal_combat_density(
    demo_id: int,
    kind: str = "kills",       # kills | deaths
    side: str = "all",         # all | CT | T
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人击杀/死亡热力图密度；复用 combat-map 事件与地图坐标转换，不新增解析数据。"""
    require_tier(current_user, "pro", "个人复盘属于进阶版功能，升级后解锁")
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    player = require_pro_focus(db, demo, current_user)
    if kind not in ("kills", "deaths") or side not in ("all", "CT", "T"):
        raise ApiError(422, "VALIDATION_ERROR", "kind 仅支持 kills/deaths，side 仅支持 all/CT/T")

    side_by_round: dict[int, str] = {}
    for rs in db.scalars(select(RoundSide).where(RoundSide.match_id == demo.match_id)).all():
        raw = rs.side_a if player.team_identity == "A" else rs.side_b
        side_by_round[rs.round] = "CT" if raw == 2 else "T"
    field = Event.actor_steam if kind == "kills" else Event.victim_steam
    coord_x = Event.actor_x if kind == "kills" else Event.victim_x
    coord_y = Event.actor_y if kind == "kills" else Event.victim_y
    rows = db.scalars(select(Event).where(
        Event.match_id == demo.match_id,
        Event.event_type == "kill",
        Event.round >= 1,
        field == player.steam_id,
    )).all()
    points = [(float(getattr(e, coord_x.key)), float(getattr(e, coord_y.key)))
              for e in rows
              if (side == "all" or side_by_round.get(e.round) == side)
              and getattr(e, coord_x.key) is not None and getattr(e, coord_y.key) is not None]
    match = db.get(Match, demo.match_id)
    map_name = match.map_name if match else demo.map_name
    if not map_name:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 地图信息缺失")
    return {**generate_heatmap_grid(points, map_name), "kind": kind, "side_filter": side}


@router.get("/demos/{demo_id}/team/combat-map")
def team_combat_map(
    demo_id: int,
    team: str = "all",          # all | A | B（队伍身份，非阵营）
    side: str = "all",          # all | CT | T（该队该回合真实阵营）
    round_no: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """团队交战图（Max）：全队击杀/死亡点位，与个人交战图（focus 玩家）数据范围隔离。

    - 个人交战图只含 focus 玩家事件；团队交战图含全部玩家（可按队伍身份 A/B 过滤），两者独立数据范围。
    - kills 坐标=攻击者位置（actor_x/y），deaths 坐标=受害者位置（victim_x/y）；side 为该队该回合真实阵营。
    """
    from app.services.tier import require_tier

    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "max", "团队交战图属于高阶版（Max）功能，升级后解锁")
    require_team_demo_entitlement(db, current_user, demo)
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    if team not in ("all", "A", "B") or side not in ("all", "CT", "T"):
        raise ApiError(422, "VALIDATION_ERROR", "team 仅支持 all/A/B，side 仅支持 all/CT/T")
    match = db.get(Match, demo.match_id)
    try:
        maps = load_maps_config()
    except Exception:  # noqa: BLE001
        maps = {}
    meta = maps.get((match.map_name if match else None) or demo.map_name)

    def _px(x, y):
        if meta is None:
            return None, None
        return _route_xy(x, y, meta)

    # steam_id → 队伍身份 / 名字 / opaque id（玩家表为本场 roster）
    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    team_of = {p.steam_id: p.team_identity for p in players}
    name_of = {p.steam_id: p.name for p in players}
    id_of = {p.steam_id: p.id for p in players}

    # round → (team_A 阵营, team_B 阵营) 的 CT/T 字符串
    side_a_of_round: dict[int, str] = {}
    side_b_of_round: dict[int, str] = {}
    for rs in db.scalars(select(RoundSide).where(RoundSide.match_id == demo.match_id)).all():
        side_a_of_round[rs.round] = "CT" if rs.side_a == 2 else "T"
        side_b_of_round[rs.round] = "CT" if rs.side_b == 2 else "T"

    def _team_side(tid: str | None, rnd: int) -> str | None:
        if tid == "A":
            return side_a_of_round.get(rnd)
        if tid == "B":
            return side_b_of_round.get(rnd)
        return None

    rows = db.scalars(select(Event).where(
        Event.match_id == demo.match_id,
        Event.event_type == "kill",
        Event.round >= 1,
    ).order_by(Event.tick)).all()

    kills = []
    deaths = []
    for e in rows:
        if round_no is not None and e.round != round_no:
            continue
        at_team = team_of.get(e.actor_steam)
        vt_team = team_of.get(e.victim_steam or "")
        # kills：按攻击者队伍过滤；deaths：按受害者队伍过滤
        if team != "all" and at_team != team:
            pass_on_kill = False
        else:
            pass_on_kill = True
        if team != "all" and vt_team != team:
            pass_on_death = False
        else:
            pass_on_death = True
        # side 过滤（该队该回合真实阵营）
        if pass_on_kill:
            side_at = _team_side(at_team, e.round)
            if side == "all" or side_at == side:
                x, y = _clean_coord(e.actor_x), _clean_coord(e.actor_y)
                rx, ry = _px(x, y)
                kills.append({
                    "round": e.round, "tick": e.tick, "side": side_at, "team": at_team,
                    "x": x, "y": y, "rx": rx, "ry": ry,
                    "weapon": e.weapon, "actor_name": name_of.get(e.actor_steam), "victim_name": name_of.get(e.victim_steam or ""),
                })
        if pass_on_death:
            side_vt = _team_side(vt_team, e.round)
            if side == "all" or side_vt == side:
                x, y = _clean_coord(e.victim_x), _clean_coord(e.victim_y)
                rx, ry = _px(x, y)
                deaths.append({
                    "round": e.round, "tick": e.tick, "side": side_vt, "team": vt_team,
                    "x": x, "y": y, "rx": rx, "ry": ry,
                    "weapon": e.weapon, "actor_name": name_of.get(e.actor_steam), "victim_name": name_of.get(e.victim_steam or ""),
                })

    return {
        "team_filter": team,
        "side_filter": side,
        "round_filter": round_no,
        "player_count": len(players),
        "kills": kills,
        "deaths": deaths,
        "note": "团队交战图与个人交战图为独立数据范围（团队含全部玩家，个人仅 focus 玩家）；kills 坐标来自攻击者位置，deaths 坐标来自受害者位置；side 为该队该回合真实阵营",
    }


@router.get("/demos/{demo_id}/team/combat-density")
def team_combat_density(
    demo_id: int,
    kind: str = "kills",          # kills | deaths
    team: str = "all",            # all | A | B
    side: str = "all",            # all | CT | T
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """团队击杀/死亡热力密度；复用团队交战图事件（含全部玩家），不新增解析数据。"""
    from app.services.tier import require_tier

    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "max", "团队交战图属于高阶版（Max）功能，升级后解锁")
    require_team_demo_entitlement(db, current_user, demo)
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    if kind not in ("kills", "deaths") or team not in ("all", "A", "B") or side not in ("all", "CT", "T"):
        raise ApiError(422, "VALIDATION_ERROR", "kind 仅支持 kills/deaths，team 仅支持 all/A/B，side 仅支持 all/CT/T")

    match = db.get(Match, demo.match_id)
    players = db.scalars(select(Player).where(Player.match_id == match.id)).all()
    team_of = {p.steam_id: p.team_identity for p in players}
    side_a_of_round: dict[int, str] = {}
    side_b_of_round: dict[int, str] = {}
    for rs in db.scalars(select(RoundSide).where(RoundSide.match_id == match.id)).all():
        side_a_of_round[rs.round] = "CT" if rs.side_a == 2 else "T"
        side_b_of_round[rs.round] = "CT" if rs.side_b == 2 else "T"

    def _team_side(tid, rnd):
        if tid == "A":
            return side_a_of_round.get(rnd)
        if tid == "B":
            return side_b_of_round.get(rnd)
        return None

    field = Event.actor_steam if kind == "kills" else Event.victim_steam
    coord_x = Event.actor_x if kind == "kills" else Event.victim_x
    coord_y = Event.actor_y if kind == "kills" else Event.victim_y
    rows = db.scalars(select(Event).where(
        Event.match_id == match.id,
        Event.event_type == "kill",
        Event.round >= 1,
    )).all()
    points = []
    for e in rows:
        tid = team_of.get(getattr(e, field.key))
        if team != "all" and tid != team:
            continue
        if side != "all" and _team_side(tid, e.round) != side:
            continue
        cx, cy = getattr(e, coord_x.key), getattr(e, coord_y.key)
        if cx is not None and cy is not None:
            points.append((float(cx), float(cy)))
    map_name = match.map_name if match else demo.map_name
    if not map_name:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 地图信息缺失")
    return {**generate_heatmap_grid(points, map_name), "kind": kind, "team_filter": team, "side_filter": side}


# ---------- 6R4：个人动态路线回放 ----------

RADAR_IMAGE_SIZE = 1024   # 雷达底图分辨率（与 heatmap.HEAT_SIZE / maps.json 换算一致）


def _route_xy(x: float | None, y: float | None, meta: dict) -> tuple[float | None, float | None]:
    """世界坐标 → 雷达归一化坐标 [0,1)（按 maps.json 规则，与服务端热力图同源）。"""
    if x is None or y is None:
        return None, None
    rx = (x - meta["pos_x"]) / meta["scale"] / RADAR_IMAGE_SIZE
    ry = (meta["pos_y"] - y) / meta["scale"] / RADAR_IMAGE_SIZE
    return round(rx, 5), round(ry, 5)


def _build_shoot_events(shoots: list[dict], px, sec) -> list[dict]:
    """把 artifact 的射击事件转换为 API 视图（rx/ry + 秒 + 已验证方向），供团队/个人复用。"""
    out = []
    for s in shoots or []:
        rx, ry = px(s.get("x"), s.get("y"))
        vrx, vry = px(s.get("victim_x"), s.get("victim_y"))
        fire = s.get("fire_tick") if s.get("fire_tick") is not None else s.get("tick")
        out.append({
            "player_id": s.get("player_id"), "victim_player_id": s.get("victim_player_id"),
            "tick": s.get("tick"), "fire_tick": s.get("fire_tick"),
            "time": sec(fire) if fire is not None else None,
            "yaw": s.get("yaw"), "pitch": s.get("pitch"),
            "rx": rx, "ry": ry, "victim_rx": vrx, "victim_ry": vry,
            "error_deg": s.get("error_deg"), "class": s.get("class"), "weapon": s.get("weapon"),
            "authority": s.get("authority"),
            "validated": bool(s.get("validated")),
            "direction_available": bool(s.get("direction_available")),
            "dir_x": s.get("dir_x"), "dir_y": s.get("dir_y"),
        })
    return out


# ---------- 6R13：击杀播报 / 真实闪光致盲 / HE 伤害视图（团队与个人共用，防两套换算漂移） ----------

def _build_killfeed_view(killfeed: list[dict], name_of: dict, team_of: dict, sec) -> list[dict]:
    """击杀播报视图：相对 freeze 秒 + 中文可读名字/队色键；raw tick/steam 只在诊断。"""
    out = []
    for k in killfeed or []:
        out.append({
            "tick": k.get("tick"), "seq": k.get("seq"),
            "time_sec": sec(k.get("tick")) if k.get("tick") is not None else None,
            "class": k.get("class"), "weapon": k.get("weapon"),
            "headshot": bool(k.get("headshot")), "thrusmoke": bool(k.get("thrusmoke")),
            "penetrated": bool(k.get("penetrated")), "self_kill": bool(k.get("self_kill")),
            "attacker_player_id": k.get("attacker_player_id"),
            "victim_player_id": k.get("victim_player_id"),
            "assist_player_id": k.get("assist_player_id"),
            "attacker_name": name_of.get(k.get("attacker_player_id")),
            "victim_name": name_of.get(k.get("victim_player_id")),
            "assist_name": name_of.get(k.get("assist_player_id")),
            "attacker_team": team_of.get(k.get("attacker_player_id")),
            "victim_team": team_of.get(k.get("victim_player_id")),
        })
    return out


def _build_blinds_view(blinds: list[dict], name_of: dict, team_of: dict, sec) -> list[dict]:
    """真实致盲视图：起止秒 + 自闪/队闪/敌闪分类 + 可读名字/队色键。"""
    out = []
    for b in blinds or []:
        start = sec(b.get("tick")) if b.get("tick") is not None else None
        duration = b.get("blind_duration")
        out.append({
            "tick": b.get("tick"), "seq": b.get("seq"), "flash_entity_id": b.get("flash_entity_id"),
            "flash_tick": b.get("flash_tick"), "blind_duration": duration,
            "start_sec": start,
            "end_sec": (start + float(duration)) if start is not None and duration is not None else None,
            "attacker_player_id": b.get("attacker_player_id"),
            "victim_player_id": b.get("victim_player_id"),
            "attacker_name": name_of.get(b.get("attacker_player_id")),
            "victim_name": name_of.get(b.get("victim_player_id")),
            "attacker_team": team_of.get(b.get("attacker_player_id")),
            "victim_team": team_of.get(b.get("victim_player_id")),
            "flash_class": b.get("flash_class"),
            "authority": b.get("authority"),
        })
    return out


def _build_he_damage_view(he_damage: list[dict], name_of: dict, team_of: dict, sec) -> list[dict]:
    """HE 受伤视图：真实 dmg_health + 关联 detonate 时刻；authority 标注 inferred。"""
    out = []
    for h in he_damage or []:
        out.append({
            "tick": h.get("tick"), "seq": h.get("seq"), "he_tick": h.get("he_tick"),
            "he_entity_id": h.get("he_entity_id"),
            "time_sec": sec(h.get("tick")) if h.get("tick") is not None else None,
            "he_time_sec": sec(h.get("he_tick")) if h.get("he_tick") is not None else None,
            "dmg_health": h.get("dmg_health"),
            "attacker_player_id": h.get("attacker_player_id"),
            "victim_player_id": h.get("victim_player_id"),
            "attacker_name": name_of.get(h.get("attacker_player_id")),
            "victim_name": name_of.get(h.get("victim_player_id")),
            "attacker_team": team_of.get(h.get("attacker_player_id")),
            "victim_team": team_of.get(h.get("victim_player_id")),
            "authority": h.get("authority"),
        })
    return out


def _route_empty_integrity(warning: str) -> dict:
    return {
        "ordered": True, "dropped_points": 0, "sampled_points": 0, "kept_points": 0,
        "coverage": 0.0, "first_tick": None, "last_tick": None,
        "sampling": {}, "warnings": [warning],
    }


def _load_route_artifact(db, demo: Match | None, match: Match) -> tuple[dict, dict]:
    """加载并校验路线 artifact（manifest/版本/MinIO/size+sha/maps）。返回 (artifact, meta)；失败抛 ROUTE_UNAVAILABLE。"""
    manifest = db.scalar(select(RouteArtifact).where(RouteArtifact.match_id == demo.match_id))
    if manifest is None:
        raise ApiError(409, ROUTE_UNAVAILABLE, "该 demo 尚无路线回放数据（旧版解析），请重新解析后再试")
    if manifest.schema_version != ROUTE_SCHEMA_VERSION:
        raise ApiError(409, ROUTE_UNAVAILABLE, "路线数据版本不兼容，请重新解析该 demo")
    try:
        data = storage_module.storage.get_bytes(manifest.object_key)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(409, ROUTE_UNAVAILABLE, "路线回放数据缺失，请重新解析该 demo") from exc
    if len(data) != manifest.size_bytes or artifact_sha256(data) != manifest.sha256:
        raise ApiError(409, ROUTE_UNAVAILABLE, "路线回放数据校验失败，请重新解析该 demo")
    try:
        artifact = gunzip_json(data)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(409, ROUTE_UNAVAILABLE, "路线回放数据损坏，请重新解析该 demo") from exc
    try:
        maps = load_maps_config()
    except Exception:  # noqa: BLE001
        maps = {}
    meta = maps.get(match.map_name or demo.map_name)
    if meta is None:
        raise ApiError(409, ROUTE_UNAVAILABLE, "该地图缺少雷达坐标配置，无法显示路线")
    return artifact, {**meta, "name": match.map_name or demo.map_name}


def _build_round_detail(r: dict, tr: dict | None, rside: str | None, meta: dict, tick_rate: float) -> dict:
    """构建单回合详情（points 带 rx/ry + 中文地点、events 聚合、integrity），供 single/half 复用。"""
    map_name = meta.get("name", "")
    points = []
    if tr:
        for p in tr["points"]:
            rx, ry = _route_xy(p.get("x"), p.get("y"), meta)
            points.append({
                "tick": p["tick"], "time": p["time"],
                "x": p.get("x"), "y": p.get("y"), "z": p.get("z"),
                "rx": rx, "ry": ry,
                "side": rside, "alive": p["alive"],
                "place": translate_place(map_name, p.get("place")),
                "events": p.get("events") or [],
                "event": p.get("event"), "source": p.get("source"),
            })

    events = {"spawn": None, "kills": [], "death": None, "round_end": None}
    for p in points:
        # 同 tick 多事件：events 数组聚合，兼容主标记 event 兜底
        evs = p.get("events") or ([p["event"]] if p.get("event") else [])
        for e in evs:
            if e == "spawn":
                events["spawn"] = {"tick": p["tick"], "x": p["x"], "y": p["y"], "rx": p["rx"], "ry": p["ry"]}
            elif e == "kill":
                events["kills"].append({"tick": p["tick"], "x": p["x"], "y": p["y"], "rx": p["rx"], "ry": p["ry"]})
            elif e == "death":
                events["death"] = {"tick": p["tick"], "x": p["x"], "y": p["y"], "rx": p["rx"], "ry": p["ry"]}
            elif e == "round_end":
                events["round_end"] = {"tick": p["tick"], "x": p["x"], "y": p["y"], "rx": p["rx"], "ry": p["ry"], "alive": p["alive"]}

    play_start = r.get("freeze_end_tick")
    return {
        "round": r["round"], "start_tick": r["start_tick"], "end_tick": r["end_tick"],
        "freeze_end_tick": play_start, "play_start_tick": play_start,
        "tick_rate": tick_rate,
        "time_unverifiable": not tick_rate,   # 权威时基不可证明 → 前端不得显示秒
        "side": rside,
        "winner_side": r.get("winner_side"), "winner_team": r.get("winner_team"),
        "outcome": tr.get("outcome", "unknown") if tr else "unknown",
        "points": points, "events": events,
        "integrity": tr.get("integrity", {}) if tr else _route_empty_integrity("该回合无轨迹数据"),
    }


def _route_round_response(artifact: dict, player: Player, round_no: int, side: str,
                          side_by_round: dict[int, str], meta: dict, match: Match) -> dict:
    r = next((x for x in artifact.get("rounds", []) if x["round"] == round_no), None)
    if r is None:
        raise ApiError(404, ROUTE_UNAVAILABLE, f"该 demo 缺少回合 {round_no} 的路线数据")
    tr = r["trajectories"].get(str(player.id))
    rside = side_by_round.get(round_no) or (tr.get("side") if tr else None)
    if side != "all" and rside != side:
        raise ApiError(404, ROUTE_UNAVAILABLE, f"焦点玩家在回合 {round_no} 不在 {side} 阵营")

    d = _build_round_detail(r, tr, rside, meta, artifact.get("tick_rate"))
    return {
        "schema_version": artifact.get("schema_version", ROUTE_SCHEMA_VERSION),
        "player": {"player_id": player.id, "name": player.name, "team_identity": player.team_identity},
        "round": {
            "number": d["round"], "start_tick": d["start_tick"], "end_tick": d["end_tick"],
            "freeze_end_tick": d.get("freeze_end_tick"), "play_start_tick": d.get("play_start_tick"),
            "side": d["side"], "winner_side": d["winner_side"], "winner_team": d["winner_team"],
            "tick_rate": d["tick_rate"],
        },
        "time_unverifiable": d.get("time_unverifiable", False),
        "side_filter": side,
        "outcome": d["outcome"],
        "map": {
            "name": match.map_name, "pos_x": meta["pos_x"], "pos_y": meta["pos_y"],
            "scale": meta["scale"], "image_size": RADAR_IMAGE_SIZE,
        },
        "points": d["points"], "events": d["events"], "integrity": d["integrity"],
        "note": "x/y/z 为世界坐标；rx/ry 为按 maps.json 换算的雷达归一化坐标，仅供渲染定位",
    }


def _route_half_response(artifact: dict, player: Player, match: Match, side: str,
                         side_by_round: dict[int, str], meta: dict) -> dict:
    """半场总览：该焦点玩家在所选阵营（CT/T）的全部回合，一次请求聚合返回（升序）。"""
    rounds_out = []
    for r in artifact.get("rounds", []):
        no = r["round"]
        rside = side_by_round.get(no)
        if rside != side:
            continue
        tr = r["trajectories"].get(str(player.id))
        d = _build_round_detail(r, tr, rside, meta, artifact.get("tick_rate"))
        rounds_out.append({
            "round": d["round"], "start_tick": d["start_tick"], "end_tick": d["end_tick"],
            "tick_rate": d["tick_rate"], "time_unverifiable": d.get("time_unverifiable", False),
            "side": d["side"], "outcome": d["outcome"],
            "points": d["points"], "events": d["events"], "integrity": d["integrity"],
        })
    rounds_out.sort(key=lambda x: x["round"])

    total = len(rounds_out)
    dead = sum(1 for x in rounds_out if x["outcome"] == "dead")
    alive = sum(1 for x in rounds_out if x["outcome"] == "alive")
    kills = sum(len(x["events"]["kills"]) for x in rounds_out)
    # 时基不可证明时不计算时长（max_duration=None），前端据此阻止显示秒
    def _dur(x):
        if not x["tick_rate"]:
            return None
        return (x["end_tick"] - x["start_tick"]) / x["tick_rate"]
    dur_vals = [d for d in (_dur(x) for x in rounds_out) if d is not None]
    max_duration = max(dur_vals) if dur_vals else None

    return {
        "schema_version": artifact.get("schema_version", ROUTE_SCHEMA_VERSION),
        "player": {"player_id": player.id, "name": player.name, "team_identity": player.team_identity},
        "map": {
            "name": match.map_name, "pos_x": meta["pos_x"], "pos_y": meta["pos_y"],
            "scale": meta["scale"], "image_size": RADAR_IMAGE_SIZE,
        },
        "side": side,
        "side_filter": side,
        "rounds": rounds_out,
        "summary": {
            "rounds": total, "alive": alive, "dead": dead,
            "survival_rate": round(alive / total, 3) if total else 0.0,
            "kills": kills,
        },
        "max_duration": round(max_duration, 2) if max_duration is not None else None,
        "note": "半场总览：仅展示 focus 玩家在 {side} 阵营的全部回合（真实换边为准）；一次请求聚合返回各回合完整轨迹".format(side=side),
    }


@router.get("/demos/{demo_id}/personal/route")
def personal_route(
    demo_id: int,
    round_no: int | None = None,
    side: str = "all",          # all | CT | T（focus 玩家该回合真实阵营过滤；half 必须 CT/T）
    view: str = "single",       # single（单回合/回合列表） | half（半场总览，聚合该阵营全部回合）
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人动态路线回放（pro/max）：复用 focus player，逐回合有序轨迹 + 事件 + 完整性。

    - view=single + round_no → 返回该回合完整轨迹；未给 round_no → 返回按 side 过滤的回合列表。
    - view=half + side=CT|T → 一次请求聚合该焦点玩家在该阵营的全部回合轨迹（升序）+ 摘要。
    - side 为该回合 focus 玩家真实阵营（换边正确，RoundSide 权威）。
    """
    # 对象归属先于档位判定：跨用户 demo 一律 404（不泄露存在性），本人才进入档位检查
    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "pro", "个人路线回放属于进阶版功能，升级后解锁")
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    player = require_pro_focus(db, demo, current_user)
    if view not in ("single", "half"):
        raise ApiError(422, "VALIDATION_ERROR", "view 仅支持 single/half")
    if side not in ("all", "CT", "T"):
        raise ApiError(422, "VALIDATION_ERROR", "side 仅支持 all/CT/T")
    if view == "half" and side not in ("CT", "T"):
        raise ApiError(422, "VALIDATION_ERROR", "半场总览需选择阵营 CT 或 T（不支持 all）")
    match = db.get(Match, demo.match_id)
    if round_no is not None and (round_no < 1 or round_no > match.rounds):
        raise ApiError(422, "VALIDATION_ERROR", "round_no 必须在有效回合范围内")

    # 加载并校验路线 artifact（manifest/版本/MinIO/size+sha/maps）
    artifact, meta = _load_route_artifact(db, demo, match)

    # focus 玩家每回合真实阵营（DB 权威）
    side_by_round: dict[int, str] = {}
    for rs in db.scalars(select(RoundSide).where(RoundSide.match_id == demo.match_id)).all():
        raw = rs.side_a if player.team_identity == "A" else rs.side_b
        if raw in (2, 3):
            side_by_round[rs.round] = "CT" if raw == 2 else "T"

    if view == "half":
        return _route_half_response(artifact, player, match, side, side_by_round, meta)

    if round_no is not None:
        return _route_round_response(artifact, player, round_no, side, side_by_round, meta, match)

    # 回合列表（供前端选择器）
    rounds_out = []
    for r in artifact.get("rounds", []):
        no = r["round"]
        tr = r["trajectories"].get(str(player.id))
        rside = side_by_round.get(no) or (tr.get("side") if tr else None)
        if side != "all" and rside != side:
            continue
        integrity = tr.get("integrity", {}) if tr else {}
        rounds_out.append({
            "round": no,
            "side": rside,
            "outcome": tr.get("outcome", "unknown") if tr else "unknown",
            "start_tick": r["start_tick"],
            "end_tick": r["end_tick"],
            "points": integrity.get("kept_points", 0),
            "coverage": integrity.get("coverage", 0.0),
            "warnings": integrity.get("warnings", ["该回合无轨迹数据"]) if tr else ["该回合无轨迹数据"],
        })
    return {"side_filter": side, "rounds": rounds_out}


@router.get("/demos/{demo_id}/place-coverage")
def place_coverage(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """人类地点词典覆盖率审计（pro）：收集 artifact 内全部 last_place_name，返回每图映射覆盖率。"""
    require_tier(current_user, "pro", "地点覆盖审计属于进阶版功能，升级后解锁")
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    match = db.get(Match, demo.match_id)
    artifact, meta = _load_route_artifact(db, demo, match)
    map_name = meta.get("name", "")
    seen: list[str | None] = []
    for r in artifact.get("rounds", []):
        for tr in r.get("trajectories", {}).values():
            for p in tr.get("points", []):
                seen.append(p.get("place"))
    from app.services.places import place_coverage

    return place_coverage(map_name, seen)


# ---------- 6R6A：团队十人单回合同步路线（Max） ----------

MAX_TEAM_PLAYERS = 10


@router.get("/demos/{demo_id}/team/route")
def team_route(
    demo_id: int,
    round_no: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """团队同步回放（Max）：单回合全部玩家有序轨迹，一次请求返回。

    - 权限：Max；对象归属先于套餐（跨用户 demo → 404 不泄露存在性）
    - 仅单回合（round_no 必填）；旧/缺失/损坏 artifact → ROUTE_UNAVAILABLE
    - 不返回 steam_id；side 以 RoundSide + A/B 身份换算（非 players.team 最终阵营）
    - 最多 10 名有效玩家；缺轨迹玩家保留状态/警告，不伪造路线
    """
    from app.services.tier import require_tier

    # 对象归属先于套餐：跨用户 demo 一律 404（不泄露存在性），本人才进入档位检查
    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "max", "团队同步回放属于高阶版（Max）功能，升级后解锁")
    require_team_demo_entitlement(db, current_user, demo)
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    match = db.get(Match, demo.match_id)
    if round_no is None:
        raise ApiError(422, "VALIDATION_ERROR", "team/route 需指定 round_no")
    if round_no < 1 or round_no > match.rounds:
        raise ApiError(422, "VALIDATION_ERROR", "round_no 必须在有效回合范围内")

    artifact, meta = _load_route_artifact(db, demo, match)
    round_meta = next((x for x in artifact.get("rounds", []) if x["round"] == round_no), None)
    if round_meta is None:
        raise ApiError(409, ROUTE_UNAVAILABLE, f"该 demo 缺少回合 {round_no} 的路线数据")
    tick_rate = artifact.get("tick_rate")   # 权威时基；None=不可证明 → 不显示秒

    # 该回合队伍 A/B 阵营（RoundSide 权威）
    rs = db.scalar(select(RoundSide).where(RoundSide.match_id == demo.match_id, RoundSide.round == round_no))
    side_a = side_b = None
    if rs is not None:
        side_a = "CT" if rs.side_a == 2 else "T"
        side_b = "CT" if rs.side_b == 2 else "T"

    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    # 稳定顺序：队伍身份（A、B）再按 player_id（升序）
    ordered = sorted(players, key=lambda p: (p.team_identity or "Z", p.id))[:MAX_TEAM_PLAYERS]

    focus_row = db.scalar(select(DemoFocusPlayer).where(
        DemoFocusPlayer.user_id == current_user.id, DemoFocusPlayer.demo_id == demo.id))
    focus_player_id = focus_row.player_id if focus_row else None

    player_out = []
    for p in ordered:
        tr = round_meta["trajectories"].get(str(p.id))
        pside = None
        if p.team_identity == "A":
            pside = side_a
        elif p.team_identity == "B":
            pside = side_b
        d = _build_round_detail(round_meta, tr, pside, meta, tick_rate)
        player_out.append({
            "player_id": p.id,
            "name": p.name,
            "team_identity": p.team_identity,
            "side": d["side"],
            "outcome": d["outcome"],
            "points": d["points"],
            "events": d["events"],
            "integrity": d["integrity"],
        })

    return {
        "schema_version": artifact.get("schema_version", ROUTE_SCHEMA_VERSION),
        "map": {
            "name": match.map_name, "pos_x": meta["pos_x"], "pos_y": meta["pos_y"],
            "scale": meta["scale"], "image_size": RADAR_IMAGE_SIZE,
        },
        "round": {
            "number": round_no, "start_tick": round_meta["start_tick"], "end_tick": round_meta["end_tick"],
            "winner_side": round_meta.get("winner_side"), "winner_team": round_meta.get("winner_team"),
            "tick_rate": tick_rate,
        },
        "tick_rate": tick_rate,
        "time_unverifiable": not tick_rate,
        "winner": round_meta.get("winner_team"),
        "max_duration": round((round_meta["end_tick"] - round_meta["start_tick"]) / tick_rate, 2) if tick_rate else None,
        "focus_player_id": focus_player_id,
        "players": player_out,
        "note": "x/y/z 为世界坐标；rx/ry 为按 maps.json 换算的雷达归一化坐标；side 为该回合真实阵营（RoundSide + A/B 身份）；仅返回最多 10 名有效玩家，缺轨迹玩家不伪造路线",
    }


# ---------- 6R8：战术回放（团队同步回放 + 真实朝向 + 道具生命周期） ----------

def _load_tactical_artifact(db, demo, match):
    """加载并校验战术 artifact（manifest/版本/MinIO/size+sha/maps）。返回 (artifact, meta)；失败抛 TACTICAL_UNAVAILABLE。"""
    manifest = db.scalar(select(TacticalArtifact).where(TacticalArtifact.match_id == demo.match_id))
    if manifest is None:
        raise ApiError(409, TACTICAL_UNAVAILABLE, "该 demo 尚无战术回放数据（旧版解析），请重新解析后再试")
    if manifest.schema_version != TACTICAL_SCHEMA_VERSION:
        raise ApiError(409, TACTICAL_UNAVAILABLE, "战术回放数据版本不兼容，请重新解析该 demo")
    try:
        data = storage_module.storage.get_bytes(manifest.object_key)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(409, TACTICAL_UNAVAILABLE, "战术回放数据缺失，请重新解析该 demo") from exc
    if len(data) != manifest.size_bytes or artifact_sha256(data) != manifest.sha256:
        raise ApiError(409, TACTICAL_UNAVAILABLE, "战术回放数据校验失败，请重新解析该 demo")
    try:
        artifact = gunzip_json(data)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(409, TACTICAL_UNAVAILABLE, "战术回放数据损坏，请重新解析该 demo") from exc
    try:
        maps = load_maps_config()
    except Exception:  # noqa: BLE001
        maps = {}
    meta = maps.get(match.map_name or demo.map_name)
    if meta is None:
        raise ApiError(409, TACTICAL_UNAVAILABLE, "该地图缺少雷达坐标配置，无法显示战术回放")
    return artifact, {**meta, "name": match.map_name or demo.map_name}


def _prop_time(sec):
    return round(sec, 2)


@router.get("/demos/{demo_id}/team/replay")
def team_replay(
    demo_id: int,
    round_no: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """团队同步回放（Max）：单回合全部玩家（含真实 yaw/pitch）+ 道具生命周期，一次请求返回。

    - 权限 Max；对象归属先于套餐（跨用户 404 不泄露存在性）
    - 仅单回合（round_no 必填）；旧/缺失/损坏 artifact → TACTICAL_UNAVAILABLE
    - 不返回 steam_id；side 以 RoundSide + A/B 身份换算（非 players.team 最终阵营）
    - 玩家点带 yaw/pitch（字段级 authority）；道具带 active_start/active_end/authority，
      烟/火只在真实有效区间出现；HE/flash 为瞬时事件
    - 原始 tick/世界坐标仅出现在技术诊断；主界面用 秒 + 中文区域 + rx/ry
    """
    from app.services.tier import require_tier

    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "max", "团队同步回放属于高阶版（Max）功能，升级后解锁")
    require_team_demo_entitlement(db, current_user, demo)
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    match = db.get(Match, demo.match_id)
    if round_no is None:
        raise ApiError(422, "VALIDATION_ERROR", "team/replay 需指定 round_no")
    if round_no < 1 or round_no > match.rounds:
        raise ApiError(422, "VALIDATION_ERROR", "round_no 必须在有效回合范围内")

    artifact, meta = _load_tactical_artifact(db, demo, match)
    round_meta = next((x for x in artifact.get("rounds", []) if x["round"] == round_no), None)
    if round_meta is None:
        raise ApiError(409, TACTICAL_UNAVAILABLE, f"该 demo 缺少回合 {round_no} 的战术数据")
    tick_rate = artifact.get("tick_rate")
    freeze = round_meta.get("freeze_end_tick") or round_meta["start_tick"]

    rs = db.scalar(select(RoundSide).where(RoundSide.match_id == demo.match_id, RoundSide.round == round_no))
    side_a = side_b = None
    if rs is not None:
        side_a = "CT" if rs.side_a == 2 else "T"
        side_b = "CT" if rs.side_b == 2 else "T"

    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    ordered = sorted(players, key=lambda p: (p.team_identity or "Z", p.id))[:10]
    name_of = {p.id: p.name for p in players}
    team_of = {p.id: p.team_identity for p in players}
    focus_row = db.scalar(select(DemoFocusPlayer).where(
        DemoFocusPlayer.user_id == current_user.id, DemoFocusPlayer.demo_id == demo.id))
    focus_player_id = focus_row.player_id if focus_row else None

    def _px(x, y):
        return _route_xy(x, y, meta)

    def _sec(tick):
        return round((tick - freeze) / tick_rate, 2) if tick_rate else None

    player_out = []
    for pl in ordered:
        pdict = round_meta["players"].get(str(pl.id))
        pside = None
        if pl.team_identity == "A":
            pside = side_a
        elif pl.team_identity == "B":
            pside = side_b
        points = []
        if pdict:
            for p in pdict.get("points", []):
                rx, ry = _px(p.get("x"), p.get("y"))
                points.append({
                    "tick": p["tick"], "time": p.get("time"),
                    "rx": rx, "ry": ry,
                    "yaw": p.get("yaw"), "pitch": p.get("pitch"),
                    "alive": p.get("alive"),
                    "place": translate_place(match.map_name, p.get("place")),
                })
        player_out.append({
            "player_id": pl.id,
            "name": pl.name,
            "team_identity": pl.team_identity,
            "side": pside,
            "outcome": pdict.get("outcome", "unknown") if pdict else "unknown",
            "points": points,
        })

    props_out = []
    for pr in round_meta.get("props", []):
        rx, ry = _px(pr.get("x"), pr.get("y"))
        traj = []
        for pt in pr.get("trajectory", []):
            txx, tyy = _px(pt.get("x"), pt.get("y"))
            traj.append({"tick": pt["tick"], "rx": txx, "ry": tyy})
        thrower = db.get(Player, pr["thrower_player_id"]) if pr.get("thrower_player_id") else None
        props_out.append({
            "type": pr["type"],
            "thrower_player_id": pr.get("thrower_player_id"),
            "thrower_name": thrower.name if thrower else None,
            "thrower_team": thrower.team_identity if thrower else None,
            "throw_tick": pr.get("throw_tick"),
            "detonate_tick": pr.get("detonate_tick"),
            "x": pr.get("x"), "y": pr.get("y"), "rx": rx, "ry": ry,
            "active_start": pr.get("active_start"),
            "active_end": pr.get("active_end"),
            "active_start_sec": _sec(pr["active_start"]) if pr.get("active_start") is not None else None,
            "active_end_sec": _sec(pr["active_end"]) if pr.get("active_end") is not None else None,
            "authority": pr.get("authority", {}),
            "trajectory": traj,
        })

    # 6R12：射击事件（枪线真值）。个人与团队共用同一 `_build_shoot_events`，禁止两套换算漂移。
    shoots_out = _build_shoot_events(round_meta.get("shoots", []), _px, _sec)

    # 6R13：击杀播报 / 真实闪光致盲 / HE 受伤（团队显示全队）
    killfeed_out = _build_killfeed_view(round_meta.get("killfeed", []), name_of, team_of, _sec)
    blinds_out = _build_blinds_view(round_meta.get("blinds", []), name_of, team_of, _sec)
    he_damage_out = _build_he_damage_view(round_meta.get("he_damage", []), name_of, team_of, _sec)

    return {
        "schema_version": artifact.get("schema_version", TACTICAL_SCHEMA_VERSION),
        "map": {"name": match.map_name, "pos_x": meta["pos_x"], "pos_y": meta["pos_y"],
                "scale": meta["scale"], "image_size": RADAR_IMAGE_SIZE},
        "round": {"number": round_no, "start_tick": round_meta["start_tick"], "end_tick": round_meta["end_tick"],
                  "freeze_end_tick": freeze, "winner_side": round_meta.get("winner_side"),
                  "winner_team": round_meta.get("winner_team"), "tick_rate": tick_rate},
        "tick_rate": tick_rate,
        "time_unverifiable": not tick_rate,
        "winner": round_meta.get("winner_team"),
        "max_duration": _sec(round_meta["end_tick"]) if tick_rate else None,
        "focus_player_id": focus_player_id,
        "players": player_out,
        "props": props_out,
        "shoots": shoots_out,
        "killfeed": killfeed_out,
        "blinds": blinds_out,
        "he_damage": he_damage_out,
        "area_state_machine": _build_area_state_machine_payload(
            [round_meta], match.map_name, tick_rate, name_of, team_of),
        "region_geometry": load_region_geometry(match.map_name),
        "gaze_gate": artifact.get("gaze_gate"),
        "gaze_counts": artifact.get("gaze_counts"),
        "note": "yaw/pitch 来自 m_angEyeAngles（字段级权威）；视角朝向为连续插值眼朝向（非移动方向）；射击线仅在 direct 可见且角误差通过门禁时可用（validated=1），否则方向不可用（direction_available=0）；烟/火仅在其真实有效区间主动出现；HE/flash 为瞬时事件；击杀播报来自真实 player_death；致盲来自 player_blind（blind_duration 为真实秒，起止与盲时长对齐）；HE 受伤为 thrower+weapon+time-window 关联（authority=inferred）；x/y/z 为世界坐标，rx/ry 为雷达归一化坐标（仅供渲染）",
    }


@router.get("/demos/{demo_id}/tactical/primitives")
def tactical_primitives(
    demo_id: int,
    round_no: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """可解释深度配合原语（Max）：每回合可追溯事件候选。

    - 每条带 round / 可读秒 / 中文区域 / 涉及玩家 / 定义 / 证据 / （候选限制）
    - 不输出"深度配合总分"；crossfire 只命名为「交叉火力候选」并写 reason（无 navmesh）。留给 6R9 标定。
    - 可由前端定位到 team/replay 的对应时刻。
    """
    from app.services.tier import require_tier

    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "max", "深度配合原语属于高阶版（Max）功能，升级后解锁")
    require_team_demo_entitlement(db, current_user, demo)
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    match = db.get(Match, demo.match_id)
    artifact, meta = _load_tactical_artifact(db, demo, match)
    if round_no is not None and (round_no < 1 or round_no > match.rounds):
        raise ApiError(422, "VALIDATION_ERROR", "round_no 必须在有效回合范围内")
    rounds_artifact = artifact.get("rounds", [])
    if round_no is not None:
        rounds_artifact = [r for r in rounds_artifact if r["round"] == round_no]

    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    id_of = {p.steam_id: p.id for p in players}
    name_of = {p.id: p.name for p in players}
    team_of = {p.id: p.team_identity for p in players}

    kills = []
    deaths = []
    for e in db.scalars(select(Event).where(Event.match_id == demo.match_id, Event.event_type == "kill", Event.round >= 1)).all():
        rec = {"round": e.round, "tick": e.tick, "actor_steam": e.actor_steam,
               "victim_steam": e.victim_steam, "actor_x": e.actor_x, "actor_y": e.actor_y,
               "victim_x": e.victim_x, "victim_y": e.victim_y,
               "actor_name": name_of.get(id_of.get(e.actor_steam), e.actor_steam[:8]),
               "victim_name": name_of.get(id_of.get(e.victim_steam or ""), (e.victim_steam or "")[:8])}
        kills.append(rec)
        deaths.append(rec)

    primitives = compute_primitives(rounds_artifact, match.map_name, artifact.get("tick_rate"),
                                    kills, deaths, id_of, name_of, team_of)
    # 玩家名展开（原语只给内部 player_id，展开为可读名）
    for p in primitives:
        p["player_names"] = name_of.get(p["players"][0], f"玩家{p['players'][0]}") if p["players"] else None
        if len(p["players"]) > 1:
            p["player_names"] = "、".join(name_of.get(i, f"玩家{i}") for i in p["players"])
    return {
        "schema_version": artifact.get("schema_version", TACTICAL_SCHEMA_VERSION),
        "map": match.map_name,
        "round_filter": round_no,
        "note": "crossfire 仅命名为「交叉火力候选」（无 navmesh 墙体遮挡验证，reason 说明）；depth score 留给 6R9 标定",
        "primitives": primitives,
    }


def _build_area_state_machine_payload(rounds_artifact, map_name, tick_rate, name_of, team_of):
    """6R16：对选中回合为每个区域构建区域控制状态机（确定性），供前端图层与证据定位。

    返回 [{round, place_key, place_cn, centroid, geometric, coverage, confidence, geometry,
           segments:{A:[...],B:[...]}, transitions:[...]}]；缺区域几何 → 空列表（诚实不绘制）。
    """
    geo = load_region_geometry(map_name)
    if not geo:
        return []
    region_list = geo.get("regions", [])
    out = []
    for rnd in rounds_artifact:
        pts_by_pid = {int(pid): pd for pid, pd in rnd.get("players", {}).items()}
        members = {"A": [p for p in pts_by_pid if team_of.get(p) == "A"],
                   "B": [p for p in pts_by_pid if team_of.get(p) == "B"]}
        props = rnd.get("props", [])
        blinds = rnd.get("blinds", [])
        for region in region_list:
            rk = region["place_key"]
            for t in ("A", "B"):
                thrower_my = set(p.get("thrower_player_id") for p in props if p.get("thrower_player_id") in members[t])
                sm = build_region_state_machine_for_round(
                    region, pts_by_pid, rnd, tick_rate, members, props, blinds, thrower_my,
                    _norm_place)
                out.append({
                    "round": rnd.get("round"), "team": t, "place_key": rk,
                    "place_cn": region.get("place_cn"),
                    "centroid": region.get("centroid"), "geometric": region.get("geometric"),
                    "coverage": region.get("coverage"), "confidence": region.get("confidence"),
                    "geometry": sm.get("geometry"),
                    "segments": sm.get("segments", {}).get(t, []),
                    "transitions": [x for x in sm.get("transitions", []) if x.get("team") == t],
                })
    return out


def _norm_place(p) -> str:
    return "".join(ch.lower() for ch in str(p or "") if ch.isalnum())


def _cache_identity_fp(players):
    """身份指纹：玩家 steam_id/id/name/team 映射（output 会用到名字与队伍标签）。改名/改标签 → 指纹变 → 失效。"""
    h = hashlib.sha256()
    for p in sorted(players, key=lambda x: (str(x.steam_id), x.id)):
        h.update(json.dumps([str(p.steam_id), p.id, p.name or "", p.team_identity or ""],
                            separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def _cache_events_fp(kills):
    """events 指纹：kills 原始字段（round/tick/steam/坐标）。Event 表变更 → 指纹变 → 失效。"""
    h = hashlib.sha256()
    for k in sorted(kills, key=lambda x: (x["round"], x["tick"], str(x["actor_steam"]), str(x["victim_steam"]))):
        h.update(json.dumps([k["round"], k["tick"], str(k["actor_steam"]), str(k["victim_steam"]),
                             k.get("actor_x"), k.get("actor_y"), k.get("victim_x"), k.get("victim_y")],
                            separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def _cache_serialize(analysis) -> bytes:
    """与 Starlette/FastAPI JSONResponse 完全一致的序列化（保证缓存字节 == 实时字节）。"""
    return json.dumps(analysis, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


@router.get("/demos/{demo_id}/coordination")
def coordination_analysis(    demo_id: int,
    round_no: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """深度配合分析（Max）：机会归一的团队指标 + 回合证据 + 个人贡献 + 结论摘要。

    - 基于战术 artifact v2（确定性生成，不重复解析 .dem），同样输入 → 同一 content_hash。
    - 机会归一：零机会 → rate=None（不可评估），绝不当作 0 分。
    - crossfire 仅命名「候选」（无 navmesh/LoS）；道具辅助仅证明时间/空间邻近。
    - 对外不返回 steam；裸 tick/世界坐标仅进技术诊断。
    - player 范围 = 仅所选 focus 玩家的贡献/暴露；团队聚合在 teams（个人决不替代团队）。
    - 建议同步在 docstring 说明与产品契约对应（docs/phase6r9-metric-contract.md）。
    """
    from app.services.tier import require_tier

    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "max", "深度配合分析属于高阶版（Max）功能，升级后解锁")
    require_team_demo_entitlement(db, current_user, demo)
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    match = db.get(Match, demo.match_id)
    artifact, meta = _load_tactical_artifact(db, demo, match)
    if round_no is not None and (round_no < 1 or round_no > match.rounds):
        raise ApiError(422, "VALIDATION_ERROR", "round_no 必须在有效回合范围内")
    rounds_artifact = artifact.get("rounds", [])
    if round_no is not None:
        rounds_artifact = [r for r in rounds_artifact if r["round"] == round_no]

    players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    id_of = {p.steam_id: p.id for p in players}
    name_of = {p.id: p.name for p in players}
    team_of = {p.id: p.team_identity for p in players}

    kills = []
    for e in db.scalars(select(Event).where(Event.match_id == demo.match_id, Event.event_type == "kill", Event.round >= 1)).all():
        kills.append({"round": e.round, "tick": e.tick, "actor_steam": e.actor_steam,
                      "victim_steam": e.victim_steam, "actor_x": e.actor_x, "actor_y": e.actor_y,
                      "victim_x": e.victim_x, "victim_y": e.victim_y,
                      "actor_name": name_of.get(id_of.get(e.actor_steam), e.actor_steam[:8]),
                      "victim_name": name_of.get(id_of.get(e.victim_steam or ""), (e.victim_steam or "")[:8])})
    deaths = [dict(k) for k in kills]   # death 由 kill 的 victim 侧派生

    focus_row = db.scalar(select(DemoFocusPlayer).where(
        DemoFocusPlayer.user_id == current_user.id, DemoFocusPlayer.demo_id == demo.id))
    focus_player_id = focus_row.player_id if focus_row else None
    team_names = {"A": match.team_a_name or "队伍 A", "B": match.team_b_name or "队伍 B"}

    # ---- E6A-COORD-CACHE：确定性缓存（key 覆盖 demo/parser/schema/算法版本 + 输入指纹；
    #      鉴权（ownership/tier/entitlement）先于查表执行，绝不缓存鉴权结果，也不跨用户串用）----
    manifest = db.scalar(select(TacticalArtifact).where(TacticalArtifact.match_id == demo.match_id))
    region_geo = load_region_geometry(match.map_name)
    tick_rate = artifact.get("tick_rate")
    cache_key = coordination_cache_key(
        demo_id=demo.id, match_id=match.id, map_name=match.map_name,
        artifact_sha=manifest.sha256 if manifest else None,
        artifact_schema=manifest.schema_version if manifest else None,
        round_no=round_no, focus_player_id=focus_player_id,
        identity_fp=_cache_identity_fp(players),
        team_names=team_names,
        region_hash=(region_geo or {}).get("content_hash"),
        events_fp=_cache_events_fp(kills),
    )

    def build():
        analysis = build_coordination_analysis(
            rounds_artifact, match.map_name, tick_rate,
            kills, deaths, id_of, name_of, team_of,
            focus_player_id=focus_player_id, team_names=team_names,
            region_geometry={match.map_name: region_geo} if region_geo else None,
        )
        # 6R16：区域控制状态机（每回合/每区域/每队），供前端"区域控制"图层与证据定位
        analysis["area_state_machine"] = _build_area_state_machine_payload(
            rounds_artifact, match.map_name, tick_rate, name_of, team_of)
        analysis["round_filter"] = round_no
        analysis["baseline_note"] = analysis.get(
            "baseline_note", "试验性基线（本批验收样本内对照，非行业基准）")
        return _cache_serialize(analysis)

    payload, _source = coordination_cache_get_or_build(cache_key, build)
    return Response(content=payload, media_type="application/json", status_code=200)


@router.get("/demos/{demo_id}/personal/replay")
def personal_replay(
    demo_id: int,
    round_no: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人战术回放（pro/max）：仅所选（focus）玩家的位置/路线 + 真实 yaw 枪线 + 其投掷道具。

    - 复用 tactical artifact v2（与团队共用同源数据），但**scope 只含焦点玩家**：
      points 只有该玩家的一条轨迹；props 只含该玩家投掷的道具生命周期。
    - 纯由 focus player 驱动：切换玩家后重新加载，不跨玩家混用缓存。
    - 烟/火为 active interval，HE/flash 为瞬时；默认时间原点 = freeze_end（跳过 freeze/buy）。
    - 不返回 steam_id；原始 tick/世界坐标仅进技术诊断。
    """
    require_tier(current_user, "pro", "个人战术回放属于进阶版功能，升级后解锁")
    demo = _get_own_demo(demo_id, current_user, db)
    if demo.match_id is None or demo.status != "parsed":
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")
    player = require_pro_focus(db, demo, current_user)
    match = db.get(Match, demo.match_id)
    artifact, meta = _load_tactical_artifact(db, demo, match)
    tick_rate = artifact.get("tick_rate")

    _all_players = db.scalars(select(Player).where(Player.match_id == demo.match_id)).all()
    name_of = {p.id: p.name for p in _all_players}
    team_of = {p.id: p.team_identity for p in _all_players}

    # 焦点玩家每回合真实阵营（RoundSide 权威）
    side_by_round: dict[int, str] = {}
    for rs in db.scalars(select(RoundSide).where(RoundSide.match_id == demo.match_id)).all():
        raw = rs.side_a if player.team_identity == "A" else rs.side_b
        if raw in (2, 3):
            side_by_round[rs.round] = "CT" if raw == 2 else "T"

    # 无 round_no：返回该玩家所有回合（供选择器），含是否有轨迹/道具
    if round_no is None:
        rounds_out = []
        for r in artifact.get("rounds", []):
            pdict = r["players"].get(str(player.id))
            pprops = [p for p in r.get("props", []) if p["thrower_player_id"] == player.id]
            rounds_out.append({
                "round": r["round"],
                "side": side_by_round.get(r["round"]),
                "outcome": pdict.get("outcome", "unknown") if pdict else "unknown",
                "has_points": bool(pdict and pdict.get("points")),
                "props": len(pprops),
            })
        rounds_out.sort(key=lambda x: x["round"])
        return {"schema_version": artifact.get("schema_version", TACTICAL_SCHEMA_VERSION),
                "map": match.map_name, "rounds": rounds_out,
                "note": "仅展示焦点玩家；道具只统计该玩家投掷"}

    if round_no < 1 or round_no > match.rounds:
        raise ApiError(422, "VALIDATION_ERROR", "round_no 必须在有效回合范围内")
    round_meta = next((x for x in artifact.get("rounds", []) if x["round"] == round_no), None)
    if round_meta is None:
        raise ApiError(409, TACTICAL_UNAVAILABLE, f"该 demo 缺少回合 {round_no} 的战术数据")
    freeze = round_meta.get("freeze_end_tick") or round_meta["start_tick"]

    def _sec(tick):
        return round((tick - freeze) / tick_rate, 2) if tick_rate else None

    def _px(x, y):
        return _route_xy(x, y, meta)

    pdict = round_meta["players"].get(str(player.id))
    pside = side_by_round.get(round_no)
    points = []
    if pdict:
        for p in pdict.get("points", []):
            rx, ry = _px(p.get("x"), p.get("y"))
            points.append({
                "tick": p["tick"], "time": p.get("time"), "rx": rx, "ry": ry,
                "yaw": p.get("yaw"), "pitch": p.get("pitch"), "alive": p.get("alive"),
                "place": translate_place(match.map_name, p.get("place")),
            })

    props_out = []
    for pr in round_meta.get("props", []):
        if pr.get("thrower_player_id") != player.id:
            continue   # 只含该玩家投掷的道具
        rx, ry = _px(pr.get("x"), pr.get("y"))
        traj = []
        for pt in pr.get("trajectory", []):
            txx, tyy = _px(pt.get("x"), pt.get("y"))
            traj.append({"tick": pt["tick"], "rx": txx, "ry": tyy})
        props_out.append({
            "type": pr["type"], "thrower_player_id": player.id, "thrower_name": player.name,
            "thrower_team": player.team_identity,
            "throw_tick": pr.get("throw_tick"), "detonate_tick": pr.get("detonate_tick"),
            "x": pr.get("x"), "y": pr.get("y"), "rx": rx, "ry": ry,
            "active_start": pr.get("active_start"), "active_end": pr.get("active_end"),
            "active_start_sec": _sec(pr["active_start"]) if pr.get("active_start") is not None else None,
            "active_end_sec": _sec(pr["active_end"]) if pr.get("active_end") is not None else None,
            "authority": pr.get("authority", {}), "trajectory": traj,
        })

    # 6R12：仅含焦点玩家的射击事件（复用团队同一换算，防漂移）
    shoots_out = _build_shoot_events(
        [s for s in round_meta.get("shoots", []) if s.get("player_id") == player.id], _px, _sec)

    # 6R13：个人 scope——击杀/致盲/HE 只含与焦点玩家相关者；道具只含其投掷。
    _killfeed = [k for k in round_meta.get("killfeed", [])
                 if k.get("attacker_player_id") == player.id or k.get("victim_player_id") == player.id]
    _blinds = [b for b in round_meta.get("blinds", [])
               if b.get("victim_player_id") == player.id or b.get("attacker_player_id") == player.id]
    _he = [h for h in round_meta.get("he_damage", [])
           if h.get("victim_player_id") == player.id or h.get("attacker_player_id") == player.id]
    killfeed_out = _build_killfeed_view(_killfeed, name_of, team_of, _sec)
    blinds_out = _build_blinds_view(_blinds, name_of, team_of, _sec)
    he_damage_out = _build_he_damage_view(_he, name_of, team_of, _sec)

    return {
        "schema_version": artifact.get("schema_version", TACTICAL_SCHEMA_VERSION),
        "map": {"name": match.map_name, "pos_x": meta["pos_x"], "pos_y": meta["pos_y"],
                "scale": meta["scale"], "image_size": RADAR_IMAGE_SIZE},
        "round": {"number": round_no, "start_tick": round_meta["start_tick"], "end_tick": round_meta["end_tick"],
                  "freeze_end_tick": freeze, "winner_side": round_meta.get("winner_side"),
                  "winner_team": round_meta.get("winner_team"), "tick_rate": tick_rate},
        "tick_rate": tick_rate,
        "time_unverifiable": not tick_rate,
        "max_duration": _sec(round_meta["end_tick"]) if tick_rate else None,
        "player": {"player_id": player.id, "name": player.name, "team_identity": player.team_identity,
                   "side": pside, "outcome": pdict.get("outcome", "unknown") if pdict else "unknown"},
        "points": points,
        "props": props_out,
        "shoots": shoots_out,
        "killfeed": killfeed_out,
        "blinds": blinds_out,
        "he_damage": he_damage_out,
        "gaze_gate": artifact.get("gaze_gate"),
        "gaze_counts": artifact.get("gaze_counts"),
        "note": "scope 仅含所选焦点玩家：位置/路线/真实朝向枪线与其投掷道具；烟/火为 active interval，HE/flash 瞬时；击杀/致盲/HE 只含与所选玩家相关者；致盲来自 player_blind（blind_duration 为真实秒）；HE 受伤为 thrower+weapon+time-window 关联（authority=inferred）；x/y 为世界坐标（仅诊断），rx/ry 为雷达归一化坐标（渲染）",
    }
