"""6R8：战术回放 artifact v2.0（团队同步回放 + 道具生命周期 + 真实朝向）。

- 独立 artifact（与 6R4 route artifact 1.2 共存，不破坏既有 /personal/route、/team/route）。
- 玩家采样：pro | team | alive | x/y/z（仅内部） | last_place_name | **真实 yaw/pitch**
  （来源 `CCSPlayerPawn.m_angEyeAngles` = [pitch, yaw, roll]，字段级 authority，见
  docs/phase6r8-field-authority.md）。**枪线只能由 yaw/pitch 计算，绝不拿移动方向冒充**。
- 道具：thrower / type / throw tick+轨迹 / detonate tick+位置 / active_start / active_end / authority。
  - 烟 = smokegrenade_detonate → smokegrenade_expired（真实事件）；火 = inferno_startburn → inferno_expire；
    HE/flash = 瞬时 detonate。
  - 烟/火结束事件为**真实事件**，非派生；缺失配对 → active_end=null + authority=missing_expire（前端 fail closed）。
  - 轨迹取自 parse_grenades 抛射体实体（*Projectile）按连续 tick 段切分（规避 entity-id 复用），
    以「投掷者 + 时间窗」关联到 detonate 事件（关联 authority=inferred）。
- 权威时基 tick_rate 来自 CDemoFileInfo；None → 不显示秒（API 层处理）。
"""
from __future__ import annotations

import math

from app.services.parser import CS2_TICK_RATE, read_cdemo_file_info
from app.services.gaze import build_shoot_events as gaze_build_shoot_events, classify_death
from app.services.routes import _clean, _round_mapper, gzip_json, gunzip_json

TACTICAL_SCHEMA_VERSION = "3.0"

# 生命周期的真实事件名（字段级 authority，已跨 3 场真实 demo 验证）
SMOKE_START = "smokegrenade_detonate"
SMOKE_END = "smokegrenade_expired"
FIRE_START = "inferno_startburn"
FIRE_END = "inferno_expire"
HE_DETONATE = "hegrenade_detonate"
FLASH_DETONATE = "flashbang_detonate"

# 抛射体实体类型 → 道具类型（轨迹来源）
PROJECTILE_TYPES = ("CSmokeGrenadeProjectile", "CFlashbangProjectile", "CHEGrenadeProjectile",
                    "CMolotovProjectile", "CIncendiaryGrenadeProjectile", "CDecoyProjectile")

# 抛射体段切分阈值（tick）：下一点与上一点间隔 > 该值视为"实体 id 被复用"的开始新段
SEG_GAP_TICKS = 64
# 抛射体段 → detonate 事件的匹配窗口（相对起飞/落点）
SEG_FLOAT_WINDOW = 256
# 抛射体轨迹降采样目标点数（避免 artifact 膨胀）
TRAJECTORY_MAX_POINTS = 32

# 道具中文标签（供前端图例/摘要，不暴露原始 tick）
PROP_LABELS = {"smoke": "烟雾", "fire": "燃烧", "he": "手雷", "flash": "闪光"}


def _tick_span_ok(x) -> float | None:
    return _clean(x)


