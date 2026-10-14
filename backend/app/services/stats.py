"""指标计算（计划书 7.3 关键指标定义，执行者按此实现，不得自创）。

Phase 1 预研实现基础版统计（player_stats 表字段）：
- K/D/A、爆头率、伤害总量、ADR、首杀数、武器分布
进阶指标（补枪率/残局胜率/道具效率/经济管理/路线聚类）在对应阶段实现。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def compute_basic_stats(
    deaths: Iterable[dict],
    hurts: Iterable[dict],
    player_steamids: Iterable[str],
    rounds: list[dict],
) -> dict[str, dict]:
    """按玩家聚合基础统计。

    输入：deaths/hurts 为 parser 解析出的事件行（含 steamid 字段），
          player_steamids 为本场所有玩家，rounds 为 parse_rounds() 结果。
    输出：{steamid: {kills, deaths, assists, hs_kills, hs_rate, damage,
                     adr, first_kills, weapon_stats: {武器: {kills, dmg}}}}
    """
    steam_ids = set(player_steamids)
    stats: dict[str, dict] = {
        sid: {
            "kills": 0, "deaths": 0, "assists": 0,
            "hs_kills": 0, "hs_rate": 0.0,
            "damage": 0, "adr": 0.0,
            "first_kills": 0,
            "weapon_stats": defaultdict(lambda: {"kills": 0, "dmg": 0}),
        }
        for sid in steam_ids
    }

    deaths = list(deaths)
    round_count = max(len(rounds), 1)

    # 6R10A：权威回合窗口。事件 round 必须是某正式回合（1..N）才计入统计；
    # round=None/0（备战/热身/刀局/场外）不计 K/D/A/伤害/首杀，避免污染数据可信度。
    valid_rounds = set(int(r["round"]) for r in rounds) if rounds else set()

    # 首杀判定：每个回合里 tick 最小的击杀
    first_kill_by_round: dict[int, str | None] = {}
    for d in sorted(deaths, key=lambda r: r["tick"]):
        if d.get("round") not in valid_rounds:
            continue  # 备战/场外击杀不计
        actor = str(d.get("attacker_steamid") or "")
        victim = str(d.get("user_steamid") or "")
        weapon = str(d.get("weapon") or "unknown")
        # 6R1：死亡独立统计——victim 在玩家列表即计（环境杀/未知 actor 也计入受害者死亡）
        if victim in steam_ids:
            stats[victim]["deaths"] += 1
        if actor not in steam_ids:
            continue
        st = stats[actor]
        st["kills"] += 1
        if d.get("headshot"):
            st["hs_kills"] += 1
        st["weapon_stats"][weapon]["kills"] += 1
        # 助攻：击杀事件的 assister_steamid 字段
        assister = str(d.get("assister_steamid") or "")
        if assister in steam_ids:
            stats[assister]["assists"] += 1
        # 首杀记录（回合号由调用方预计算注入，或这里用近似：先记 tick）
        first_kill_by_round.setdefault(d.get("round", 0), actor)

    for sid in steam_ids:
        stats[sid]["first_kills"] = sum(1 for v in first_kill_by_round.values() if v == sid)

    for h in hurts:
        if h.get("round") not in valid_rounds:
            continue  # 备战期伤害不计入 ADR 分子
        actor = str(h.get("attacker_steamid") or "")
        if actor not in steam_ids:
            continue
        dmg = int(h.get("dmg_health") or 0)
        stats[actor]["damage"] += dmg
        weapon = str(h.get("weapon") or "unknown")
        stats[actor]["weapon_stats"][weapon]["dmg"] += dmg

    for sid, st in stats.items():
        st["hs_rate"] = round(st["hs_kills"] / st["kills"], 4) if st["kills"] else 0.0
        st["adr"] = round(st["damage"] / round_count, 1)
        st["weapon_stats"] = {
            w: {"kills": v["kills"], "dmg": v["dmg"]}
            for w, v in st["weapon_stats"].items()
        }
    return stats


def compute_match_score(rounds: list[dict]) -> tuple[int, int]:
    """最终比分（6R0：按队伍身份 A/B，换边正确）。

    每回合 winner_team（由 roster 映射的胜者队伍）累计；
    兼容旧数据（无 winner_team 时回退为阵营胜局计数，仅作降级）。
    返回 (team_a_score, team_b_score)。
    """
    if rounds and "winner_team" in rounds[0]:
        score_a = sum(1 for r in rounds if r.get("winner_team") == "A")
        score_b = sum(1 for r in rounds if r.get("winner_team") == "B")
        return score_a, score_b
    # 降级（旧 rounds）：winner_side 阵营胜局计数
    score_ct = sum(1 for r in rounds if r.get("winner_side") == 2 or r.get("winner") == 2)
    score_t = sum(1 for r in rounds if r.get("winner_side") == 3 or r.get("winner") == 3)
    return score_ct, score_t
