"""6R15：长期 AI 教练 —— 确定性事实层服务（match/profile snapshot）。

权威依据：docs/phase6r15-coach-formula.md。
- 只纳入「解析完成 + 绑定本人确实在 roster」的 demo；身份解绑后停止新增且不可跨身份读取。
- 主体永远 = 当前登录用户的 active SteamIdentity；任何 query/body/focus/cache key 都不能切换他人。
- facts 全部来自 validated `player_stats`（基础统计）；experimental/hidden 指标不进入结论。
- evidence 的 seconds/region 由战术 artifact（若可得）计算；不可得则 seconds=None + region="未知区域"，绝不伪造。
- content_hash 确定性（规范化 sort_keys → sha256；生成两次一致）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import STEAM_IDENTITY_NOT_IN_DEMO, STEAM_IDENTITY_REQUIRED, ApiError
from app.models import (
    Demo,
    Event,
    Match,
    MatchCoachSnapshot,
    Player,
    PlayerStats,
    ProfileCoachSnapshot,
    RoundSide,
    SteamIdentity,
    TacticalArtifact,
    User,
)
from app.services import storage as storage_module
from app.services.coach_definitions import (
    CANDIDATE_BY_ID,
    CANDIDATE_PROBLEMS,
    CLUTCH_OPPORTUNITY_VALID,
    COACH_METRIC_VERSION,
    COACH_SCHEMA_VERSION,
    compute_confidence,
    confidence_score,
    impact_weight,
    magnitude_human,
    recency_score,
    repeat_score,
    sort_top3,
)
from app.services.identity import get_active_identity, resolve_bound_focus
from app.services.places import translate_place
from app.services.routes import artifact_sha256, gunzip_json
from app.services.steamid import mask_steamid64
from app.services.tactical import TACTICAL_SCHEMA_VERSION


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# metric 值键 → 候选问题（含方向），用于趋势方向判定
CANDIDATE_BY_METRIC: dict[str, dict] = {c["metric"]: c for c in CANDIDATE_PROBLEMS}


def mask_steam(steam_id64: str | None) -> str | None:
    return mask_steamid64(steam_id64) if steam_id64 else None


def _content_hash(payload: dict) -> str:
    """确定性 sha256：规范化 JSON（sort_keys），剔除 hash/generated_at/查看者视角字段。"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rounds(match: Match) -> int:
    return int(match.rounds or 0)


def _fact(value, denominator: int | None, status: str, trust: str = "validated") -> dict:
    return {"value": value, "denominator": denominator, "rate": value,
            "status": status, "trust": trust}


def _player_stats(db: Session, player: Player) -> PlayerStats | None:
    return db.scalar(select(PlayerStats).where(PlayerStats.player_id == player.id))


def _outcome(match: Match, player: Player) -> tuple[str | None, str | None]:
    """返回 (outcome win|loss|None, score "13:9" 用户:对手)。"""
    ct, t = int(match.score_ct or 0), int(match.score_t or 0)
    if player.team == "CT":
        user_score, opp_score, user_won = ct, t, ct > t
    else:
        user_score, opp_score, user_won = t, ct, t > ct
    outcome = "win" if user_won else ("loss" if ct != t else None)
    score = f"{user_score}:{opp_score}"
    return outcome, score


# ---------------- 战术 artifact（最佳努力，用于 evidence seconds/region） ----------------

def _load_tactical_artifact(db: Session, demo: Demo, match: Match) -> dict | None:
    """加载战术 artifact；缺/旧/损坏 → None（证据降级为 rounds 级，不伪造）。"""
    if demo.match_id is None:
        return None
    manifest = db.scalar(select(TacticalArtifact).where(TacticalArtifact.match_id == demo.match_id))
    if manifest is None or manifest.schema_version != TACTICAL_SCHEMA_VERSION:
        return None
    try:
        data = storage_module.storage.get_bytes(manifest.object_key)
    except Exception:  # noqa: BLE001
        return None
    if len(data) != manifest.size_bytes or artifact_sha256(data) != manifest.sha256:
        return None
    try:
        return gunzip_json(data)
    except Exception:  # noqa: BLE001
        return None