def _segment_projectile_trajectories(grenades_rows: list[dict]) -> list[dict]:
    """从 parse_grenades 抛射体实体切出单次投掷段（规避 entity-id 复用）。

    返回 [{type, steamid, t0, t1, points:[{tick,x,y,z}], origin:{x,y,z}}]。
    """
    # 仅保留抛射体实体
    proj = [g for g in grenades_rows if str(g.get("grenade_type", "")).endswith("Projectile")]
    # 按实体 id 分组
    by_entity: dict[int, list[dict]] = {}
    for g in proj:
        by_entity.setdefault(g["grenade_entity_id"], []).append(g)
    segments: list[dict] = []
    for eid, rows in by_entity.items():
        rows.sort(key=lambda r: r["tick"])
        # 切连续段
        runs: list[list[dict]] = []
        cur: list[dict] = []
        for r in rows:
            if cur and int(r["tick"]) - int(cur[-1]["tick"]) > SEG_GAP_TICKS:
                if cur:
                    runs.append(cur)
                cur = []
            cur.append(r)
        if cur:
            runs.append(cur)
        for run in runs:
            if not run:
                continue
            coords = [r for r in run if r.get("x") is not None and r.get("y") is not None]
            if not coords:
                continue
            # 降采样轨迹
            step = max(1, len(run) // TRAJECTORY_MAX_POINTS)
            pts = [{"tick": int(r["tick"]), "x": _clean(r.get("x")), "y": _clean(r.get("y")), "z": _clean(r.get("z"))}
                   for r in run[::step]]
            segments.append({
                "type": str(run[0]["grenade_type"]),
                "steamid": str(run[0].get("steamid") or ""),
                "t0": int(run[0]["tick"]),
                "t1": int(run[-1]["tick"]),
                "points": pts,
                "origin": {"x": _clean(run[0].get("x")), "y": _clean(run[0].get("y")), "z": _clean(run[0].get("z"))},
            })
    return segments


def _match_segment(segments: list[dict], thrower: str, detonate_tick: int) -> dict | None:
    """按「投掷者 + 时间窗」把抛射体段关联到 detonate 事件（关联 authority=inferred）。"""
    for s in segments:
        if s["steamid"] != thrower:
            continue
        if s["t0"] - SEG_GAP_TICKS <= detonate_tick <= s["t1"] + SEG_FLOAT_WINDOW:
            return s
    return None


def _next_end(sorted_ticks: list[int] | None, detonate_tick: int) -> int | None:
    """取该 detonate 之后最近的同 entityid 结束 tick（规避实体 id 复用的跨投掷污染）。"""
    if not sorted_ticks:
        return None
    import bisect
    i = bisect.bisect_right(sorted_ticks, detonate_tick)
    return sorted_ticks[i] if i < len(sorted_ticks) else None


# 道具生命周期合理性上限（秒）：烟 ~18s、火 ~7s，留足余量；超过视为实体 id 复用的跨投掷污染
# → 结束事件证据不足 → missing_expire（前端 fail closed，不无限期显示、不编造时长）。
MAX_PROP_SECONDS = 45.0


def _clamped_end(end: int | None, start_tick: int, tick_rate: float | None) -> int | None:
    """若结束 tick 距离 detonate 超过 MAX_PROP_SECONDS（或无法换算时前后无合理间距），视为缺证。"""
    if end is None or not tick_rate:
        return end
    if (end - start_tick) > MAX_PROP_SECONDS * tick_rate:
        return None
    return end


def _prop_type_from_event(ev_name: str) -> str:
    return {"smokegrenade_detonate": "smoke", "inferno_startburn": "fire",
            "hegrenade_detonate": "he", "flashbang_detonate": "flash"}.get(ev_name, "other")


def _authority(kind: str, extra: str | None = None) -> dict:
    a = {"kind": "event", "source": kind} if kind != "inferred" else {"kind": "inferred", "source": "thrower+time-window"}
    if extra:
        a["detail"] = extra
    return a


def _parse_event_rows(analyzer, ev: str) -> list[dict]:
    try:
        return analyzer._parser.parse_event(ev).to_dict(orient="records")
    except Exception:  # noqa: BLE001
        return []


# ---- 6R13：击杀播报 / 投掷归属 / 真实闪光致盲 / HE 伤害 ----

FIREARM_OR_GENADE_OK = True
# player_blind 与 flashbang_detonate 的关联窗口（tick）：勘探实测 5 demo 100% 命中
BLIND_LINK_WINDOW = 256
# HE 受伤与 hegrenade_detonate 的关联窗口（tick，含爆炸前一点 + 爆炸后伤害结算窗口）
HE_DAMAGE_WINDOW = 128
# 击杀播报排序的稳定 tie-breaker 增量用：(tick, seq) 稳定
HE_WEAPONS = {"hegrenade", "weapon_hegrenade"}


def build_killfeed(deaths: list[dict], player_id_of: dict[str, int], rounds: list[dict]) -> dict[int, list[dict]]:
    """从真实 player_death 行构建每回合击杀播报事件。

    - 分类复用 gaze.classify_death：direct / wallbang / thrusmoke / grenade / knife / suicide。
    - 只与玩家表内直接索引用 player_id_of 映射；无法映射的攻击者/受害者/助攻者 → 对应 id=None，
      由 API 层诚实显示"未知"（绝不伪造姓名/队伍）。
    - 排序按 (tick, seq) 稳定 tie-breaker（seq 为输入行序号），保证同输入同输出。
    - 只返回落在正式回合窗口内的击杀；回合外（备战/热身/刀局）不进入播报。
    """
    mapper = _round_mapper(rounds)
    out: dict[int, list[dict]] = {}
    for seq, d in enumerate(deaths):
        tick = int(d.get("tick") or 0)
        rnd = mapper(tick)
        if rnd is None:
            continue
        attacker = str(d.get("attacker_steamid") or "")
        victim = str(d.get("user_steamid") or "")
        assister = str(d.get("assister_steamid") or "")
        item = {
            "tick": tick,
            "seq": seq,
            "class": classify_death(d),
            "weapon": str(d.get("weapon") or ""),
            "headshot": bool(d.get("headshot")),
            "thrusmoke": bool(d.get("thrusmoke")),
            "penetrated": bool(d.get("penetrated")),
            "self_kill": bool(attacker and attacker == victim),
            "attacker_player_id": player_id_of.get(attacker),
            "victim_player_id": player_id_of.get(victim) if victim else None,
            "assist_player_id": player_id_of.get(assister) if assister else None,
        }
        out.setdefault(rnd, []).append(item)
    for rnd in out:
        out[rnd].sort(key=lambda e: (e["tick"], e["seq"]))
    return out


def build_blinds(blind_rows: list[dict], flash_rows: list[dict], player_id_of: dict[str, int],
                 identity_of: dict[str, str], rounds: list[dict], tick_rate: float | None,
                 window: int = BLIND_LINK_WINDOW) -> dict[int, list[dict]]:
    """真实闪光致盲：player_blind 按 entityid + 时间窗关联 flashbang_detonate。

    - player_blind 受害者/攻击者(投掷者)/blind_duration(秒，真实事件字段)都为 authoritative。
    - self/team/enemy 分类：victim==attacker → self；同队伍身份(A/B) → team；否则 enemy；身份缺失 → unknown。
    - blind_end_tick = tick + round(blind_duration * tick_rate)（derived；tick_rate 缺失 → None，
      不伪造结束时刻；API 层仍可直接用 start_sec + blind_duration）。
    - 无法关联到任何 flash detonate 的 blind 行 → 丢弃（不伪造爆点/不虚构归属）。
    """
    mapper = _round_mapper(rounds)
    flash_by_eid: dict[int, list[int]] = {}
    for r in flash_rows:
        flash_by_eid.setdefault(int(r["entityid"]), []).append(int(r["tick"]))

    out: dict[int, list[dict]] = {}
    for seq, b in enumerate(blind_rows):
        tick = int(b.get("tick") or 0)
        rnd = mapper(tick)
        if rnd is None:
            continue
        eid = int(b.get("entityid") or -1)
        cand = flash_by_eid.get(eid)
        if not cand:
            continue
        nearest = min(cand, key=lambda t: abs(t - tick))
        if abs(nearest - tick) > window:
            continue
        attacker = str(b.get("attacker_steamid") or "")
        victim = str(b.get("user_steamid") or "")
        duration = float(b.get("blind_duration") or 0.0)
        cls = "unknown"
        if attacker and attacker == victim:
            cls = "self"
        elif identity_of.get(victim) and identity_of.get(attacker):
            cls = "team" if identity_of[victim] == identity_of[attacker] else "enemy"
        end_tick = None
        if tick_rate:
            end_tick = int(tick + round(duration * tick_rate))
        out.setdefault(rnd, []).append({
            "tick": tick, "seq": seq, "flash_entity_id": eid, "flash_tick": nearest,
            "blind_duration": round(duration, 4), "blind_end_tick": end_tick,
            "attacker_player_id": player_id_of.get(attacker),
            "victim_player_id": player_id_of.get(victim),
            "flash_class": cls,
            "authority": {"kind": "event", "source": "player_blind",
                          "detail": f"linked flash entityid={eid} window={window}"},
        })
    for rnd in out:
        out[rnd].sort(key=lambda e: (e["tick"], e["seq"]))
    return out


def build_he_damage(hurt_rows: list[dict], he_rows: list[dict], player_id_of: dict[str, int],
                    rounds: list[dict], window: int = HE_DAMAGE_WINDOW) -> dict[int, list[dict]]:
    """HE 真实受伤：player_hurt(weapon=hegrenade) 关联 hegrenade_detonate。

    - player_hurt 无 entityid 直连；按「攻击者(投掷者) == 投掷者 + weapon=hegrenade + 时间窗」关联 → authority=inferred。
    - 无法关联的伤（无匹配 hegrenade_detonate）不输出（不把 HE 当持续区、不虚构归属）。
    - 对每条关联输出 dmg_health + 受害者 + 关联的 he_tick（该 grenade 的 detonate tick）。
    """
    mapper = _round_mapper(rounds)
    he_index: list[dict] = []
    for r in he_rows:
        he_index.append({
            "tick": int(r["tick"] or 0),
            "thrower": str(r.get("user_steamid") or ""),
            "entityid": int(r.get("entityid") or -1),
        })
    out: dict[int, list[dict]] = {}
    for seq, h in enumerate(hurt_rows):
        weapon = str(h.get("weapon") or "").removeprefix("weapon_")
        if weapon not in HE_WEAPONS and weapon != "hegrenade":
            continue
        tick = int(h.get("tick") or 0)
        rnd = mapper(tick)
        if rnd is None:
            continue
        attacker = str(h.get("attacker_steamid") or "")
        if not attacker:
            continue
        # 找该攻击者最近（窗内）的 HE detonate
        match = None
        best = None
        for he in he_index:
            if he["thrower"] != attacker:
                continue
            d = abs(he["tick"] - tick)
            if d <= window and (best is None or d < best):
                best = d
                match = he
        if match is None:
            continue
        out.setdefault(rnd, []).append({
            "tick": tick, "seq": seq, "he_tick": match["tick"], "he_entity_id": match["entityid"],
            "dmg_health": int(float(h.get("dmg_health") or 0.0)),
            "attacker_player_id": player_id_of.get(attacker),
            "victim_player_id": player_id_of.get(str(h.get("user_steamid") or "")),
            "authority": {"kind": "inferred", "source": "thrower+weapon+time-window",
                          "detail": f"linked hegrenade_detonate entityid={match['entityid']}"},
        })
    for rnd in out:
        out[rnd].sort(key=lambda e: (e["tick"], e["seq"]))
    return out


def build_tactical_artifact(
    analyzer,
    match_id: int,
    map_name: str,
    playout_ticks: int,
    rounds: list[dict],
    players: list[dict],
    identity_of: dict[str, str],
    player_id_of: dict[str, int],
    deaths: list[dict],
    grenades_rows: list[dict],
    tick_rate: float | None = None,
    yaw_tick_step: int = 16,
    hurts: list[dict] | None = None,
) -> dict:
    """构建整场版本化战术 artifact（v3.0，6R13：击杀播报/真实闪光致盲/HE 伤害/投掷归属）。

    仅内部使用世界坐标 + 原始 tick；秒/中文区域/雷达归一化坐标由 API 层换算。
    """
    mapper = _round_mapper(rounds)

    # ---- 0. 朝向/枪线真值：每回合射击事件 + 门禁 ----
    gaze = gaze_build_shoot_events(analyzer, rounds, deaths, player_id_of, tick_rate)
    gaze_per_round = gaze.get("per_round", {})
    gaze_gate = gaze.get("gate", {})
    gaze_counts = gaze.get("counts", {})

    # ---- 0b. 每回合 bomb_planted tick（比赛阶段划分用）----
    bomb_planted_by_round: dict[int, int] = {}
    try:
        bp = analyzer._parser.parse_event("bomb_planted")
        for r in bp.to_dict(orient="records"):
            b_tick = int(r["tick"])
            rnd = mapper(b_tick)
            if rnd is not None and rnd not in bomb_planted_by_round:
                bomb_planted_by_round[rnd] = b_tick
    except Exception:  # noqa: BLE001
        bomb_planted_by_round = {}

    # ---- 1. 采样位置 + 真实 yaw/pitch（一次 parse_ticks）----
    pos_df = analyzer._parser.parse_ticks(
        ["X", "Y", "Z", "health", "CCSPlayerPawn.m_iTeamNum",
         "CCSPlayerPawn.m_angEyeAngles", "steamid", "tick", "last_place_name"],
        ticks=range(0, int(playout_ticks or 0) + 1, yaw_tick_step),
    )
    sampled_by_rp: dict[tuple[int, str], list[dict]] = {}
    for row in pos_df.to_dict(orient="records"):
        tick = int(row.get("tick") or 0)
        rnd = mapper(tick)
        if rnd is None:
            continue
        sid = str(row.get("steamid"))
        x, y, z = _clean(row.get("X")), _clean(row.get("Y")), _clean(row.get("Z"))
        if x is None or y is None:
            continue
        health = row.get("health")
        alive = health is not None and health > 0
        eye = row.get("CCSPlayerPawn.m_angEyeAngles")
        yaw = pitch = None
        if isinstance(eye, (list, tuple)) and len(eye) >= 2:
            pitch = _clean(eye[0])   # QAngle[0] = pitch
            yaw = _clean(eye[1])     # QAngle[1] = yaw
        sampled_by_rp.setdefault((rnd, sid), []).append({
            "tick": tick, "x": x, "y": y, "z": z, "alive": alive,
            "yaw": yaw, "pitch": pitch, "place": row.get("last_place_name") or None,
        })

    # ---- 2. kill/death 事件索引（复用 route 逻辑）----
    kills_by_rp: dict[tuple[int, str], list[dict]] = {}
    deaths_by_rp: dict[tuple[int, str], dict] = {}
    for d in deaths:
        tick = int(d.get("tick") or 0)
        rnd = mapper(tick)
        if rnd is None:
            continue
        attacker = str(d.get("attacker_steamid") or "")
        victim = str(d.get("user_steamid") or "")
        ax, ay = _clean(d.get("attacker_X")), _clean(d.get("attacker_Y"))
        vx, vy = _clean(d.get("user_X")), _clean(d.get("user_Y"))
        if attacker and ax is not None and ay is not None:
            kills_by_rp.setdefault((rnd, attacker), []).append({"tick": tick, "x": ax, "y": ay})
        if victim and vx is not None and vy is not None:
            deaths_by_rp[(rnd, victim)] = {"tick": tick, "x": vx, "y": vy}

    # ---- 3. 道具生命周期事件 + 轨迹段 ----
    smoke_start_rows = _parse_event_rows(analyzer, SMOKE_START)
    smoke_end_rows = _parse_event_rows(analyzer, SMOKE_END)
    fire_start_rows = _parse_event_rows(analyzer, FIRE_START)
    fire_end_rows = _parse_event_rows(analyzer, FIRE_END)
    he_rows = _parse_event_rows(analyzer, HE_DETONATE)
    flash_rows = _parse_event_rows(analyzer, FLASH_DETONATE)

    # 结束事件按 entityid 索引为「升序时间列表」：同投掷取"该 detonate 之后最近的一个 expire"
    # （规避 CS2 实体 id 在整场对局中被复用的跨投掷污染，保证烟/火 interval 诚实）
    def _end_index(rows) -> dict[int, list[int]]:
        idx: dict[int, list[int]] = {}
        for r in rows:
            idx.setdefault(int(r["entityid"]), []).append(int(r["tick"]))
        for eid in idx:
            idx[eid].sort()
        return idx

    smoke_end_by_eid = _end_index(smoke_end_rows)
    fire_end_by_eid = _end_index(fire_end_rows)

    segments = _segment_projectile_trajectories(grenades_rows)

    # ---- 3b. 6R13：击杀播报 / 真实闪光致盲 / HE 伤害 ----
    blind_rows: list[dict] = []
    try:
        blind_rows = analyzer._parser.parse_event(
            "player_blind", other=["tick", "user_steamid", "attacker_steamid", "blind_duration", "entityid"],
        ).to_dict(orient="records")
    except Exception:  # noqa: BLE001
        blind_rows = []
    if hurts is None:
        hurts = analyzer.parse_hurts()

    killfeed_by_round = build_killfeed(deaths, player_id_of, rounds)
    blind_by_round = build_blinds(blind_rows, flash_rows, player_id_of, identity_of, rounds, tick_rate)
    he_damage_by_round = build_he_damage(hurts, he_rows, player_id_of, rounds)

    # ---- 4. 逐回合构建玩家 + 道具 ----
    def _build_player_traj(points: list[dict], start_tick: int, end_tick: int, side: str) -> dict:
        pts = []
        for p in points:
            t = round((p["tick"] - start_tick) / tick_rate, 3) if tick_rate else None
            pts.append({
                "tick": int(p["tick"]), "time": t,
                "x": p["x"], "y": p["y"], "z": p["z"],
                "yaw": p.get("yaw"), "pitch": p.get("pitch"),
                "alive": bool(p["alive"]), "place": p.get("place") or None,
            })
        return {"side": side, "points": pts}

    artifact_rounds = []
    for r in rounds:
        rnd = r["round"]
        side_map = r.get("team_side_map") or {}
        start_tick, end_tick = r["start_tick"], r["end_tick"]
        # 玩家
        players_out: dict[str, dict] = {}
        for p in players:
            sid = str(p["steamid"])
            pid = player_id_of.get(sid)
            if pid is None:
                continue
            side = "CT" if (side_map.get(identity_of.get(sid)) == 2) else ("T" if (side_map.get(identity_of.get(sid)) == 3) else None)
            pts = sampled_by_rp.get((rnd, sid), [])
            deaths_for_p = deaths_by_rp.get((rnd, sid))
            # 死亡截断
            if deaths_for_p is not None:
                pts = [q for q in pts if q["tick"] <= deaths_for_p["tick"]]
                outcome = "dead"
            else:
                outcome = "alive"
            traj = _build_player_traj(pts, start_tick, end_tick, side)
            players_out[str(pid)] = {"player_id": pid, "side": side, "outcome": outcome,
                                     "points": traj["points"]}
        # 道具
        props = _build_props(rounds, rnd, side_map, identity_of, player_id_of,
                             smoke_start_rows, smoke_end_by_eid, fire_start_rows, fire_end_by_eid,
                             he_rows, flash_rows, segments, start_tick, end_tick, tick_rate)
        artifact_rounds.append({
            "round": rnd, "start_tick": start_tick, "end_tick": end_tick,
            "freeze_end_tick": r.get("freeze_end_tick"),
            "bomb_planted_tick": bomb_planted_by_round.get(rnd),
            "winner_side": r.get("winner_side"), "winner_team": r.get("winner_team"),
            "team_side_map": side_map, "players": players_out, "props": props,
            "shoots": gaze_per_round.get(rnd, []),
            "killfeed": killfeed_by_round.get(rnd, []),
            "blinds": blind_by_round.get(rnd, []),
            "he_damage": he_damage_by_round.get(rnd, []),
        })

    timebase = {"authority": None}
    if tick_rate:
        try:
            info = read_cdemo_file_info(getattr(analyzer, "demo_path", None)) if getattr(analyzer, "demo_path", None) else None
            if info:
                timebase = {"authority": "cdemo_file_info", **info}
        except Exception:  # noqa: BLE001
            timebase = {"authority": None}

    return {
        "schema_version": TACTICAL_SCHEMA_VERSION,
        "match_id": match_id,
        "map_name": map_name,
        "tick_rate": tick_rate,
        "timebase": timebase,
        "rounds": artifact_rounds,
        "prop_count": sum(len(r["props"]) for r in artifact_rounds),
        "shoot_count": sum(len(r["shoots"]) for r in artifact_rounds),
        "kill_count": sum(len(r["killfeed"]) for r in artifact_rounds),
        "blind_count": sum(len(r["blinds"]) for r in artifact_rounds),
        "he_damage_count": sum(len(r["he_damage"]) for r in artifact_rounds),
        "gaze_gate": gaze_gate,
        "gaze_counts": gaze_counts,
    }


def _build_props(rounds: list[dict], rnd: int, side_map: dict, identity_of: dict[str, str],
                 player_id_of: dict[str, int], smoke_start, smoke_end_by_eid,
                 fire_start, fire_end_by_eid, he_rows, flash_rows, segments,
                 start_tick: int, end_tick: int, tick_rate: float | None) -> list[dict]:
    """构造该回合活跃道具（active interval 与该回合作业窗口有交集）。"""
    props: list[dict] = []
    mapper = _round_mapper(rounds)
    r0, r1 = start_tick, end_tick

    def _thrower_id(steamid: str) -> int | None:
        return player_id_of.get(str(steamid))

    def _prop(type_, det_tick, x, y, z, active_start, active_end, authority, thrower_id, throw_tick=None, traj=None):
        props.append({
            "type": type_, "thrower_player_id": thrower_id,
            "throw_tick": throw_tick, "detonate_tick": int(det_tick),
            "x": _clean(x), "y": _clean(y), "z": _clean(z),
            "active_start": int(active_start) if active_start is not None else None,
            "active_end": int(active_end) if active_end is not None else None,
            "authority": authority,
            "trajectory": traj or [],
        })

    # 烟雾
    for r in smoke_start:
        tick = int(r["tick"]); rnd_of = mapper(tick)
        t_i = _thrower_id(r.get("user_steamid") or "")
        if rnd_of != rnd:
            continue
        eid = int(r["entityid"])
        active_end = _clamped_end(_next_end(smoke_end_by_eid.get(eid), tick), tick, tick_rate)
        authority = _authority("event", f"smokegrenade_detonate/expired entityid={eid}") if active_end is not None \
            else _authority("event", "missing_expire")
        seg = _match_segment(segments, str(r.get("user_steamid") or ""), tick)
        traj = seg["points"] if seg else []
        _prop("smoke", tick, r.get("x"), r.get("y"), r.get("z"), tick, active_end, authority, t_i,
              throw_tick=seg["t0"] if seg else None, traj=traj)

    # 燃烧（molotov/incendiary → inferno）
    for r in fire_start:
        tick = int(r["tick"]); rnd_of = mapper(tick)
        t_i = _thrower_id(r.get("user_steamid") or "")
        if rnd_of != rnd:
            continue
        eid = int(r["entityid"])
        active_end = _clamped_end(_next_end(fire_end_by_eid.get(eid), tick), tick, tick_rate)
        authority = _authority("event", f"inferno_startburn/expire entityid={eid}") if active_end is not None \
            else _authority("event", "missing_expire")
        seg = _match_segment(segments, str(r.get("user_steamid") or ""), tick)
        traj = seg["points"] if seg else []
        _prop("fire", tick, r.get("x"), r.get("y"), r.get("z"), tick, active_end, authority, t_i,
              throw_tick=seg["t0"] if seg else None, traj=traj)

    # 高爆 / 闪光（瞬时事件）
    for r in he_rows:
        tick = int(r["tick"]); rnd_of = mapper(tick)
        t_i = _thrower_id(r.get("user_steamid") or "")
        if rnd_of != rnd:
            continue
        seg = _match_segment(segments, str(r.get("user_steamid") or ""), tick)
        traj = seg["points"] if seg else []
        _prop("he", tick, r.get("x"), r.get("y"), r.get("z"), tick, tick,
              _authority("event", "hegrenade_detonate"), t_i, throw_tick=seg["t0"] if seg else None, traj=traj)

    for r in flash_rows:
        tick = int(r["tick"]); rnd_of = mapper(tick)
        t_i = _thrower_id(r.get("user_steamid") or "")
        if rnd_of != rnd:
            continue
        seg = _match_segment(segments, str(r.get("user_steamid") or ""), tick)
        traj = seg["points"] if seg else []
        _prop("flash", tick, r.get("x"), r.get("y"), r.get("z"), tick, tick,
              _authority("event", "flashbang_detonate"), t_i, throw_tick=seg["t0"] if seg else None, traj=traj)

    props.sort(key=lambda p: p["detonate_tick"])
    return props


def tactical_object_key(match_id: int, schema_version: str = TACTICAL_SCHEMA_VERSION) -> str:
    return f"tactical/{match_id}/{schema_version}/tactical.json.gz"


def artifact_sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()
