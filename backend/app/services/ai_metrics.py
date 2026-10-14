"""AI 报告指标快照构建器（Phase 5A A 项）。

- 确定性：只从已授权 demo 的数据库/现有聚合层读取，不抓页面文本
- 版本化：schema_version + metric key/source 逐项可引用
- 明确 null/unknown：尚未实现的指标（补枪率等）显式 null，严禁伪造
- input_hash：canonical JSON（sort_keys）→ sha256，支持幂等与追溯
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Demo, Event, HeatmapCache, Match, Player, PlayerStats

METRICS_SCHEMA_VERSION = "1.0"

# 尚未实现的高级指标（显式 null，禁止伪造）——对应计划书 7.3 部分指标
UNIMPLEMENTED_METRICS = [
    "team.trade_rate",          # 补枪率（需回合级击杀时序）
    "team.utility_efficiency",  # 道具效率（需闪/烟效果判定）
    "team.economy",             # 经济管理（需回合经济数据）
    "team.clutch_winrate",      # 残局胜率
    "player.route_preference",  # 路线偏好聚类（KMeans，Phase 后续）
]

METRIC_KEYS = [
    "match.map_name", "match.score_ct", "match.score_t", "match.rounds", "match.duration_seconds",
    "players.count", "teams.ct.kills", "teams.ct.deaths", "teams.ct.assists", "teams.ct.damage",
    "teams.ct.adr", "teams.ct.first_kills", "teams.t.kills", "teams.t.deaths", "teams.t.assists",
    "teams.t.damage", "teams.t.adr", "teams.t.first_kills",
    "kills.total", "kills.top_weapons", "kills.first_kills", "kills.by_round_max",
    "grenades.total", "grenades.by_type",
    "heatmaps.available",
] + UNIMPLEMENTED_METRICS


def compute_input_hash(snapshot: dict) -> str:
    """canonical JSON（sort_keys）→ sha256。"""
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_metrics_snapshot(db: Session, demo_id: int) -> dict:
    """构建版本化指标快照（仅已解析、有 match 的 demo）。"""
    demo = db.get(Demo, demo_id)
    if demo is None or demo.match_id is None:
        raise ValueError("demo 未解析完成，无法生成指标快照")

    match = db.get(Match, demo.match_id)
    players = db.scalars(select(Player).where(Player.match_id == match.id)).all()
    stats_rows = db.scalars(select(PlayerStats).where(PlayerStats.match_id == match.id)).all()
    stat_of = {s.player_id: s for s in stats_rows}
    heatmaps = db.scalars(select(HeatmapCache).where(HeatmapCache.match_id == match.id)).all()

    # 团队汇总（与 team-stats 聚合同一数据源）
    teams = {"CT": {"kills": 0, "deaths": 0, "assists": 0, "damage": 0, "adr": 0.0, "first_kills": 0},
             "T": {"kills": 0, "deaths": 0, "assists": 0, "damage": 0, "adr": 0.0, "first_kills": 0}}
    player_metrics = []
    for p in players:
        s = stat_of.get(p.id)
        if s is None:
            continue
        team = teams.setdefault(p.team, {"kills": 0, "deaths": 0, "assists": 0, "damage": 0, "adr": 0.0, "first_kills": 0})
        team["kills"] += s.kills
        team["deaths"] += s.deaths
        team["assists"] += s.assists
        team["damage"] += s.damage
        team["adr"] += s.adr
        team["first_kills"] += s.first_kills
        player_metrics.append({
            "player_index": len(player_metrics) + 1,   # 匿名序号（不输出真实 steam id）
            "team": p.team,
            "kills": s.kills,
            "deaths": s.deaths,
            "assists": s.assists,
            "hs_rate": s.hs_rate,
            "damage": s.damage,
            "adr": s.adr,
            "first_kills": s.first_kills,
            "source": "player_stats",
        })

    # 击杀/道具聚合摘要（事件表）；6R10A：仅统计正式回合（round>=1），备战/场外击杀不计。
    kills_total = db.scalar(select(Event.id).where(
        Event.match_id == match.id, Event.event_type == "kill", Event.round >= 1
    ).order_by(Event.id.desc()).limit(1))
    kill_rows = db.scalars(select(Event.weapon).where(
        Event.match_id == match.id, Event.event_type == "kill", Event.round >= 1
    )).all()
    grenade_rows = db.scalars(select(Event.grenade_type).where(
        Event.match_id == match.id, Event.event_type == "grenade", Event.round >= 1
    )).all()
    round_rows = db.scalars(select(Event.round).where(
        Event.match_id == match.id, Event.event_type == "kill", Event.round >= 1
    )).all()

    from collections import Counter

    weapon_counter = Counter(w or "unknown" for w in kill_rows)
    grenade_counter = Counter(g or "other" for g in grenade_rows)
    round_counter = Counter(r for r in round_rows)

    snapshot = {
        "schema_version": METRICS_SCHEMA_VERSION,
        # 注意：不包含任何时间字段（generated_at 由 ai_reports.created_at 承担），
        # 保证同数据 input_hash 稳定（幂等键）。
        "demo": {"id": demo.id, "file_name": demo.file_name, "map_name": match.map_name},
        "match": {
            "map_name": match.map_name,
            "score_ct": match.score_ct,
            "score_t": match.score_t,
            "rounds": match.rounds,
            "duration_seconds": match.duration_seconds,
        },
        "players": player_metrics,
        "teams": {
            "CT": {"kills": teams["CT"]["kills"], "deaths": teams["CT"]["deaths"],
                   "assists": teams["CT"]["assists"], "damage": teams["CT"]["damage"],
                   "adr": round(teams["CT"]["adr"], 1), "first_kills": teams["CT"]["first_kills"]},
            "T": {"kills": teams["T"]["kills"], "deaths": teams["T"]["deaths"],
                  "assists": teams["T"]["assists"], "damage": teams["T"]["damage"],
                  "adr": round(teams["T"]["adr"], 1), "first_kills": teams["T"]["first_kills"]},
        },
        "kills": {
            "total": len(kill_rows),
            "top_weapons": [{"weapon": w, "count": c} for w, c in weapon_counter.most_common(5)],
            "by_round_max": max(round_counter.items(), key=lambda kv: kv[1]) if round_counter else None,
        },
        "grenades": {
            "total": len(grenade_rows),
            "by_type": [{"type": t, "count": c} for t, c in grenade_counter.most_common()],
        },
        "heatmaps": {"available": [h.mode for h in heatmaps]},
        # 未实现指标：显式 null（证据契约：null ≠ 0）
        "unimplemented": {k: None for k in UNIMPLEMENTED_METRICS},
    }
    return snapshot
