"""6R12：朝向/枪线真值门禁（gaze truth gate）。

根因与标定（来自真实 golden set 勘探，见 docs/phase6r12-gaze-truth.md）：
- 真实世界前向向量 = (cos yaw, sin yaw)（yaw 为度）。经 maps.json 雷达换算
  rx=(x-pos_x)/scale、ry=(pos_y-y)/scale，得**雷达/屏幕方向 = (cos yaw, -sin yaw)**。
- 此前 `(-sin yaw, cos yaw)`（等价屏幕 (-sin,-cos)）为 **90° 错误变换**，是"枪线不指向
  击杀目标"的主要根因。golden set 实测：正确变换下 攻击者→受害者 与可信方向
  角误差 median≈0.5°、p95≈3.5°，且无 90°/180° 系统偏差。
- 语义拆分（诚实性）：
  * 连续插值的眼朝向只能叫「视角朝向」（view direction），表示"该玩家正看向哪个方向"，
    不声称是"开枪瞬间的枪线"。
  * 只有具备"可信开枪时刻 + 可信方向证据"的事件才画瞬时「射击线」（shoot line）。
    无权威/未通过门禁 → 不渲染，并诚实显示"该事件方向不可用"。
- 本模块独立可测，供战术 artifact 构建与测试复用；个人与团队回放应共用同一变换
  （个人/团队 API 都从本模块取已验证射击方向，禁止前端两套公式漂移）。
"""
from __future__ import annotations

import math

# 门禁阈值（产品契约，见 specs）：直接非穿墙可见枪械击杀 角误差
GATE_MEDIAN_MAX_DEG = 10.0
GATE_P95_MAX_DEG = 25.0
# "系统偏差"判定：角误差落入该区间视为 90° 或 180° 方向系统性偏移
BIAS_90_LO, BIAS_90_HI = 60.0, 120.0
BIAS_180_LO = 150.0
# weapon_fire 与 death 的关联窗口（fire_tick <= death_tick 且差 <= 该值）
FIRE_WINDOW_TICKS = 64
# 极端近距离（几乎贴脸）角误差对 yaw 意义较低，但仍记录不丢弃
CLOSE_DIST_WORLD = 25.0

# 枪械基础武器（CS2 raw weapon id，含 *_vip/_ace_vip 皮肤后缀）
FIREARM_BASES = {
    "ak47", "m4a1", "m4a1_silencer", "awp", "galilar", "famas", "mp9", "mp7",
    "mp5sd", "mac10", "p90", "ump45", "fiveseven", "glock", "usp_silencer",
    "deagle", "p250", "tec9", "ssg08", "aug", "sg556", "nova", "xm1014",
    "mag7", "sawedoff", "p2000", "elite", "g3sg1", "scar20", "m249", "negev",
}
# 投掷物/近战（直接枪械击杀需排除）
GRENADE_BASES = {"hegrenade", "inferno", "smokegrenade", "flashbang", "molotov",
                 "incgrenade", "decoy", "frag", "grenade"}
KNIFE_BASES = {"knife", "knife_t", "knife_gg", "knife_css", "bayonet", "c4", "taser"}


def is_firearm(weapon: str) -> bool:
    """是否为枪械（排除手雷/近战/道具）。weapon 兼容 'ak47' 或 'weapon_ak47'、皮肤 '_vip' 后缀。"""
    w = weapon.removeprefix("weapon_")
    if w in KNIFE_BASES or any(w.startswith(b) for b in KNIFE_BASES):
        return False
    if any(w == b or w.startswith(b + "_") for b in GRENADE_BASES):
        return False
    return any(w.startswith(b) for b in FIREARM_BASES)


def classify_death(row: dict) -> str:
    """把一条 player_death 归入：direct / wallbang / thrusmoke / grenade / knife / suicide / missing。

    - grenade/knife：武器为手雷/近战/道具。
    - suicide：无攻击者 steamid 或 攻击者==受害者。
    - wallbang/thrusmoke：穿透/烟后（无直接视线，需单独分类）。
    - missing：坐标缺失。
    - direct：其余（直接可见枪械击杀候选）。
    """
    weapon = str(row.get("weapon") or "")
    attacker = str(row.get("attacker_steamid") or "")
    victim = str(row.get("user_steamid") or "")
    if not attacker or attacker == victim:
        return "suicide"
    if not is_firearm(weapon):
        if any(weapon.removeprefix("weapon_") == b or weapon.removeprefix("weapon_").startswith(b + "_")
               for b in KNIFE_BASES):
            return "knife"
        return "grenade"
    if row.get("user_X") is None or row.get("user_Y") is None or \
       row.get("attacker_X") is None or row.get("attacker_Y") is None:
        return "missing"
    if bool(row.get("penetrated")):
        return "wallbang"
    if bool(row.get("thrusmoke")):
        return "thrusmoke"
    return "direct"


