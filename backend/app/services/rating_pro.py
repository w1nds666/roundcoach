"""6R10B：个人 Rating Pro v1（透明·版本化·权威字段·确定性）。

- 参考公开、可观测的职业统计思想，但**绝不宣称精确复刻 HLTV Rating 2.0/3.0**。
- 只使用有权威证据的成分：击杀/首杀/多杀（frag）、存活率（survival）、
  KAST（含可证明的 traded）、ADR、补枪/交易贡献（trade）。
  经济影响仅在能从真实 demo 字段权威证明时加入，否则 unavailable（本版不加入，绝不补假数据）。
- 原始分量在**解析期**由 parse_tasks 计算并存入 player_stats.rating_raw（含分子分母与状态），
  读取时按「验收样本内标定」的归一化常数（rating_pro_calibration.json）与权重合成最终 Rating。
- 确定性：所有阈值/权重/常数集中在配置；无 NaN/Infinity；零机会 → 该分量标记 no_opportunity
  （不计入加权，而不是当作 0 分）；全部不可用 → rating=null + 原因。
- provisional：均值约 1.00 仅为「本批验收样本内」试验性基线，绝不伪装成行业/全球基准。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from app.services.coordination_thresholds import TRADE_WINDOW_SEC

RATING_PRO_VERSION = "1.0"
RATING_RAW_SCHEMA = "rating_raw_v1"
PROVISIONAL = True
REFERENCE_SCOPE = "本批验收样本内（demo 19–23，约 50 player-match）试验性基线，非 HLTV/行业/全球基准"
NOT_HLTV_NOTE = "不等于也不代表 HLTV 官方 Rating，本产品自研口径，仅供个人参考"

# 权重（和为 1；本产品自研口径，非 HLTV）
WEIGHTS = {"frag": 0.30, "kast": 0.25, "survival": 0.20, "adr": 0.15, "trade": 0.10}

# 各分量人话说明
COMP_LABEL = {
    "frag": "击杀贡献",
    "kast": "KAST",
    "survival": "存活率",
    "adr": "回合伤害",
    "trade": "补枪/交易贡献",
}

# 分量公式（供技术口径展开）
COMP_FORMULA = {
    "frag": "(击杀 + 0.4×首杀 + 0.3×多杀回合) / 回合数",
    "survival": "存活回合数 / 回合数",
    "kast": "击杀|助攻|存活|交易 覆盖回合数 / 回合数（含可证明交易）",
    "adr": "总伤害 / 回合数",
    "trade": "本人成功补枪次数 / 本人可补枪机会数",
}

RATING_MIN = 0.0
RATING_MAX = 3.0

_MODULE_DIR = Path(__file__).resolve().parent
CALIBRATION_FILE = _MODULE_DIR / "rating_pro_calibration.json"

# 权重和为 1 断言（单测校验）
def _weights_sum_ok() -> bool:
    return abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def _load_cal() -> dict:
    """读取验收样本标定常数（来自 rating_pro_calibration.json）；缺省用保守回退并标 provisional。"""
    try:
        data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        if data.get("version") == RATING_PRO_VERSION:
            return data.get("cal", {})
    except (OSError, ValueError):
        pass
    return {}


CAL = _load_cal()


def _fin(v) -> float | None:
    """JSON 安全数值：NaN/Inf/None → None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------- 解析期：原始分量 ----------

def _alive_at_tick(death_by_rp, pid, rnd, tick) -> bool:
    """玩家在某回合 tick 时刻是否存活（以死亡事件推断：该回合无更早死亡事件视为存活）。"""
    dt = death_by_rp.get((rnd, pid))
    return dt is None or dt > tick


def _killed_within_window(kills_detail, trader, target, rnd, from_tick, tick_rate) -> bool:
    """trader 在 [from_tick, from_tick+window] 内击杀 target。"""
    if not tick_rate:
        return False
    limit = from_tick + TRADE_WINDOW_SEC * tick_rate
    for k in kills_detail.get(rnd, []):
        if k["actor"] == trader and k["victim"] == target and from_tick < k["tick"] <= limit:
            return True
    return False