def _point_at(artifact: dict | None, round_no: int, player_id: int, tick: int) -> tuple[float | None, str | None]:
    """取该回合该玩家最近 tick 的点 → (seconds, place)。不可得 → (None, None)。"""
    if not artifact:
        return None, None
    try:
        rm = next((x for x in artifact.get("rounds", []) if int(x.get("round")) == int(round_no)), None)
        if rm is None:
            return None, None
        pdict = rm.get("players", {}).get(str(player_id))
        if not pdict:
            return None, None
        pts = pdict.get("points", [])
        if not pts:
            return None, None
        best = min(pts, key=lambda p: abs(int(p.get("tick", 0)) - int(tick)))
        return best.get("time"), best.get("place")
    except Exception:  # noqa: BLE001
        return None, None


# ---------------- match snapshot ----------------

def _build_evidence(db: Session, demo: Demo, match: Match, player: Player,
                    artifact: dict | None, map_name: str) -> list[dict]:
    """按本人参与的 kill/death 事件构建 evidence（best-effort seconds/region）。

    每条：{demo_id, round_no, seconds, region, metric_id, event_id, players, layers, highlight, value, time_precision}
    """
    out: list[dict] = []
    ident_steam = player.steam_id
    rows = db.scalars(select(Event).where(
        Event.match_id == demo.match_id,
        Event.round >= 1,
        Event.event_type.in_(["kill", "death"]),
        Event.actor_steam == ident_steam,
    )).all()
    # 若以 actor=me 的事件为锚（击杀）；也辅以死亡（victim=me）作为阵亡证据
    for ev in rows:
        seconds, place = _point_at(artifact, int(ev.round), int(player.id), int(ev.tick))
        region = translate_place(map_name, place) if place else "未知区域"
        out.append({
            "demo_id": demo.id,
            "round_no": int(ev.round),
            "seconds": seconds,
            "region": region,
            "metric_id": "kill",   # 由 profile 聚合按问题归类替换
            "event_id": int(ev.id),
            "players": [int(player.id)],
            "layers": ["route", "gun"],
            "highlight": [int(player.id)],
            "value": _point_value(ev),
            "time_precision": "seconds" if seconds is not None else "round",
        })
    # 死亡锚（victim=me），去重避免与击杀重复 event
    death_rows = db.scalars(select(Event).where(
        Event.match_id == demo.match_id,
        Event.round >= 1,
        Event.event_type == "death",
        Event.victim_steam == ident_steam,
    )).all()
    seen = {e["event_id"] for e in out}
    for ev in death_rows:
        if int(ev.id) in seen:
            continue
        seconds, place = _point_at(artifact, int(ev.round), int(player.id), int(ev.tick))
        region = translate_place(map_name, place) if place else "未知区域"
        out.append({
            "demo_id": demo.id,
            "round_no": int(ev.round),
            "seconds": seconds,
            "region": region,
            "metric_id": "death",
            "event_id": int(ev.id),
            "players": [int(player.id)],
            "layers": ["route", "gun"],
            "highlight": [int(player.id)],
            "value": _point_value(ev),
            "time_precision": "seconds" if seconds is not None else "round",
        })
    return out


def _point_value(ev: Event) -> float:
    """事件锚值：击杀=1，死亡=0（仅作定位参考，不参与指标计算）。"""
    return 1.0 if getattr(ev, "event_type", None) == "kill" else 0.0


