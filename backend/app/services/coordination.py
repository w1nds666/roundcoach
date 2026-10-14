"""6R16：深度配合指标引擎重建（诚实·机会归一·episode 级·区域状态机·可定位）。

相对 6R9/6R12 的修复：
- 机会归一：所有"率/时长/覆盖率"以机会数为分母；**零机会 → rate=null 且 applicable=false**。
- spacing：**按 episode 秒级聚合**（不再用 tick 采样数灌分母）；只统计 freeze_end 后的
  opening/execute/post_plant；freeze/spawn/save/round_end 与无实际战术变化的静态段不产出 episode。
- utility_assisted_entry：**复用 6R13 真实道具生命周期**，区分 validated（flash→player_blind、
  HE→player_hurt）与 inferred（烟/火→时空遮挡候选），**绝不把邻近当因果**。
- isolated_death：排除残局（最后存活）/保枪（save）/回合基本结束（post_plant 收尾）反例；
  无 navmesh → 文案说「支援距离候选」。
- area_control：重建为可信状态机（unknown/occupied/contested/influence/controlled），
  **controlled 需正向可审计战术信号**，**敌方缺席本身不升级为 controlled**（6R16 关键校正）；
  **回合胜负仅作后续转化结果，不作控制真值**。
- crossfire_candidate：无 LoS/navmesh → 保持 hidden 诊断候选，不进入正向教练结论。
- 同一事实只计一次（episode 去重），team/round/player 不串缓存，seek 状态确定性。
"""
from __future__ import annotations

import hashlib
import json
import math
from itertools import combinations

from app.services.coordination_thresholds import (
    AREA_SAMPLE_STEP, BASELINE_NOTE, CLOSE_SPACING_WORLD, CONFIDENCE_OPP, CROSSFIRE_ANGLE_DEG,
    CROSSFIRE_RANGE_WORLD, ISOLATED_SUPPORT_DIST, MAX_CONCLUSIONS, SIMULT_WINDOW_SEC,
    SPACING_ALIVE_SCALE, SPACING_EPISODE_GAP_TICKS, SPACING_MIN_EPISODE_SEC, TRADE_FACING_DEG,
    TRADE_REACH_WORLD, TRADE_WINDOW_SEC, UTILITY_ENTRY_RANGE_WORLD, UTILITY_WINDOW_SEC,
    VERSION as THRESHOLDS_VERSION, cap_confidence, confidence_from_opportunities, spacing_allowed,
)
from app.services.places import translate_place
from app.services.area_state import build_region_state_machine_for_round

COORDINATION_SCHEMA_VERSION = "2.0"

MAX_EVIDENCE_PER_ROUND_METRIC = 3
MAX_EVIDENCE_PER_ROUND = 6
MAX_EVIDENCE_TOTAL = 20          # 6R16：克制信息密度（不再 40 条）

# 指标的中文标签与"人话"文案模板
METRIC_LABEL = {
    "trade": "补枪响应/交易支持",
    "simultaneous_contact": "协同接触",
    "spacing": "队伍间距纪律",
    "utility_assisted_entry": "道具助攻进点",
    "isolated_death": "孤立阵亡",
    "area_control": "区域控制",
    "crossfire_candidate": "交叉火力候选",
}
METRIC_LIVE = {
    "trade": "trans",
    "simultaneous_contact": "trans",
    "spacing": "spacing",
    "utility_assisted_entry": "support",
    "isolated_death": "death",
    "area_control": "area",
    "crossfire_candidate": "fire",
}

# ---- 信任度（6R16 重定义）----
METRIC_TRUST = {
    "trade": "validated",
    "simultaneous_contact": "validated",
    "spacing": "experimental",            # 间距启发式，无 navmesh
    "utility_assisted_entry": "experimental",  # 仅能证邻近/部分 validated；无 LoS
    "isolated_death": "experimental",     # 无 navmesh/可达性 → 支援距离候选
    "area_control": "experimental",       # 无 LoS / 区域几何覆盖不足
    "crossfire_candidate": "hidden",      # 无 LoS/遮挡验证
}
METRIC_DEFINITION = {
    "trade": "队友被敌方击杀后，存活队友在窗口内完成补枪（击杀击杀者）的比例；同一阵亡只计一次。",
    "simultaneous_contact": "一波窗口内 ≥2 名不同队友均取得击杀的波次占比（单人连杀不算协同）。",
    "spacing": "战术机会阶段内，'保持合理间距段'占'有效战术期段'的时间占比（按 episode 秒级聚合）。",
    "utility_assisted_entry": "队友投掷道具在其真实作用窗口内辅助进点击杀；flash 以真实 player_blind、HE 以真实 player_hurt 为 validated，烟/火仅证时空遮挡候选。",
    "isolated_death": "阵亡时最近存活队友距离超支援阈值且非残局/保枪/最后存活的阵亡占比；无 navmesh 时为'支援距离候选'。",
    "area_control": "区域控制可信状态机：controlled 需持续占据+无敌方+正向可审计信号；回合胜负仅作后续转化结果。",
    "crossfire_candidate": "两队友朝向同一交战点且夹角足够大的'几何候选'；无 LoS 无法证明真实交叉火力。",
}
METRIC_OPPORTUNITY = {
    "trade": "一次敌方击杀本队成员且存在可交战存活队友。",
    "simultaneous_contact": "一次接敌接触波。",
    "spacing": "一个'有 ≥2 存活队友的战术期连续段'（freeze_end 后）。",
    "utility_assisted_entry": "一次己方投掷且存在可关联真实效果（盲/伤）或烟/火 active interval 覆盖的道具。",
    "isolated_death": "一次满足排除反例的本队阵亡（非最后存活/非保枪）。",
    "area_control": "出现带正向信号 controlled 状态段的回合。",
    "crossfire_candidate": "一个交战点几何上存在可构成交叉的队友对。",
}
METRIC_LIMITS = {
    "trade": "仅证明时序（窗口内补枪），未验证补枪者实际朝向/视线；同一阵亡计一次。",
    "simultaneous_contact": "仅按击杀时间聚类，未验证真正'协同开火'；单人连杀不算协同。",
    "spacing": "间距为启发式（按存活人数放宽），无 navmesh；只统计 freeze_end 后战术期；按 episode 秒级聚合。",
    "utility_assisted_entry": "flash/HE 可 prove 真实效果，烟/火仅证时间/空间邻近（无法证明致盲/遮挡）；不把邻近当因果。",
    "isolated_death": "距离阈值启发式，无 navmesh/可达性 → 支援距离候选；已排除残局/保枪/最后存活。",
    "area_control": "无 LoS；缺区域几何/几何不足时 controlled 只能标实验性；敌方缺席不升级为 controlled；回合胜负不作真值。",
    "crossfire_candidate": "仅几何+朝向候选，无 navmesh/LoS，无法证明真实交叉火力。",
}