def compute_rating_raw(rounds, deaths, stats, steamids, identity_of, tick_rate):
    """解析期计算每玩家 Rating Pro 原始分量（确定性；权威字段）。

    rounds: 正式回合（1..N）；deaths: 已注入 round 的死亡事件（备战=0/None）
    stats: {steamid: {kills, deaths, assists, damage, adr, first_kills, hs_kills}}
    identity_of: {steamid: 'A'|'B'}；tick_rate: 权威时基（None 则交易窗口无法换算）
    返回 {steamid: rating_raw}（rating_raw None 表示无正式回合/无数据）。
    """
    valid = set(int(r["round"]) for r in rounds)
    N = len(rounds)
    if N == 0:
        return {sid: None for sid in steamids}

    # 索引（round >= 1 的正式回合事件）
    kills_by_rp: dict[tuple, int] = {}      # (rnd, actor) -> count
    death_by_rp: dict[tuple, int] = {}      # (rnd, victim) -> death tick
    assist_by_rp: set[tuple] = set()        # (rnd, assister)
    kills_detail: dict[int, list[dict]] = {}  # rnd -> [{actor,victim,tick}]
    for d in deaths:
        r = d.get("round")
        if r not in valid:
            continue
        r = int(r); tk = int(d.get("tick") or 0)
        actor = str(d.get("attacker_steamid") or "")
        victim = str(d.get("user_steamid") or "")
        assister = str(d.get("assister_steamid") or "")
        if actor:
            kills_by_rp[(r, actor)] = kills_by_rp.get((r, actor), 0) + 1
            kills_detail.setdefault(r, []).append({"actor": actor, "victim": victim, "tick": tk})
        if victim:
            death_by_rp[(r, victim)] = tk
        if assister:
            assist_by_rp.add((r, assister))

    out = {}
    for sid in steamids:
        st = stats.get(sid)
        if st is None:
            out[sid] = None
            continue
        frac = {}
        # --- frag ---
        multi = sum(1 for r in range(1, N + 1) if kills_by_rp.get((r, sid), 0) >= 2)
        frag_raw = (float(st["kills"]) + 0.4 * float(st["first_kills"]) + 0.3 * multi) / N
        frac["frag"] = {"numerator": round(float(st["kills"]) + 0.4 * float(st["first_kills"]) + 0.3 * multi, 4),
                        "denominator": N, "value": round(frag_raw, 5)}
        # --- survival ---
        death_rounds = {r for (r, pid) in death_by_rp if pid == sid}
        survived = max(0, N - len(death_rounds))
        frac["survival"] = {"numerator": survived, "denominator": N, "value": round(survived / N, 5)}
        # --- kast (K/A/S/T) ---
        kast = 0
        for r in range(1, N + 1):
            K = kills_by_rp.get((r, sid), 0) >= 1
            A = (r, sid) in assist_by_rp
            S = r not in death_rounds   # 该回合玩家未被击杀（未死）即算存活
            T = False
            dt = death_by_rp.get((r, sid))
            if dt is not None and tick_rate:
                # traded：本回合击杀该玩家的凶手，被同队任何队友在窗口内补掉
                killer_evs = [k for k in kills_detail.get(r, [])
                              if k["victim"] == sid and k["actor"] and k["actor"] != sid]
                for kk in killer_evs:
                    killer = kk["actor"]
                    for k2 in kills_detail.get(r, []):
                        if (k2["actor"] and k2["victim"] == killer
                                and identity_of.get(k2["actor"]) == identity_of.get(sid)
                                and k2["actor"] != sid
                                and kk["tick"] < k2["tick"] <= kk["tick"] + TRADE_WINDOW_SEC * tick_rate):
                            T = True
                            break
                    if T:
                        break
            if K or A or S or T:
                kast += 1
        frac["kast"] = {"numerator": kast, "denominator": N, "value": round(kast / N, 5)}
        # --- adr ---
        frac["adr"] = {"value": round(float(st["adr"]), 2)}
        # --- trade (player as trader) ---
        opp = 0
        conv = 0
        for r in range(1, N + 1):
            for d in kills_detail.get(r, []):
                victim = d["victim"]; actor = d["actor"]
                if not victim or not actor:
                    continue
                v_team = identity_of.get(victim); a_team = identity_of.get(actor)
                if not v_team or not a_team or v_team == a_team:
                    continue
                tk = d["tick"]
                if sid == victim or identity_of.get(sid) != v_team:
                    continue
                if not _alive_at_tick(death_by_rp, sid, r, tk):
                    continue
                opp += 1
                if _killed_within_window(kills_detail, sid, actor, r, tk, tick_rate):
                    conv += 1
        if opp > 0:
            frac["trade"] = {"numerator": conv, "denominator": opp, "value": round(conv / opp, 5), "status": "ok"}
        else:
            frac["trade"] = {"numerator": 0, "denominator": 0, "value": None, "status": "no_opportunity"}
        out[sid] = {"schema": RATING_RAW_SCHEMA, "version": RATING_PRO_VERSION,
                    "rounds": N, "tick_rate": tick_rate, "components": frac}
    return out


# ---------- 读取期：合成最终 Rating ----------

def _norm(value, cal_key, cal):
    c = cal.get(cal_key)
    if not c or c <= 0:
        return None
    v = value / c
    v = max(RATING_MIN, min(RATING_MAX, v))
    return round(v, 4)