def _compute_match_facts(stats: PlayerStats, match: Match) -> dict:
    rounds = _rounds(match)
    kills = int(stats.kills or 0)
    deaths = int(stats.deaths or 0)
    facts = {}
    facts["adr"] = _fact(round(float(stats.adr or 0), 1), rounds, "ok" if rounds > 0 else "no_opportunity")
    kd = (round(kills / deaths, 3) if deaths > 0 else None)
    facts["kd_ratio"] = _fact(kd, (kills + deaths) if (kills + deaths) > 0 else None,
                              "ok" if deaths > 0 else "insufficient")
    facts["deaths_per_round"] = _fact(round(deaths / rounds, 3), rounds, "ok" if rounds > 0 else "no_opportunity")
    facts["hs_rate"] = _fact(round(float(stats.hs_rate or 0), 3), kills, "ok" if kills > 0 else "no_opportunity")
    facts["first_kill_rate"] = _fact(round(int(stats.first_kills or 0) / rounds, 3), rounds,
                                     "ok" if rounds > 0 else "no_opportunity")
    # clutches_won：无法证明实际残局机会 → 置 no_opportunity（不当作 0 分）
    facts["clutches_won"] = _fact(int(stats.clutches_won or 0), None, "no_opportunity")
    return facts


def _hits_from_facts(facts: dict) -> list[str]:
    hits = []
    for c in CANDIDATE_PROBLEMS:
        cid = c["id"]
        if cid == "low_clutch" and not CLUTCH_OPPORTUNITY_VALID:
            continue
        f = facts.get(c["metric"])
        if f is None or f.get("status") != "ok":
            continue
        v = f.get("rate")
        if v is None:
            continue
        thr = c["threshold"]
        if c["direction"] == "low" and v < thr:
            hits.append(cid)
        elif c["direction"] == "high" and v > thr:
            hits.append(cid)
    return hits


def compute_match_snapshot(db: Session, user: User, demo: Demo) -> MatchCoachSnapshot:
    """生成/更新一场的 match coach snapshot。若该 demo 不含绑定本人 → 抛 STEAM_IDENTITY_REQUIRED / not_in_demo。"""
    ident = get_active_identity(db, user)
    if ident is None:
        raise ApiError(403, STEAM_IDENTITY_REQUIRED, "请先绑定 Steam 后再使用长期教练")
    status, player = resolve_bound_focus(db, demo, user)
    if status != "identified" or player is None:
        raise ApiError(403, STEAM_IDENTITY_NOT_IN_DEMO, "这场没有识别到你，无法计入长期画像")
    if demo.match_id is None:
        raise ApiError(409, "DEMO_NOT_PARSED", "demo 尚未解析完成")
    match = db.get(Match, demo.match_id)
    stats = _player_stats(db, player)
    if stats is None:
        # 无该玩家统计 → 不纳入（诚实，不补假数据）
        raise ApiError(409, "DEMO_NOT_PARSED", "该场缺少本人统计数据")

    facts = _compute_match_facts(stats, match)
    hits = _hits_from_facts(facts)
    artifact = _load_tactical_artifact(db, demo, match)
    evidence = _build_evidence(db, demo, match, player, artifact, match.map_name or demo.map_name)
    outcome, score = _outcome(match, player)

    payload = {
        "steam_id64": ident.steam_id64,
        "demo_id": demo.id,
        "match_id": demo.match_id,
        "map_name": match.map_name or demo.map_name,
        "outcome": outcome,
        "score": score,
        "rounds": _rounds(match),
        "match_at": (demo.created_at or _now()).isoformat(),
        "schema_version": COACH_SCHEMA_VERSION,
        "metric_version": COACH_METRIC_VERSION,
        "facts": facts,
        "hits": hits,
        "evidence": evidence,
        "coverage": {"total_rounds": _rounds(match), "evidence_count": len(evidence)},
    }
    chash = _content_hash(payload)

    row = db.scalar(select(MatchCoachSnapshot).where(
        MatchCoachSnapshot.steam_id64 == ident.steam_id64,
        MatchCoachSnapshot.demo_id == demo.id,
        MatchCoachSnapshot.metric_version == COACH_METRIC_VERSION,
    ))
    if row is None:
        row = MatchCoachSnapshot(
            steam_id64=ident.steam_id64, demo_id=demo.id, match_id=demo.match_id,
            map_name=match.map_name or demo.map_name, outcome=outcome, score=score,
            rounds=_rounds(match), match_at=demo.created_at or _now(),
            schema_version=COACH_SCHEMA_VERSION, metric_version=COACH_METRIC_VERSION,
            content_hash=chash, coverage=payload["coverage"], facts=facts, evidence=evidence,
        )
        db.add(row)
    else:
        # 幂等：仅当数据变化（hash 不同）才更新
        if row.content_hash != chash:
            row.facts, row.evidence, row.content_hash, row.coverage = facts, evidence, chash, payload["coverage"]
            row.outcome, row.score, row.rounds = outcome, score, _rounds(match)
    db.commit()
    db.refresh(row)
    return row