# ---- 比赛阶段最小可信分类 ----
PHASE_OPENING_SEC = 5.0
PHASE_SAVE_WINDOW_SEC = 10.0
PHASE_TACTICAL = {"opening", "execute", "post_plant"}


def classify_phase(tick: int, start_t: int, freeze_end_t: int, end_t: int,
                   bomb_planted_t: int | None, tick_rate: float | None) -> str:
    if not tick_rate:
        return "execute"
    if tick < freeze_end_t:
        return "freeze"
    if tick >= end_t - PHASE_SAVE_WINDOW_SEC * tick_rate:
        return "save"
    opening_end = freeze_end_t + int(PHASE_OPENING_SEC * tick_rate)
    if tick < opening_end:
        return "opening"
    if bomb_planted_t is not None and tick >= bomb_planted_t:
        return "post_plant"
    return "execute"


def _metric_credibility_inventory() -> list[dict]:
    return [{
        "metric_id": m, "trust": METRIC_TRUST.get(m, "experimental"),
        "definition": METRIC_DEFINITION.get(m, ""),
        "opportunity": METRIC_OPPORTUNITY.get(m, ""),
        "limits": METRIC_LIMITS.get(m, ""),
    } for m in METRIC_LABEL]


def _metric_human(m, rate):
    if rate is None:
        return None
    return {
        "trade": f"补枪率 {rate:.0%}",
        "simultaneous_contact": f"协同接敌 {rate:.0%}",
        "spacing": f"有效间距时间 {rate:.0%}",
        "utility_assisted_entry": f"道具助攻进点 {rate:.0%}",
        "isolated_death": f"孤立阵亡 {rate:.0%}",
        "area_control": f"控区转化 {rate:.0%}",
        "crossfire_candidate": f"交叉候选 {rate:.0%}",
    }[m]


def _dist(ax, ay, bx, by):
    return math.hypot((ax or 0) - (bx or 0), (ay or 0) - (by or 0))


def _deg(a, b):
    d = abs((a or 0) - (b or 0)) % 360
    return min(d, 360 - d)


def _at(points, tick):
    if not points:
        return None
    n = len(points)
    lo, hi = 0, n - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if (points[mid]["tick"] or 0) < tick:
            lo = mid + 1
        else:
            hi = mid
    best = None; bd = 10 ** 18
    for i in (lo, lo - 1, lo + 1):
        if 0 <= i < n:
            d = abs((points[i]["tick"] or 0) - tick)
            if d < bd:
                bd = d; best = points[i]
    return best


def _region_cn(map_name, place):
    return translate_place(map_name, place) if place else "未知区域"


def _place_of(pts_by_pid, pid, tick):
    return (_at(pts_by_pid.get(pid, {}).get("points", []), tick) or {}).get("place")


# 出生点区域（spawn）：这些区域的"间距"不是有意义的战术纪律时刻，禁止正向表扬
_SPAWN_PLACES = {"CTSpawn", "TSpawn", "CT_Spawn", "T_Spawn", "Spawn", "CTSpawn", "TSpawn"}


def _is_spawn(place: str | None) -> bool:
    p = (place or "").replace("_", "").replace(" ", "").lower()
    return p in {"ctspawn", "tspawn", "spawn"} or p.endswith("spawn")


def _id_for_steam(id_of, steam):
    return id_of.get(str(steam)) if steam is not None else None


# ---------- 序列化/确定性 ----------