def build_rating(rating_raw: dict | None, cal: dict | None = None):
    """由 rating_raw 合成 Rating Pro v1 响应（确定性；零机会不当作 0 分）。"""
    cal = cal if cal is not None else CAL
    if rating_raw is None:
        return {"rating": None, "version": RATING_PRO_VERSION, "status": "unavailable",
                "reason": "该玩家无 Rating Pro 数据（0 正式回合或未计算）。"}
    comps = rating_raw.get("components")
    rounds = _fin(rating_raw.get("rounds"))
    if not comps or not rounds or rounds <= 0:
        return {"rating": None, "version": RATING_PRO_VERSION, "status": "unavailable",
                "reason": "无有效正式回合，无法计算 Rating Pro。"}

    avail = {}
    exclusions = []
    for key in WEIGHTS:
        c = comps.get(key) or {}
        value = _fin(c.get("value"))
        status = c.get("status")
        if value is None or status == "no_opportunity":
            exclusions.append({"component": key, "label": COMP_LABEL.get(key, key),
                               "status": status or "unavailable",
                               "note": "无机会（no_opportunity）不计为 0 分" if status == "no_opportunity" else "数据缺失"})
            continue
        norm = _norm(value, key, cal)
        if norm is None:
            exclusions.append({"component": key, "label": COMP_LABEL.get(key, key),
                               "status": "calibration_missing",
                               "note": "缺少该分量标定常数，暂不计分"})
            continue
        avail[key] = {"value": round(_fin(c["value"]), 5), "norm": norm,
                      "weight": WEIGHTS[key], "status": "ok",
                      "numerator": c.get("numerator"), "denominator": c.get("denominator")}

    if not avail:
        return {"rating": None, "version": RATING_PRO_VERSION, "status": "unavailable",
                "reason": "所有分量均不可用（无机会/缺数据/缺标定），不给出分数。"}

    total_w = sum(a["weight"] for a in avail.values())
    rating = sum(a["norm"] * a["weight"] for a in avail.values()) / total_w
    rating = round(max(RATING_MIN, min(RATING_MAX, rating)), 2)

    # confidence：分量覆盖数 + 回合样本量
    n_avail = len(avail)
    if n_avail >= 5 and rounds >= 20:
        confidence = "high"
    elif n_avail >= 4 and rounds >= 12:
        confidence = "medium"
    elif n_avail >= 3:
        confidence = "low"
    else:
        confidence = "low"

    components_out = []
    for key in WEIGHTS:
        if key in avail:
            a = avail[key]
            components_out.append({
                "key": key, "label": COMP_LABEL[key], "weight": a["weight"],
                "value": a["value"], "normalized": a["norm"], "status": a["status"],
                "formula": COMP_FORMULA[key],
                "numerator": a["numerator"], "denominator": a["denominator"],
                "human": _component_human(key, a["value"], a["denominator"]),
            })
        else:
            ex = next((e for e in exclusions if e["component"] == key), None)
            components_out.append({
                "key": key, "label": COMP_LABEL[key], "weight": WEIGHTS[key],
                "value": None, "normalized": None, "status": (ex or {}).get("status", "unavailable"),
                "formula": COMP_FORMULA[key], "numerator": None, "denominator": None,
                "human": (ex or {}).get("note", "无数据/无机会"),
            })

    return {
        "rating": rating,
        "version": RATING_PRO_VERSION,
        "status": "available",
        "provisional": PROVISIONAL,
        "confidence": confidence,
        "coverage": {"rounds": rounds, "total_rounds": rounds, "ratio": 1.0},
        "reference_scope": REFERENCE_SCOPE,
        "not_hltv_note": NOT_HLTV_NOTE,
        "weights": {k: v for k, v in WEIGHTS.items()},
        "formula": "rating = Σ(权重×归一化分量) / Σ(可用权重)",
        "explanation": _explanation(round(rating, 2), confidence, len(avail), exclusions),
        "components": components_out,
        "exclusions": exclusions,
    }


def _component_human(key, value, denominator):
    if value is None:
        return None
    if key == "frag":
        return f"回合均击杀贡献 {value:.2f}（含首杀/多杀加成）"
    if key == "survival":
        return f"存活率 {value:.0%}"
    if key == "kast":
        return f"KAST {value:.0%}（击杀/助攻/存活/交易覆盖回合）"
    if key == "adr":
        return f"回合均伤害 {value:.0f}"
    if key == "trade":
        return f"补枪转化 {value:.0%}"
    return None


def _explanation(rating, confidence, n_avail, exclusions):
    base = (f"本场 Rating Pro 为 {rating}。它是按击杀、KAST、存活率、回合伤害与补枪贡献"
            f"五个维度加权合成，其中 {n_avail} 个维度有据可算。")
    conf = {"high": "数据充分，结果较稳定", "medium": "部分维度缺乏充分样本，结果仅供参考",
            "low": "可用维度偏少或样本有限，结果仅作粗略参考"}.get(confidence, "仅供参考")
    note = ""
    if exclusions:
        names = "、".join(e["label"] for e in exclusions)
        note = f"（{names} 因无机会或数据缺失未计分，而非 0 分）"
    return f"{base}{conf}{note}。此分值为本产品自研口径，不等于 HLTV 官方 Rating。"