# ---------------- profile snapshot ----------------

def _eligible_matches(db: Session, user: User, ident: SteamIdentity) -> list[tuple[Demo, Match, Player]]:
    """本人（绑定 steam）确实在 roster 且已解析的 demos，按时间升序。"""
    rows = db.execute(
        select(Demo, Match, Player)
        .join(Match, Match.id == Demo.match_id)
        .join(Player, Player.match_id == Match.id)
        .where(Player.steam_id == ident.steam_id64, Demo.status == "parsed", Demo.match_id.isnot(None))
        .distinct()
        .order_by(Demo.created_at.asc(), Match.id.asc())
    ).all()
    return [(d, m, p) for d, m, p in rows]


def _profile_coverage(n: int, window: int) -> dict:
    return {"available": n, "max": window, "fraction": round(n / window, 2) if window else 0.0}


def _wind(matches: list, window: int) -> list:
    return matches[-max(1, min(window, len(matches))):] if matches else []


def _trend_for(metric_key: str, window_facts: list[dict]) -> dict:
    """窗口内该指标前后半段趋势。样本不足 → status=insufficient。"""
    vals = [f.get(metric_key, {}).get("rate") for f in window_facts if f.get(metric_key, {}).get("status") == "ok"]
    vals = [v for v in vals if v is not None]
    n = len(vals)
    half = n // 2
    if n < 4 or half < 2:
        cand = CANDIDATE_BY_METRIC.get(metric_key)
        return {"status": "insufficient", "metric": metric_key,
                "candidate_id": cand["id"] if cand else None,
                "human_name": cand["human_name"] if cand else metric_key,
                "previous": None, "current": None, "delta": None, "trend": None, "magnitude": None}
    prev = sum(vals[:half]) / half
    cur = sum(vals[n - half:]) / half
    delta = cur - prev
    base = max(abs(prev), 1.0)
    flat = abs(delta) < 0.05 * base
    if flat:
        trend = "flat"
    else:
        cand = CANDIDATE_BY_METRIC.get(metric_key)
        if cand and cand["direction"] == "high":
            # bad-when-high：下降=改善
            trend = "improving" if delta < 0 else "regressing"
        else:
            # bad-when-low：上升=改善
            trend = "improving" if delta > 0 else "regressing"
    return {"status": "ok", "previous": round(prev, 3), "current": round(cur, 3),
            "delta": round(delta, 3), "trend": trend, "magnitude": magnitude_human(abs(delta), prev)}


