"""6R9：深度配合指标阈值集中配置（可解释·机会归一·可回放定位）。

- 所有可调阈值集中于此，带 VERSION（产品配置版本号），由单测校验与敏感性分析（
  docs/phase6r9-calibration.md）选定。任何代码不得散落魔法阈值。
- authority 标注：字段级（event/field）或口径定义（defined）/未验证（unverified，如 LoS）。
"""
from __future__ import annotations

VERSION = "2.0.0"   # 6R16：七类指标重建（spacing episode 级、area 状态机、utility 区分 validated/inferred、isolated 排除反例、crossfire 保持 hidden）

TRADE_WINDOW_SEC = 5.0          # 补枪/交易窗口（敏感性 2/3/5/8）
TRADE_REACH_WORLD = 1400.0      # 可交易的队友与交战点最大距离（world 单位）
TRADE_FACING_DEG = 90.0         # 严格口径：队友朝向击杀者的最大夹角（°）

SIMULT_WINDOW_SEC = 3.0         # 协同接触窗口（敏感性 2/3/5）
CONTACT_CLUSTER_DIST_WORLD = 1200.0  # 把同一区域交战聚为一波的最大距离

CLOSE_SPACING_WORLD = 600.0     # 聚团判定基线距离（敏感性 400/600/750/900）
# 存活人数 → 允许距离放宽系数（人多时平均间距应更大）
SPACING_ALIVE_SCALE = {2: 0.85, 3: 1.0, 4: 1.15, 5: 1.30}
SPACING_DEFAULT_SCALE = 1.0

CROSSFIRE_ANGLE_DEG = 40.0      # 两枪线到交战点的最小夹角（°）（敏感性 30/40/50）
CROSSFIRE_RANGE_WORLD = 1400.0  # 枪线到交战点最大距离

UTILITY_WINDOW_SEC = 6.0        # 道具 detonate → 击杀的窗口（敏感性 3/6/9）
UTILITY_ENTRY_RANGE_WORLD = 1400.0  # 道具作用点与进点击杀点的最大距离

ISOLATED_SUPPORT_DIST = 900.0   # 阵亡时最近队友距离阈值（敏感性 600/750/900）

# ---- 6R16：区域控制状态机阈值 ----
AREA_CONTROL_MIN_SEC = 12.0     # controlled 最短持续占据秒（正向信号门槛外的时间要求）
AREA_OCCUPIED_MIN_SEC = 3.0     # occupied 最短持续秒（低于 → influence 瞬态）
AREA_MIN_ALIVE = 1              # controlled 最少存活在区人数
AREA_SAMPLE_STEP = 16           # 位置采样步长（tick）
AREA_SIGNAL_WINDOW_SEC = 3.0    # 正向信号（道具/致盲）关联窗口（秒）
AREA_STATE_GEOMETRY_CONFIDENT = "validated"  # 区域几何可信到哪一档才允许 controlled 标 validated

# ---- 6R16：spacing episode 级聚合 ----
# 一个「战术期连续段」内，若连续样本间出现"存活<2 / 区域跳变 / 阶段退出"则断段
SPACING_EPISODE_GAP_TICKS = 96     # 连续存活≥2 的采样允许的最大 tick 间隔（>此值断段）
SPACING_MIN_EPISODE_SEC = 2.0      # 短于该秒的段不算有效段（瞬态）


AREA_MIN_OCCUPANCY_SEC = 10.0   # 连续占据同一区域的下限时长（敏感性 5/10/15）
AREA_SAMPLE_STEP = 16           # 位置采样步长（tick）

# 空间/几何采样步长（间距、区域、交叉火力复用）
SAMPLE_STEP = 16

# confidence 派生规则（机会样本量阈值；6R16：spacing 分母改 episode 段数，area 分母改 controlled 回合数）
CONFIDENCE_OPP = {
    # metric_id: (low_lt, medium_lt)  样本数 < low_lt → low；< medium_lt → medium；否则 high
    "trade": (3, 9),
    "simultaneous_contact": (2, 6),
    "spacing": (10, 40),        # 分母为"有效战术期段数"（6R16 不再用 tick 采样点灌分母）
    "utility_assisted_entry": (2, 6),
    "isolated_death": (3, 10),   # 分母为"阵亡总数"（已排除保枪/最后存活等）
    "area_control": (2, 6),      # 分母为"带正向信号 controlled 段数"
    "crossfire_candidate": (3, 10),  # 候选机会数；无 LoS → 上限 medium
}
# 无 LoS/navmesh → 候选类 confidence 上限
CROSSFIRE_CONFIDENCE_CAP = "medium"
# 仅能证明"时间/空间邻近"（无法证明致盲/遮挡）→ 道具辅助类 confidence 上限
UTILITY_CONFIDENCE_CAP = "medium"
# 无 LoS/navmesh，且区域几何覆盖不足 → area_control 上限
AREA_CONFIDENCE_CAP = "medium"
# 间距为启发式（无 navmesh）→ spacing 上限
SPACING_CONFIDENCE_CAP = "medium"
# 孤立阵亡为距离启发式（无 navmesh/可达性）→ 上限
ISOLATED_CONFIDENCE_CAP = "medium"

# 结论抽取上限
MAX_CONCLUSIONS = 5

# 试验性基线标记（五场样本不足以建立行业百分位）
BASELINE_NOTE = "试验性基线（本批验收样本内对照，非行业基准）"


def spacing_allowed(dist_base: float, alive: int) -> float:
    """按存活人数放宽"允许间距"（人多 → 平均间距应更大）。"""
    return dist_base * SPACING_ALIVE_SCALE.get(alive, SPACING_DEFAULT_SCALE)


def confidence_from_opportunities(metric_id: str, opp: int) -> str:
    """由机会样本量派生 confidence（high/medium/low）。零机会返回 'none'。"""
    if opp <= 0:
        return "none"
    low_lt, med_lt = CONFIDENCE_OPP.get(metric_id, (3, 9))
    if opp < low_lt:
        return "low"
    if opp < med_lt:
        return "medium"
    return "high"


def cap_confidence(metric_id: str, conf: str) -> str:
    """按指标已知限制封顶 confidence（候选/邻近/无navmesh类不高于 medium）。"""
    caps = {
        "crossfire_candidate": CROSSFIRE_CONFIDENCE_CAP,
        "utility_assisted_entry": UTILITY_CONFIDENCE_CAP,
        "area_control": AREA_CONFIDENCE_CAP,
        "spacing": SPACING_CONFIDENCE_CAP,
        "isolated_death": ISOLATED_CONFIDENCE_CAP,
    }
    cap = caps.get(metric_id)
    if cap and cap == "medium" and conf == "high":
        return cap
    return conf
