"""6R15：长期教练 —— 候选问题定义 / 置信度 / 重复/趋势/Top3 纯函数（确定性、版本化）。

权威依据：docs/phase6r15-coach-formula.md（§2 候选问题、§3 重复、§4 趋势、§5 置信度、§6 Top3）。
所有实现必须与本文件一致；若冲突以 formula 文档为准。
"""
from __future__ import annotations

import math

COACH_SCHEMA_VERSION = "1.0"
COACH_METRIC_VERSION = "1.0"

# ---- §2 候选问题（candidate problems）----
# 每项：id / metric / direction(bad=low|high) / threshold / denominator / impact_weight /
#      human_name / impact_human / advice。阈值与权重为 MVP 固定经验值（版本化）。
CANDIDATE_PROBLEMS: list[dict] = [
    {
        "id": "low_adr",
        "metric": "adr",
        "direction": "low",
        "threshold": 70.0,
        "denominator": "rounds",
        "impact_weight": 1.0,
        "human_name": "对枪输出偏低",
        "impact_human": "对枪输出长期偏低会拉低单回合威胁，难以形成常态压制。",
        "advice": "复盘对枪时的站位与预瞄，优先打有信息差的枪，别和对面对换。",
    },
    {
        "id": "high_deaths",
        "metric": "deaths_per_round",
        "direction": "high",
        "threshold": 0.55,
        "denominator": "rounds",
        "impact_weight": 1.0,
        "human_name": "阵亡偏多",
        "impact_human": "阵亡率过高会拖累存活与节奏，给对面人多打人少的机会。",
        "advice": "减少无信息时的单独深入，多跟队友抱团并注意撤退路线。",
    },
    {
        "id": "low_hs_rate",
        "metric": "hs_rate",
        "direction": "low",
        "threshold": 0.25,
        "denominator": "kills",
        "impact_weight": 0.8,
        "human_name": "爆头完成率偏低",
        "impact_human": "爆头完成率低说明击杀效率不足，容易在对枪中吃亏。",
        "advice": "练习把准星压到头部高度，逐步提高一枪击杀的比例。",
    },
    {
        "id": "low_kd",
        "metric": "kd_ratio",
        "direction": "low",
        "threshold": 0.90,
        "denominator": "kills_deaths",
        "impact_weight": 1.0,
        "human_name": "击杀/阵亡比偏低",
        "impact_human": "击杀与阵亡比偏低说明正贡献不足，队伍需要你活着才有输出。",
        "advice": "先保命再抢人头，避免无意义的换枪与送头。",
    },
    {
        "id": "low_first_kill",
        "metric": "first_kill_rate",
        "direction": "low",
        "threshold": 0.12,
        "denominator": "rounds",
        "impact_weight": 0.9,
        "human_name": "开局首杀偏少",
        "impact_human": "开局首杀少，队伍难以在回合早期建立人数优势。",
        "advice": "开局抢先拿到有利对位，争取第一波交火的首杀。",
    },
    {
        "id": "low_clutch",
        "metric": "clutches_won",
        "direction": "low",
        "threshold": 0.0,
        "denominator": "clutch_opportunities",
        "impact_weight": 0.7,
        "human_name": "残局胜出不足",
        "impact_human": "残局把控弱会浪费已建立的优势，残局数据常稀缺。",
        "advice": "残局先算清双方位置与血量，再决定架点还是转点。",
    },
]

CANDIDATE_BY_ID: dict[str, dict] = {c["id"]: c for c in CANDIDATE_PROBLEMS}

# 残局机会分母：MVP 无法从 player_stats 证明实际残局出现数，置 0（不可评估）。
# 公式 §2 明确「不可证时置 0」「样本少时不计入 Top3 权重」。
CLUTCH_OPPORTUNITY_VALID = False

# ---- §5 置信度 ----
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
_CONF_SCORE = {"high": 1.0, "medium": 0.7, "low": 0.4}


def compute_confidence(metric_value: float | None, hits: int, valid_matches: int, window_matches: int) -> str:
    """置信度（§5）：基础档(validated→high)、命中场次档、coverage 档，三者取低。

    - 基础档：validated 基础统计 → high。
    - 命中场次 hits：<2 → low；2–4 → medium；>4 → high。
    - coverage：有效指标场次/窗口场次，开方打折后分档。
    """
    base = "high"
    if hits < 2:
        hits_tier = "low"
    elif hits <= 4:
        hits_tier = "medium"
    else:
        hits_tier = "high"

    if window_matches <= 0:
        coverage_tier = "low"
    else:
        fraction = valid_matches / window_matches
        c = math.sqrt(max(0.0, fraction))
        if c >= 0.75:
            coverage_tier = "high"
        elif c >= 0.5:
            coverage_tier = "medium"
        else:
            coverage_tier = "low"

    best = base
    for tier in (hits_tier, coverage_tier):
        if CONFIDENCE_ORDER[tier] < CONFIDENCE_ORDER[best]:
            best = tier
    return best


def confidence_score(confidence: str) -> float:
    return _CONF_SCORE.get(confidence, 0.4)


# ---- §6 Top3 权重 / 排序 ----
def impact_weight(candidate_id: str) -> float:
    return CANDIDATE_BY_ID.get(candidate_id, {}).get("impact_weight", 1.0)


def repeat_score(hits: int, matches: int) -> float:
    """命中率 hits/N（0..1）。"""
    if matches <= 0:
        return 0.0
    return hits / matches


def recency_score(hit_indices: list[int], window_matches: int) -> float:
    """近期命中加权（§6）：权重按时间升序线性递增（第 i 场权重 1 + i/(N-1)），
    仅命中场次计入加权和；分母为全部场次权重和（全部命中时 → 1.0）。

    hit_indices：命中匹配在窗口内按时间升序的下标。
    window_matches：窗口总场次 N。
    """
    if window_matches <= 0:
        return 0.0
    all_weights = [1.0 + i / (window_matches - 1) if window_matches > 1 else 1.0 for i in range(window_matches)]
    total = sum(all_weights)
    if total <= 0:
        return 0.0
    hit_total = sum(all_weights[i] for i in hit_indices)
    return hit_total / total


def sort_top3(items: list[dict]) -> list[dict]:
    """排序：score 降序；同分按 hits 降序；再按 candidate_id。返回排序后的列表。"""
    return sorted(items, key=lambda x: (-x["score"], -x.get("hits", 0), x["candidate_id"]))


def magnitude_human(magnitude: float, reference: float) -> str:
    """幅度人话：|Δ| 相对参考值（max(|p|,1)）的比值 → 小幅/中等/明显。"""
    if reference is None:
        reference = 1.0
    base = max(abs(reference), 1.0)
    ratio = magnitude / base
    if ratio < 0.05:
        return "小幅"
    if ratio < 0.15:
        return "中等"
    return "明显"