def profile_snapshot(db: Session, user: User, window: int = 30) -> ProfileCoachSnapshot:
    """聚合长期画像。window ∈ {10,30}（5 供参考）。"""
    ident = get_active_identity(db, user)
    if ident is None:
        raise ApiError(403, STEAM_IDENTITY_REQUIRED, "请先绑定 Steam 后再使用长期教练")

    eligible = _eligible_matches(db, user, ident)
    n = len(eligible)
    # 三窗口覆盖（5/10/30），供前端展示真实覆盖
    windows_data = {
        "5": _profile_coverage(min(n, 5), 5),
        "10": _profile_coverage(min(n, 10), 10),
        "30": _profile_coverage(min(n, 30), 30),
    }

    window_matches = _wind(eligible, window)
    match_snaps: list[MatchCoachSnapshot] = []
    window_facts: list[dict] = []
    for d, m, p in window_matches:
        try:
            snap = compute_match_snapshot(db, user, d)
            match_snaps.append(snap)
            window_facts.append(snap.facts)
        except Exception:  # noqa: BLE001  跳过无数据/不可用单场（不纳入）
            continue

    # 重复问题（hits>=2）
    repeated = []
    metric_keys = ["adr", "deaths_per_round", "hs_rate", "kd_ratio", "first_kill_rate"]
    m = len(window_facts)
    for c in CANDIDATE_PROBLEMS:
        cid = c["id"]
        if cid == "low_clutch" and not CLUTCH_OPPORTUNITY_VALID:
            continue
        key = c["metric"]
        hit_facts = [f for f in window_facts if _hit(f, c)]
        hits = len(hit_facts)
        valid = sum(1 for f in window_facts if f.get(key, {}).get("status") == "ok")
        conf = compute_confidence(None, hits, valid, m or 1)
        repeated.append({
            "candidate_id": cid, "human_name": c["human_name"], "metric": key,
            "hits": hits, "matches": m, "rate": round(hits / m, 3) if m else 0.0,
            "coverage": round(valid / m, 3) if m else 0.0, "confidence": conf,
            "impact_weight": c["impact_weight"],
            "repeat": hits >= 2,
            "detail": f"{c['human_name']}：最近 {m} 场有 {hits} 场命中（{round(hits / m, 2) if m else 0}）。",
        })

    # 趋势
    trends = []
    for key in metric_keys:
        t = _trend_for(key, window_facts)
        cand = CANDIDATE_BY_METRIC.get(key)
        if cand:
            t.setdefault("candidate_id", cand["id"])
            t.setdefault("human_name", cand["human_name"])
        trends.append(t)

    # Top3
    top3 = []
    for r in repeated:
        if not r["repeat"]:
            continue
        hit_indices = [i for i, f in enumerate(window_facts) if _hit(f, CANDIDATE_BY_ID[r["candidate_id"]])]
        rec = recency_score(hit_indices, m)
        score = impact_weight(r["candidate_id"]) * repeat_score(r["hits"], m) * \
            confidence_score(r["confidence"]) * rec
        cand = CANDIDATE_BY_ID[r["candidate_id"]]
        # 该问题在命中场次的证据数
        ev_count = sum(
            len([e for e in snap.evidence if e.get("metric_id") in ("kill", "death")])
            for i, snap in enumerate(match_snaps) if _hit(window_facts[i], cand)
        )
        top3.append({
            "rank": 0, "candidate_id": r["candidate_id"], "human_name": cand["human_name"],
            "score": round(score, 4), "impact": cand["impact_weight"], "repeat": r["rate"],
            "confidence": r["confidence"], "recency": round(rec, 3),
            "why": cand["impact_human"], "advice": cand["advice"],
            "evidence_count": ev_count,
            "evidence": _problem_evidence(match_snaps, window_facts, cand),
        })
    ranked = sort_top3(top3)
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    # 摘要 + 确定性中文正文 + 状态
    summary, states, det_text = _build_text(ranked, trends, windows_data, m, ident, window)
    content = {
        "steam_id64": ident.steam_id64, "window": window,
        "schema_version": COACH_SCHEMA_VERSION, "metric_version": COACH_METRIC_VERSION,
        "coverage": {"window_10": windows_data["10"], "window_30": windows_data["30"],
                     "window_5": windows_data["5"], "total_matches_available": n},
        "windows": {"5": windows_data["5"], "10": windows_data["10"], "30": windows_data["30"],
                    "selected": window, "matches": [s.demo_id for s in match_snaps]},
        "repeated_problems": repeated,
        "trends": trends,
        "top3": ranked,
        "summary": summary,
        "states": states,
        "rendered": {"deterministic": det_text, "ai": {"enabled": False, "body": None, "status": "unavailable"}},
    }
    chash = _content_hash(content)

    row = db.scalar(select(ProfileCoachSnapshot).where(
        ProfileCoachSnapshot.steam_id64 == ident.steam_id64,
        ProfileCoachSnapshot.metric_version == COACH_METRIC_VERSION,
        ProfileCoachSnapshot.window == window,
    ))
    if row is None:
        row = ProfileCoachSnapshot(
            steam_id64=ident.steam_id64, window=window, schema_version=COACH_SCHEMA_VERSION,
            metric_version=COACH_METRIC_VERSION, content_hash=chash, coverage=content["coverage"],
            windows=content["windows"], repeated_problems=repeated, trends=trends, top3=ranked,
            summary=summary, states=states, rendered=content["rendered"],
        )
        db.add(row)
    else:
        if row.content_hash != chash:
            row.coverage, row.windows, row.repeated_problems, row.trends, row.top3 = (
                content["coverage"], content["windows"], repeated, trends, ranked)
            row.summary, row.states, row.rendered, row.content_hash = summary, states, content["rendered"], chash
    db.commit()
    db.refresh(row)
    return row