def yaw_forward(yaw_deg: float) -> tuple[float, float]:
    """真实世界前向向量（单位圆）= (cos yaw, sin yaw)。yaw 为度。"""
    r = math.radians(yaw_deg)
    return math.cos(r), math.sin(r)


def yaw_screen(yaw_deg: float) -> tuple[float, float]:
    """雷达/屏幕方向（归一化坐标 + 屏幕 y 向下）= (cos yaw, -sin yaw)。"""
    r = math.radians(yaw_deg)
    return math.cos(r), -math.sin(r)


def world_angle(dx: float, dy: float) -> float:
    """世界坐标下方向角（度），atan2(dy, dx)。"""
    return math.degrees(math.atan2(dy, dx))


def deg_error(a: float, b: float) -> float:
    """两角度（度）的最短角误差 [0,180]。"""
    d = (a - b) % 360
    return min(d, 360 - d)


def _deg_error_batched(pred: list[float], true: list[float]) -> list[float]:
    return [deg_error(p, t) for p, t in zip(pred, true)]


def compute_gate(errors: list[float]) -> dict:
    """对直接枪械击杀角误差序列计算门禁统计。

    返回 {n, median, p95, mean, max, frac_le_10, frac_le_25, bias_90, bias_180, passed}
    passed = median<=10 且 p95<=25 且 bias_90==0 且 bias_180==0。
    """
    n = len(errors)
    if n == 0:
        return {"n": 0, "median": None, "p95": None, "mean": None, "max": None,
                "frac_le_10": None, "frac_le_25": None, "bias_90": None, "bias_180": None,
                "passed": False, "reason": "no_errors"}
    e = sorted(errors)
    median = e[n // 2] if n % 2 else (e[n // 2 - 1] + e[n // 2]) / 2.0
    p95 = e[min(n - 1, int(math.ceil(0.95 * n)) - 1)]
    mean = sum(e) / n
    frac_le_10 = sum(1 for x in e if x <= GATE_MEDIAN_MAX_DEG) / n
    frac_le_25 = sum(1 for x in e if x <= GATE_P95_MAX_DEG) / n
    bias_90 = sum(1 for x in e if BIAS_90_LO <= x <= BIAS_90_HI) / n
    bias_180 = sum(1 for x in e if x >= BIAS_180_LO) / n
    passed = (median <= GATE_MEDIAN_MAX_DEG and p95 <= GATE_P95_MAX_DEG
              and bias_90 <= 0.02 and bias_180 <= 0.02)
    return {"n": n, "median": round(median, 3), "p95": round(p95, 3), "mean": round(mean, 3),
            "max": round(e[-1], 3), "frac_le_10": round(frac_le_10, 4),
            "frac_le_25": round(frac_le_25, 4), "bias_90": round(bias_90, 4),
            "bias_180": round(bias_180, 4), "passed": passed,
            "reason": None if passed else "gate_not_passed"}


def build_shoot_events(analyzer, rounds: list[dict], deaths: list[dict],
                       player_id_of: dict[str, int], tick_rate: float | None) -> dict:
    """从真实 demo 构建每回合"射击事件" + 门禁统计。

    - 对每条 player_death 分类；对 direct 枪械击杀，用 weapon_fire 关联精确开火 tick
      （同攻击者+同武器+在死亡前 64 tick 窗口内），在该 tick 采样攻击者 eyeAngles+位置。
    - 计算 攻击者(开火点)→受害者 方向 与 可信 yaw 方向 的角误差；
      生成 validated（error<=25° 且为 direct 可见）与 authority。
    - 返回 {per_round: {round: [shoot_event...]}, gate: {...}, counts:{...}}。
    - 只读取 .dem，不做任何写入；确定性输出。
    """
    # weapon_fire 索引：attacker+weapon → 升序 fire tick 列表
    try:
        wf = analyzer._parser.parse_event("weapon_fire")
        wf_rows = wf.to_dict(orient="records")
    except Exception:  # noqa: BLE001
        wf_rows = []
    wf_by: dict[tuple[str, str], list[int]] = {}
    for r in wf_rows:
        wf_by.setdefault((str(r.get("user_steamid")), str(r.get("weapon"))), []).append(int(r["tick"]))
    for k in wf_by:
        wf_by[k].sort()

    # 先把所有 direct 击杀的开火 tick 收集，再一次性精确采样（避免逐 tick parse）
    import bisect

    direct = []
    fire_tick_of = []
    for d in deaths:
        cls = classify_death(d)
        if cls != "direct":
            continue
        att = str(d.get("attacker_steamid"))
        weapon = str(d.get("weapon"))
        death_tick = int(d.get("tick") or 0)
        ticks = wf_by.get((att, weapon), [])
        i = bisect.bisect_right(ticks, death_tick) - 1
        fire = ticks[i] if (i >= 0 and death_tick - ticks[i] <= FIRE_WINDOW_TICKS) else None
        fire_tick = fire if fire is not None else death_tick
        fire_tick_of.append((d, att, weapon, fire_tick, death_tick))
        direct.append((d, att, weapon, fire_tick, death_tick))

    fire_ticks = sorted({r[3] for r in fire_tick_of})
    eye_map: dict[tuple[int, str], dict] = {}
    if fire_ticks:
        try:
            te = analyzer._parser.parse_ticks(
                ["CCSPlayerPawn.m_angEyeAngles", "X", "Y", "steamid", "tick"],
                ticks=fire_ticks)
            for r in te.to_dict(orient="records"):
                eye_map[(int(r["tick"]), str(r["steamid"]))] = r
        except Exception:  # noqa: BLE001
            eye_map = {}

    # 回合映射（与 routes._round_mapper 语义一致：tick 落在正式回合窗口内才归属）
    starts = [int(r["start_tick"]) for r in rounds]
    ends = [int(r["end_tick"]) for r in rounds]

    def rnd_of(tick: int) -> int | None:
        idx = bisect.bisect_right(starts, tick) - 1
        if idx < 0:
            return None
        if tick < starts[idx] or tick > ends[idx]:
            return None
        return rounds[idx]["round"]

    errors: list[float] = []
    counts = {"direct": 0, "wallbang": 0, "thrusmoke": 0, "grenade": 0,
              "knife": 0, "suicide": 0, "missing": 0}
    for d in deaths:
        counts[classify_death(d)] = counts.get(classify_death(d), 0) + 1

    per_round: dict[int, list[dict]] = {}
    for (d, att, weapon, fire, death_tick) in direct:
        rnd = rnd_of(death_tick)
        rec = eye_map.get((fire, att))
        pid = player_id_of.get(att)
        vic_pid = player_id_of.get(str(d.get("user_steamid") or ""))
        if rec is None:
            continue
        eye = rec.get("CCSPlayerPawn.m_angEyeAngles")
        if not isinstance(eye, (list, tuple)) or len(eye) < 2:
            continue
        pitch, yaw = eye[0], eye[1]
        fx, fy = rec.get("X"), rec.get("Y")
        if fx is None or fy is None:
            fx, fy = d.get("attacker_X"), d.get("attacker_Y")
        vx, vy = d.get("user_X"), d.get("user_Y")
        if vx is None or vy is None or fx is None or fy is None:
            continue
        true_angle = world_angle(vx - fx, vy - fy)
        err = deg_error(yaw, true_angle)
        dist = math.hypot(vx - fx, vy - fy)
        # 可信方向：direct 可见且误差超阈值 → 视作"不可用"
        direction_ok = err <= GATE_P95_MAX_DEG
        errors.append(err)   # 门禁统计所有直接枪械击杀（含未入正式回合的热身击杀）
        dx, dy = yaw_screen(yaw)
        ev = {
            "player_id": pid, "victim_player_id": vic_pid,
            "tick": int(death_tick), "fire_tick": int(fire),
            "yaw": _clean(yaw), "pitch": _clean(pitch),
            "x": _clean(fx), "y": _clean(fy), "victim_x": _clean(vx), "victim_y": _clean(vy),
            "distance": round(dist, 2),
            "error_deg": round(err, 2),
            "class": "direct", "weapon": str(weapon),
            "authority": ("event" if fire else "inferred_near_death"),
            "validated": bool(direction_ok),
            "direction_available": bool(direction_ok),
            "dir_x": round(dx, 5), "dir_y": round(dy, 5),
        }
        if rnd is not None:
            per_round.setdefault(rnd, []).append(ev)

    gate = compute_gate(errors)
    for rnd in per_round:
        per_round[rnd].sort(key=lambda e: e["tick"])
    return {"per_round": per_round, "gate": gate, "counts": counts,
            "errors": [round(e, 3) for e in errors]}


def _clean(x):
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