def _canon(v):
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return "NONFINITE"
        return round(v, 5)
    if isinstance(v, dict):
        return {str(k): _canon(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    return v


def _content_hash(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        b = json.dumps(_canon(p), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        h.update(b)
    return h.hexdigest()


MAX_CONCLUSION_PER_METRIC_TONE = 1


def _ev_sig(ev):
    """同一事实（episode）签名：metric+team+round+region+排序players+时间桶+tone。"""
    tb = int((ev.get("seconds") or 0) // 8)
    return (ev.get("metric_id"), ev.get("team"), ev.get("round"), ev.get("region"),
            tuple(sorted(ev.get("players", []) or [])), tb,
            "success" if ev.get("success") else "problem")


def _sig_as_list(ev):
    m, team, rn, region, players, tb, tone = _ev_sig(ev)
    return [m, team, rn, region, list(players), tb, tone]


def _dedup_evidence(round_entries):
    seen = set()
    deduped = []
    for ev in round_entries:
        sig = _ev_sig(ev)
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(ev)
    per_round = {}
    for ev in deduped:
        per_round.setdefault(ev["round"], []).append(ev)
    capped = []
    for r in sorted(per_round):
        capped.extend(per_round[r][:MAX_EVIDENCE_PER_ROUND])
    capped = capped[:MAX_EVIDENCE_TOTAL]
    capped.sort(key=lambda e: (e["round"], e.get("seconds") or 0))
    return capped


# ---------- 单指标累积 ----------

class _Acc:
    def __init__(self):
        self.opportunities = 0
        self.converted = 0
        self.evidence = []
        self.rounds_seen = set()
        self.has_data = False

    def add_ev(self, ev):
        if len(self.evidence) < MAX_EVIDENCE_PER_ROUND_METRIC * 10:
            self.evidence.append(ev)


def _new_accs(team_ids, metric_ids):
    return {(t, m): _Acc() for t in team_ids for m in metric_ids}


def _rate(acc):
    if acc.opportunities <= 0:
        return None, "no_opportunity", 0.0
    r = acc.converted / acc.opportunities
    return r, "ok", r * 100.0


# ---------- spacing episode 级聚合 ----------

def _spacing_episodes(round_players, team_pids, freeze, end_t, start_t, tick_rate, bomb_t):
    """把单个队伍在该回合的战术期采样切成连续段（仅 opening/execute/post_plant）。

    返回 [{start_tick, end_tick, sec, samples:[(tk, alive, cram_frac)]}]。
    断段条件：非战术 phase / 存活<2 / **任一存活成员仍在出生点(spawn)** → 产出一个 episode；
    段内样本用秒聚合；**出生点不产出正/负间距证据**（任何存活成员在 spawn 的采样一律跳过）。
    绝不对 freeze/spawn/save/round_end 产出段。只有当全部存活成员都离开出生点、进入有效战术区
    才重新开始一段。episode 分母 = 段数（非采样点）。
    """
    if not tick_rate:
        return []
    pts_by_pid = {p: round_players.get(str(p), {}).get("points", []) for p in team_pids}

    def _place_of_p(pid, tk):
        return (_at(pts_by_pid.get(pid), tk) or {}).get("place")

    episodes = []
    cur = None
    for tk in range(start_t, end_t + 1, AREA_SAMPLE_STEP):
        ph = classify_phase(tk, start_t, freeze, end_t, bomb_t, tick_rate)
        if ph not in PHASE_TACTICAL:
            if cur:
                episodes.append(cur); cur = None
            continue
        alive = [p for p in team_pids if bool((_at(pts_by_pid[p], tk) or {}).get("alive"))]
        if len(alive) < 2:
            if cur:
                episodes.append(cur); cur = None
            continue
        # 6R16 关键：任一存活成员仍在出生点 → 该采样不是有效战术区间距时刻，跳过且断段
        #（出生点不能产出正/负间距证据；断段后仅在全体离开出生点后重新开始）
        any_spawn_alive = any(_is_spawn(_place_of_p(p, tk)) for p in alive)
        if any_spawn_alive:
            if cur:
                episodes.append(cur); cur = None
            continue
        allow = spacing_allowed(CLOSE_SPACING_WORLD, len(alive))
        total_pairs = len(alive) * (len(alive) - 1) // 2
        crammed = 0
        for a, b in combinations(alive, 2):
            aa = _at(pts_by_pid[a], tk); bb = _at(pts_by_pid[b], tk)
            if aa and bb and _dist(aa.get("x"), aa.get("y"), bb.get("x"), bb.get("y")) < allow:
                crammed += 1
        cram_frac = crammed / total_pairs if total_pairs else 0.0
        if cur is None:
            cur = {"start_tick": tk, "end_tick": tk, "alive_min": len(alive), "samples": []}
        cur["end_tick"] = tk
        cur["alive_min"] = min(cur["alive_min"], len(alive))
        cur["samples"].append({"tk": tk, "alive": len(alive), "cram_frac": cram_frac})
    if cur:
        episodes.append(cur)
    # 每条 episode 秒级聚合 + 去除非战术变化瞬态段
    out = []
    for ep in episodes:
        sec = (ep["end_tick"] - ep["start_tick"]) / tick_rate
        if sec < SPACING_MIN_EPISODE_SEC:
            continue
        compliant_sec = 0.0
        for i in range(len(ep["samples"]) - 1):
            if ep["samples"][i]["cram_frac"] <= 0.5:
                compliant_sec += (ep["samples"][i + 1]["tk"] - ep["samples"][i]["tk"]) / tick_rate
        last = ep["samples"][-1]
        if last["cram_frac"] <= 0.5 and ep["end_tick"] >= last["tk"]:
            compliant_sec += (ep["end_tick"] - last["tk"]) / tick_rate
        out.append({"start_tick": ep["start_tick"], "end_tick": ep["end_tick"],
                    "sec": round(sec, 2), "compliant_sec": round(compliant_sec, 2),
                    "alive_min": ep["alive_min"], "samples": ep["samples"]})
    return out


# ---------- 主函数 ----------

def build_coordination_analysis(
    artifact_rounds: list[dict],
    map_name: str,
    tick_rate: float | None,
    kills: list[dict],
    deaths: list[dict],
    id_of: dict,
    name_of: dict,
    team_of: dict,
    focus_player_id: int | None = None,
    team_names: dict | None = None,
    region_geometry: dict | None = None,
) -> dict:
    """从战术 artifact + kill 事件计算整场深度配合分析（确定性）。

    artifact_rounds：[{round,start_tick,end_tick,freeze_end_tick,winner_team,players,props,
                       blinds,he_damage,shoots}]（v3.0 已含 6R13 真实生命周期）。
    region_geometry：{map: {regions:[...], geometry_trust:{...}}}（可选；缺省 → 区域几何不可用）。
    """
    team_names = team_names or {}
    metric_ids = list(METRIC_LABEL.keys())
    team_ids = sorted({t for t in team_of.values() if t in ("A", "B")})

    ks_r: dict[int, list[dict]] = {}
    for k in kills:
        ks_r.setdefault(k["round"], []).append(k)
    ds_r: dict[int, list[dict]] = {}
    for d in deaths:
        ds_r.setdefault(d["round"], []).append(d)

    accs = _new_accs(team_ids, metric_ids)
    round_entries: list[dict] = []
    total_rounds = len(artifact_rounds)

    # 区域几何（按 map 聚合；无则 geometry_available=False）
    geo_by_map = region_geometry or {}

    def _team_of_pid(pid):
        return team_of.get(pid)

    for rnd in artifact_rounds:
        r = rnd["round"]
        start_t = rnd["start_tick"]; end_t = rnd["end_tick"]
        freeze = rnd.get("freeze_end_tick") or start_t
        players_map = rnd.get("players", {})
        props = rnd.get("props", [])
        blinds = rnd.get("blinds", [])
        he_damage = rnd.get("he_damage", [])
        shoots = rnd.get("shoots", [])
        winner_team = rnd.get("winner_team")
        pts_by_pid = {int(pid): pd for pid, pd in players_map.items()}
        side_of_pid = {}
        for pid, pd in players_map.items():
            side_of_pid[pid] = pd.get("side")

        members: dict[str, list[int]] = {}
        for t in team_ids:
            members[t] = [pid for pid in pts_by_pid if _team_of_pid(pid) == t]

        def _alive_at(pid, tick):
            return bool((_at(pts_by_pid.get(pid, {}).get("points", []), tick) or {}).get("alive"))

        def _pos_at(pid, tick):
            a = _at(pts_by_pid.get(pid, {}).get("points", []), tick)
            return (a or {}).get("x"), (a or {}).get("y")

        def _sec(tick):
            if not tick_rate:
                return None
            return max(0.0, round((tick - freeze) / tick_rate, 2))

        def _ev(metric_id, sec, region, players, success, reason, layers, highlight, detail="", mval=None):
            return {"metric_id": metric_id, "round": r, "seconds": sec, "region": region,
                    "players": players, "success": success, "reason": reason,
                    "layers": layers, "highlight": highlight, "detail": detail, "metric_value": mval}

        ks = ks_r.get(r, [])
        ds = ds_r.get(r, [])

        # ===== 1. trade（validated；同一阵亡只计一次；须存活队友可交战距离）=====
        for d in ds:
            v_id = _id_for_steam(id_of, d.get("victim_steam"))
            a_id = _id_for_steam(id_of, d.get("actor_steam"))
            if v_id is None or a_id is None:
                continue
            v_team = _team_of_pid(v_id); a_team = _team_of_pid(a_id)
            if v_team is None or a_team is None or v_team == a_team:
                continue
            dt = int(d["tick"])
            if not _alive_at(a_id, dt):
                continue
            # 可交际队友：存活且在可交易距离内的同队玩家
            vx, vy = d.get("victim_x"), d.get("victim_y")
            tradable = []
            for pid in members[v_team]:
                if pid == v_id or not _alive_at(pid, dt):
                    continue
                ax, ay = _pos_at(pid, dt)
                if vx is not None and _dist(vx, vy, ax, ay) <= TRADE_REACH_WORLD:
                    tradable.append(pid)
            if not tradable:
                continue
            acc = accs[(v_team, "trade")]
            acc.opportunities += 1
            acc.rounds_seen.add(r)
            acc.has_data = True
            traded = False
            for k2 in ks:
                if str(k2.get("victim_steam")) == str(d.get("actor_steam")):
                    kb = _id_for_steam(id_of, k2.get("actor_steam"))
                    if kb and _team_of_pid(kb) == v_team and kb != v_id:
                        if tick_rate and dt < int(k2["tick"]) <= dt + TRADE_WINDOW_SEC * tick_rate:
                            traded = True
                            sec = _sec(int(k2["tick"]))
                            acc.add_ev(_ev("trade", sec, _region_cn(map_name, _place_of(pts_by_pid, kb, int(k2["tick"]))),
                                           [v_id, kb], True, f"死亡后 {TRADE_WINDOW_SEC}s 内完成补枪",
                                           ["gun", "route"], [v_id, kb]))
                            break
            if traded:
                acc.converted += 1
            else:
                sec = _sec(dt)
                acc.add_ev(_ev("trade", sec, _region_cn(map_name, _place_of(pts_by_pid, v_id, dt)),
                               [v_id, a_id], False, f"阵亡后 {TRADE_WINDOW_SEC}s 内队友未完成补枪",
                               ["gun", "route"], [v_id, a_id]))

        # ===== 2. simultaneous_contact（validated；≥2 不同队友）=====
        for t in team_ids:
            tks = [k for k in ks if _team_of_pid(_id_for_steam(id_of, k.get("actor_steam"))) == t]
            if not tks:
                continue
            tks.sort(key=lambda x: int(x["tick"]))
            clusters = []
            cur = [tks[0]]
            for k in tks[1:]:
                if tick_rate and (int(k["tick"]) - int(cur[-1]["tick"])) <= SIMULT_WINDOW_SEC * tick_rate:
                    cur.append(k)
                else:
                    clusters.append(cur); cur = [k]
            clusters.append(cur)
            acc = accs[(t, "simultaneous_contact")]
            for cl in clusters:
                actors = {_id_for_steam(id_of, k.get("actor_steam")) for k in cl}
                actors.discard(None)
                acc.opportunities += 1
                if len(actors) >= 2:
                    acc.converted += 1
                    last = cl[-1]
                    a_ids = list(actors)[:2]
                    sec = _sec(int(last["tick"]))
                    acc.add_ev(_ev("simultaneous_contact", sec,
                                   _region_cn(map_name, _place_of(pts_by_pid, a_ids[0], int(last["tick"]))),
                                   a_ids, True, f"{SIMULT_WINDOW_SEC}s 内 {len(actors)} 名队友均取得击杀",
                                   ["gun", "route"], a_ids, mval=len(actors)))
                acc.rounds_seen.add(r)
                acc.has_data = True

        # ===== 3. spacing（6R16：episode 秒级聚合；仅 freeze_end 后战术期）=====
        for t in team_ids:
            acc = accs[(t, "spacing")]
            if not tick_rate:
                continue
            bomb_t = rnd.get("bomb_planted_tick")
            episodes = _spacing_episodes(players_map, members[t], freeze, end_t, start_t, tick_rate, bomb_t)
            for ep in episodes:
                acc.opportunities += 1     # 分母 = episode 段数（不是采样点）
                acc.converted += 1 if ep["compliant_sec"] >= (ep["sec"] * 0.6) else 0
                acc.rounds_seen.add(r)
                acc.has_data = True
                # 代表性证据：取该段的**中间样本**（而非边界/最差样本），success 由该样本真实 cram_frac 决定
                mid = ep["samples"][len(ep["samples"]) // 2]
                ok = mid["cram_frac"] <= 0.5
                pair = None
                alive = [p for p in members[t] if _alive_at(p, mid["tk"])]
                if len(alive) >= 2:
                    pair = (alive[0], alive[1])
                else:
                    pair = (members[t][0], members[t][1]) if len(members[t]) >= 2 else (members[t][0], members[t][0])
                acc.add_ev(_ev("spacing", _sec(mid["tk"]),
                               _region_cn(map_name, _place_of(pts_by_pid, pair[0], mid["tk"])),
                               list(pair), ok,
                               (f"战术期间距合规（段内代表性扎堆率 {mid['cram_frac']:.0%}）" if ok
                                else f"战术期间距扎堆（段内代表性扎堆率 {mid['cram_frac']:.0%}）"),
                               ["route", "smoke"], list(pair), mval=round(mid["cram_frac"], 2)))

        # ===== 4. utility_assisted_entry（6R16：复用真实生命周期；区分 validated/inferred）=====
        for t in team_ids:
            acc = accs[(t, "utility_assisted_entry")]
            for pr in props:
                thrower = pr.get("thrower_player_id")
                if _team_of_pid(thrower) != t:
                    continue
                ptype = pr.get("type")
                det = pr.get("detonate_tick")
                px, py = pr.get("x"), pr.get("y")
                if det is None or px is None or py is None:
                    continue
                window_start, window_end = det, det
                has_active = False
                if ptype in ("smoke", "fire") and pr.get("active_start") is not None and pr.get("active_end") is not None:
                    window_start = pr["active_start"]; window_end = pr["active_end"]
                    has_active = True
                # 可关联真实效果（validated）：
                # flash → 存在真实 player_blind（attacker=thrower）在 detonate 附近
                # he → 存在真实 he_damage（attacker=thrower）在 detonate 附近
                effect_kind = None
                if tick_rate:
                    w0, w1 = window_start - 0, window_end + UTILITY_WINDOW_SEC * tick_rate
                    if ptype == "flash":
                        for b in blinds:
                            if b.get("attacker_player_id") == thrower and abs(int(b.get("tick") or 0) - det) <= UTILITY_WINDOW_SEC * tick_rate:
                                effect_kind = "validated_blind"
                                break
                    elif ptype == "he":
                        for hd in he_damage:
                            if hd.get("attacker_player_id") == thrower and abs(int(hd.get("tick") or 0) - det) <= UTILITY_WINDOW_SEC * tick_rate:
                                effect_kind = "validated_hurt"
                                break
                    else:
                        effect_kind = "temporal_block_candidate"   # 烟/火 → 时空遮挡候选
                # 机会：存在非投掷者存活队友在作用范围
                beneficiary = [pid for pid in members[t] if pid != thrower and _alive_at(pid, window_start)]
                if not beneficiary:
                    continue
                acc.opportunities += 1
                acc.rounds_seen.add(r)
                acc.has_data = True
                converted_here = False; hit_actor = None; converted_kt = None
                if tick_rate:
                    for k in ks:
                        actor = _id_for_steam(id_of, k.get("actor_steam"))
                        if actor is None or _team_of_pid(actor) != t or actor == thrower:
                            continue
                        kt = int(k["tick"])
                        # 6R21D item8：烟/火只有在 active 区间内才算"掩护"——烟散则无掩护，
                        # 不得把 active 结束后的击杀（即便落在 detonate+宽限窗内）判为道具助攻；
                        # he/flash 为瞬时效果，保留 detonate 后宽限窗。
                        hard_end = window_end
                        if not has_active:
                            hard_end = window_end + UTILITY_WINDOW_SEC * tick_rate
                        if window_start <= kt <= hard_end:
                            if _dist(px, py, k.get("actor_x"), k.get("actor_y")) <= UTILITY_ENTRY_RANGE_WORLD:
                                converted_here = True; hit_actor = actor; converted_kt = kt
                                break
                if converted_here:
                    acc.converted += 1
                    # 证据定位到真实击杀时刻（烟/火在其 active 区间内），而非 window_end+宽限（可能已过烟散）
                    sec = _sec(converted_kt if converted_kt is not None else min(window_end + UTILITY_WINDOW_SEC * tick_rate, end_t))
                    detail = ""
                    if effect_kind == "validated_blind":
                        detail = "真实 player_blind 证明致盲（validated）"
                    elif effect_kind == "validated_hurt":
                        detail = "真实 player_hurt(hegrenade) 证明受伤（validated）"
                    elif effect_kind == "temporal_block_candidate":
                        detail = "仅证明时间/空间邻近：烟/火（无致盲/遮挡字段验证）"
                    else:
                        detail = "无真实效果关联，仅邻近（无致盲/遮挡验证）"
                    acc.add_ev(_ev("utility_assisted_entry", sec,
                                   _region_cn(map_name, _place_of(pts_by_pid, thrower, det)),
                                   [thrower, hit_actor or beneficiary[0]], True,
                                   f"队友《{ptype}》在进点窗口内掩护下取得击杀",
                                   ["smoke", "fire", "flash", "gun"], [thrower, hit_actor or beneficiary[0]],
                                   detail=detail, mval=ptype))

        # ===== 5. isolated_death（6R16：排除残局/保枪/最后存活）=====
        for d in ds:
            v_id = _id_for_steam(id_of, d.get("victim_steam"))
            if v_id is None:
                continue
            t = _team_of_pid(v_id)
            if t is None:
                continue
            dt = int(d["tick"])
            # 反例排除：非战术期(保枪/收尾) → 不报
            ph = classify_phase(dt, start_t, freeze, end_t, rnd.get("bomb_planted_tick"), tick_rate) if tick_rate else "execute"
            if ph in ("freeze", "save"):
                continue
            # 反例排除：最后存活（该队除他外无人存活）
            other_alive = [pid for pid in members[t] if pid != v_id and _alive_at(pid, dt)]
            if not other_alive:
                continue
            vx, vy = d.get("victim_x"), d.get("victim_y")
            acc = accs[(t, "isolated_death")]
            acc.opportunities += 1
            acc.rounds_seen.add(r)
            acc.has_data = True
            nearest = 10 ** 18; nearest_pid = None
            for pid in other_alive:
                ax, ay = _pos_at(pid, dt)
                dd = _dist(vx, vy, ax, ay)
                if dd < nearest:
                    nearest = dd; nearest_pid = pid
            if nearest >= ISOLATED_SUPPORT_DIST:
                acc.converted += 1
                sec = _sec(dt)
                players = [v_id] + ([nearest_pid] if nearest_pid else [])
                acc.add_ev(_ev("isolated_death", sec,
                               _region_cn(map_name, _place_of(pts_by_pid, v_id, dt)),
                               players, False,
                               f"阵亡时最近存活队友 {round(nearest)} 距离（阈值 {ISOLATED_SUPPORT_DIST}；无路径验证）",
                               ["route"], players,
                               detail="支援距离候选（无 navmesh）", mval=round(nearest)))

        # ===== 6. area_control（6R16：可信状态机 + 正向信号门槛，调用 area_state 模块）=====
        for t in team_ids:
            acc = accs[(t, "area_control")]
            if not tick_rate:
                continue
            members_side = [pid for pid in members[t] if _alive_at(pid, start_t)]
            if not members_side:
                continue
            geo = (geo_by_map.get(map_name) or {}).get("regions")
            if not geo:
                continue
            # 我方投掷者集合（区分队伍）
            thrower_my = set(p.get("thrower_player_id") for p in props if p.get("thrower_player_id") in members[t])
            for region in geo:
                sm = build_region_state_machine_for_round(
                    region, pts_by_pid, rnd, tick_rate, members, props, blinds,
                    thrower_my, _norm_place)
                team_segs = sm.get("segments", {}).get(t, [])
                for seg in team_segs:
                    if seg["state"] != "controlled":
                        continue
                    acc.opportunities += 1
                    acc.rounds_seen.add(r)
                    acc.has_data = True
                    won = (winner_team == t)
                    if won:
                        acc.converted += 1
                    mid_sec = round((seg["start_sec"] + seg["end_sec"]) / 2, 2)
                    acc.add_ev(_ev("area_control", mid_sec, seg.get("place_cn") or region["place_cn"],
                                   members[t], won,
                                   f"带信号控制《{region['place_cn']}》{seg['dur']:.0f}s"
                                   f"{'并获胜' if won else '（未转化为胜利）'}",
                                   ["route", "area"], members[t],
                                   detail=f"controlled 需正向信号（{'已具备' if seg.get('signal') else '缺少'}；无 LoS 实验性）",
                                   mval=round(seg["dur"], 1)))

        # ===== 7. crossfire_candidate（hidden；保持诊断候选）=====
        for t in team_ids:
            acc = accs[(t, "crossfire_candidate")]
            contact_points = [(k["actor_x"], k["actor_y"], int(k["tick"])) for k in ks
                              if _team_of_pid(_id_for_steam(id_of, k.get("actor_steam"))) != t
                              and k.get("actor_x") is not None]
            team_ids_t = members[t]
            for cx, cy, ct in contact_points:
                possible = False; candidates = []
                for a_pid, b_pid in combinations(team_ids_t, 2):
                    a = _at(pts_by_pid.get(a_pid, {}).get("points", []), ct)
                    b = _at(pts_by_pid.get(b_pid, {}).get("points", []), ct)
                    if not a or not b or a.get("yaw") is None or b.get("yaw") is None:
                        continue
                    if _dist(a["x"], a["y"], cx, cy) > CROSSFIRE_RANGE_WORLD or _dist(b["x"], b["y"], cx, cy) > CROSSFIRE_RANGE_WORLD:
                        continue
                    possible = True
                    ang_a = math.degrees(math.atan2(cy - a["y"], cx - a["x"]))
                    ang_b = math.degrees(math.atan2(cy - b["y"], cx - b["x"]))
                    if _deg(ang_a, ang_b) >= CROSSFIRE_ANGLE_DEG:
                        candidates.append((a_pid, b_pid, _deg(ang_a, ang_b)))
                if not possible:
                    continue
                acc.opportunities += 1
                acc.rounds_seen.add(r)
                acc.has_data = True
                if candidates:
                    acc.converted += 1
                    best = max(candidates, key=lambda c: c[2])
                    sec = _sec(ct)
                    acc.add_ev(_ev("crossfire_candidate", sec,
                                   _region_cn(map_name, _place_of(pts_by_pid, best[0], ct)),
                                   [best[0], best[1]], True,
                                   f"两队友朝向同一交战点，夹角 {best[2]:.0f}°",
                                   ["gun"], [best[0], best[1]],
                                   detail="仅几何+朝向候选，无墙体遮挡（LoS）验证", mval=round(best[2])))

    # ---------- 团队级聚合 ----------
    teams_out = []
    for t in team_ids:
        tm_metrics = []
        for m in metric_ids:
            acc = accs[(t, m)]
            rate, status, pct = _rate(acc)
            coverage = len(acc.rounds_seen) / total_rounds if total_rounds else None
            conf = confidence_from_opportunities(m, acc.opportunities)
            conf = cap_confidence(m, conf)
            if m == "spacing" and acc.opportunities > 0:
                rate = acc.converted / acc.opportunities
                pct = rate * 100
            tm_metrics.append({
                "metric_id": m, "label": METRIC_LABEL[m],
                "opportunities": acc.opportunities, "converted": acc.converted,
                "rate": rate, "rate_label": f"{pct:.0f}%" if rate is not None else None,
                "human": _metric_human(m, rate),
                "coverage": coverage,
                "coverage_label": f"覆盖 {coverage*100:.0f}% 回合" if coverage is not None else None,
                "confidence": conf, "status": status,
                "trust": METRIC_TRUST.get(m, "experimental"),
                "definition": METRIC_DEFINITION.get(m, ""),
                "opportunity_meaning": METRIC_OPPORTUNITY.get(m, ""),
                "limits": METRIC_LIMITS.get(m, ""),
                "applicable": acc.opportunities > 0,
                "evidence_count": len(acc.evidence),
            })
        team_name = team_names.get(t, f"队伍{'A' if t=='A' else 'B'}")
        teams_out.append({"team": t, "name": team_name, "metrics": tm_metrics})

    # ---------- 回合级证据（episode 去重 + 限量） ----------
    for t in team_ids:
        for m in metric_ids:
            for ev in accs[(t, m)].evidence:
                ev = dict(ev)
                ev["team"] = t
                ev["players_name"] = "、".join(name_of.get(i, f"玩家{i}") for i in ev["players"])
                round_entries.append(ev)
    round_entries = _dedup_evidence(round_entries)

    # ---------- 个人贡献（focus） ----------
    player_scope = _player_contribution(focus_player_id, name_of, round_entries, teams_out)

    # ---------- 结论（3-5 条，成功与问题都要） ----------
    conclusions = _conclusions(round_entries, name_of, teams_out)

    payload = {
        "coordination_schema_version": COORDINATION_SCHEMA_VERSION,
        "thresholds_version": THRESHOLDS_VERSION,
        "match_id": artifact_rounds[0].get("match_id") if artifact_rounds else None,
        "map_name": map_name,
        "tick_rate": tick_rate,
        "total_rounds": total_rounds,
        "baseline_note": BASELINE_NOTE,
        "metric_credibility": _metric_credibility_inventory(),
        "teams": teams_out,
        "rounds": round_entries,
        "conclusions": conclusions,
        "note": "crossfire 仅命名候选（无 navmesh/LoS）；道具辅助区分 validated 真实效果与 inferred 邻近候选；"
                "区域控制为可信状态机（controlled 需正向信号，敌方缺席不升级；回合胜负仅作后续转化）；"
                "间距按 episode 秒级聚合；机会归一率/时长/覆盖率；零机会显示「不适用」（非 0 分）",
    }
    payload["content_hash"] = _content_hash(payload)
    payload["player"] = player_scope
    return payload


def _norm_place(p) -> str:
    return "".join(ch.lower() for ch in str(p or "") if ch.isalnum())


def _round_key(r: dict) -> tuple:
    return (r.get("round"), r.get("start_tick"), r.get("end_tick"), r.get("freeze_end_tick"),
            r.get("winner_team"), sorted(str(k) for k in r.get("players", {})))


def _player_contribution(focus_player_id, name_of, round_entries, teams_out):
    if focus_player_id is None:
        return {"focus_player_id": None, "name": None, "contributions": [], "exposures": [],
                "contribution_count": 0, "exposure_count": 0,
                "summary": "未指定分析对象（个人视角需先选择焦点玩家）。"}
    contributions = []
    exposures = []
    seen = set()
    for ev in round_entries:
        if focus_player_id not in ev.get("players", []):
            continue
        sig = _ev_sig(ev)
        if sig in seen:
            continue
        seen.add(sig)
        item = {"round": ev["round"], "seconds": ev.get("seconds"), "region": ev.get("region"),
                "metric_id": ev["metric_id"], "metric_label": METRIC_LABEL.get(ev["metric_id"]),
                "reason": ev.get("reason"), "layers": ev.get("layers"),
                "highlight": ev.get("highlight"), "team": ev.get("team"),
                "players": ev.get("players", []),
                "teammates": "、".join(name_of.get(i, f"玩家{i}") for i in ev.get("players", []) if i != focus_player_id)}
        (contributions if ev.get("success") else exposures).append(item)
    contributions.sort(key=lambda x: (x["round"], x.get("seconds") or 0))
    exposures.sort(key=lambda x: (x["round"], x.get("seconds") or 0))
    name = name_of.get(focus_player_id)
    n_c = len(contributions); n_e = len(exposures)
    summary = (f"{name} 在本场参与 {n_c} 次协同成功、暴露 {n_e} 次协同问题。"
               f"团队整体结果见『团队』视图，个人贡献仅反映其角色，不代表团队胜负归因。")
    return {"focus_player_id": focus_player_id, "name": name,
            "contributions": contributions[:5], "exposures": exposures[:5],
            "contribution_count": n_c, "exposure_count": n_e, "summary": summary}


_PROP_CN = {"smoke": "烟雾", "fire": "燃烧", "he": "手雷", "flash": "闪光"}


def _conclusion_text(m, success, ev):
    r = ev.get("round", "?")
    region = ev.get("region") or "未知区域"
    pn = ev.get("players_name") or "相关玩家"
    v = ev.get("metric_value")
    if success:
        if m == "trade":
            return (f"R{r}·{region}：{pn} 在队友阵亡后完成补枪",
                    "及时补枪把人数劣势拉回，残局更平衡",
                    "提前卡住击杀者位置，队友倒地瞬间就位")
        if m == "simultaneous_contact":
            n = v or 2
            return (f"R{r}·{region}：{pn} 等 {n} 名队友同一波开火",
                    "同时开火形成火力优势，对面难以招架",
                    "拉开但不脱节，约定同一波开火时机")
        if m == "utility_assisted_entry":
            t = _PROP_CN.get(v, "道具")
            return (f"R{r}·{region}：{pn} 在队友{t}掩护下完成进点",
                    "道具给进攻创造了空间，进点更有把握",
                    "进攻前先投道具，再跟枪线进点")
        if m == "area_control":
            vv = v if isinstance(v, (int, float)) else ""
            return (f"R{r}·{region}：队伍带信号控制{'约' + str(int(vv)) + 's' if vv else ''}并赢得回合",
                    "控制关键区域带来地图主动权，转化为回合胜利",
                    "拿下区域后及时收缩守点，别让控制权白拿")
        if m == "crossfire_candidate":
            ang = f"{int(v)}°" if isinstance(v, (int, float)) else "较大夹角"
            return (f"R{r}·{region}：{pn} 两把枪线夹角 {ang}，形成交叉候选",
                    "两把枪线覆盖不同角度，对面更难同时应对",
                    "站位拉开两把枪线夹角（仍为候选，无视野遮挡验证）")
        if m == "spacing":
            return (f"R{r}·{region}：队友间距纪律良好",
                    "合理间距不易被一颗雷或火伤到多人",
                    "保持当前站位，既互相支援又不过分扎堆")
    else:
        if m == "area_control":
            vv = v if isinstance(v, (int, float)) else ""
            return (f"R{r}·{region}：队伍带信号控制区域{'约' + str(int(vv)) + 's' if vv else ''}但未转化成回合",
                    "有控制权却没守住或滚起雪球，优势被浪费",
                    "拿下区域后立刻组织防守或转点，别让控制权白拿")
        if m == "isolated_death":
            dist = f"约{int(v)}单位" if isinstance(v, (int, float)) else "较远"
            return (f"R{r}·{region}：{pn} 孤立阵亡（无路径验证），队友支援不上",
                    f"阵亡时最近队友距离 {dist}，已超出可支援范围",
                    "注意与最近队友的距离，避免单独深入")
        if m == "trade":
            return (f"R{r}·{region}：{pn} 倒地后未完成补枪",
                    "击杀者没被及时处理，对面白拿一个人头",
                    "倒地后立即报点击杀者方位，队友提前卡住角度")
        if m == "spacing":
            return (f"R{r}·{region}：队友间距扎堆，容易被一波带走",
                    "扎堆会放大道具伤害，也给对面连爆窗口",
                    "分散站位，留出安全间距与撤退路线")
        if m == "utility_assisted_entry":
            return (f"R{r}·{region}：{pn} 的道具没换来进点击杀",
                    "道具没有转化成人头，浪费了节奏与资源",
                    "根据队友枪位选道具，投完立刻补枪跟进")
        if m == "simultaneous_contact":
            return (f"R{r}·{region}：队友开火节奏不齐，被逐个应对",
                    "火力分散让对面能逐个击破，协同没打出来",
                    "约定同一波开火时机，避免一人先冲")
    return (f"R{r}·{region}：{pn} 相关的一处协同",
            "这一处击决定该回合走向",
            "针对这处协同练习下一次应对")


def _conclusions(round_entries, name_of, teams_out):
    success_order = ["utility_assisted_entry", "trade", "simultaneous_contact", "area_control", "crossfire_candidate"]
    problem_order = ["isolated_death", "trade", "spacing", "utility_assisted_entry", "simultaneous_contact"]
    rank = {m: i for i, m in enumerate(success_order + [x for x in problem_order if x not in success_order])}

    by_group: dict[tuple[str, str], dict] = {}
    for ev in round_entries:
        key = (ev["metric_id"], "success" if ev.get("success") else "problem")
        sig = _ev_sig(ev)
        reps = by_group.setdefault(key, {})
        if sig not in reps:
            reps[sig] = ev
    success_cands = []
    problem_cands = []
    for (m, tone), reps in by_group.items():
        if tone == "success" and METRIC_TRUST.get(m) == "hidden":
            continue
        best = min(reps.values(), key=lambda e: (rank.get(e["metric_id"], 99), e["round"], e.get("seconds") or 0))
        (success_cands if tone == "success" else problem_cands).append(best)

    chosen = []
    used_metric = set()

    def _pick(pool, allow_repeat):
        for cand in pool:
            if cand in chosen:
                continue
            if cand["metric_id"] in used_metric and not allow_repeat:
                continue
            chosen.append(cand); used_metric.add(cand["metric_id"]); return True
        return False

    _pick(success_cands, False)
    _pick(problem_cands, False)
    turn_success = True
    while len(chosen) < MAX_CONCLUSIONS:
        pool = success_cands if turn_success else problem_cands
        if not _pick(pool, allow_repeat=len(chosen) >= 3):
            if not _pick(problem_cands if turn_success else success_cands, allow_repeat=len(chosen) >= 3):
                break
        turn_success = not turn_success
    while len(chosen) < 3:
        if not _pick(success_cands + problem_cands, True):
            break

    def _metric_conf(m):
        confs = []
        for t in teams_out:
            for mt in t["metrics"]:
                if mt["metric_id"] == m and mt["confidence"] != "none":
                    confs.append(mt["confidence"])
        return max(confs) if confs else "low"

    conclusions = []
    for ev in chosen:
        m = ev["metric_id"]
        success = bool(ev.get("success"))
        title, why, next_ = _conclusion_text(m, success, ev)
        conclusions.append({
            "id": f"c{len(conclusions)+1}",
            "metric_id": m, "label": METRIC_LABEL.get(m, m),
            "tone": "success" if success else "problem",
            "signature": _sig_as_list(ev),
            "title": title, "what": title, "why": why, "next": next_,
            "round": ev["round"], "seconds": ev.get("seconds"), "region": ev.get("region"),
            "players": ev.get("players"), "players_name": ev.get("players_name") or "、".join(name_of.get(i, f"玩家{i}") for i in ev.get("players", [])),
            "highlight": ev.get("highlight"), "layers": ev.get("layers"),
            "confidence": _metric_conf(m),
        })
    return conclusions