def _hit(facts: dict, cand: dict) -> bool:
    f = facts.get(cand["metric"])
    if f is None or f.get("status") != "ok":
        return False
    v = f.get("rate")
    if v is None:
        return False
    if cand["direction"] == "low":
        return v < cand["threshold"]
    return v > cand["threshold"]


def _problem_evidence(match_snaps: list, window_facts: list, cand: dict) -> list[dict]:
    """该问题在命中场次对应的证据锚（最多 3 条）。"""
    out = []
    for i, snap in enumerate(match_snaps):
        if not _hit(window_facts[i], cand):
            continue
        for e in snap.evidence:
            if e.get("metric_id") in ("kill", "death"):
                item = dict(e)
                item["metric_id"] = cand["id"]
                out.append(item)
                break
        if len(out) >= 3:
            break
    return out


# ---------------- 中文表达 ----------------

def _build_text(top3: list[dict], trends: list[dict], windows_data: dict, n: int,
                ident: SteamIdentity, window: int = 10) -> tuple[str, dict, str]:
    k10 = windows_data["10"]["available"] if windows_data.get("10") else 0
    k30 = windows_data["30"]["available"] if windows_data.get("30") else 0
    states = {"status": "ok", "reasons": []}
    if n == 0:
        states = {"status": "empty", "reasons": ["还没有足够的已完成复盘来生成画像"]}
        summary = "还没有足够的已完成复盘来生成画像。"
        det = summary
        return summary, states, det
    if n < window:
        states["status"] = "underfilled"
        states["reasons"].append(f"当前样本较少（{n}/{window}），判断仅供参考")
    if not top3:
        summary = f"最近 {n} 场未见重复问题；样本越多，判断越准确。"
    else:
        t = top3[0]
        summary = (f"你已经完成 {k10}/10、{k30}/30 场复盘；最近最值得改的是"
                   f"「{t['human_name']}」（{round(t['repeat'] * 100)}% 的场次命中）。")

    lines = []
    lines.append(f"## 摘要")
    lines.append(summary)
    if trends:
        lines.append("")
        lines.append("## 趋势")
        for t in trends:
            if t.get("status") != "ok":
                lines.append(f"- {t.get('human_name', t['metric'])}：尚无法判断（样本不足）")
            else:
                label = {"improving": "改善", "flat": "持平", "regressing": "退步"}.get(t["trend"], t["trend"])
                lines.append(f"- {t['human_name']}：{label}（前 {round(t['previous'] * 100)}% → 后 {round(t['current'] * 100)}%，{t['magnitude']}）")
    if top3:
        lines.append("")
        lines.append("## 最值得改的 3 件事")
        for t in top3:
            lines.append(f"- {t['rank']}. {t['why']} 下一步：{t['advice']}（证据 {t['evidence_count']} 条）")
    return summary, states, "\n".join(lines)
