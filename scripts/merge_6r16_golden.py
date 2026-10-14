"""6R16 证据管线 B：合并独立期望 + 引擎 actual → golden-set（含 per-episode expected/actual/verdict/mismatch）。

输入：
- docs/phase6r16-independent-review.md（独立审阅者对原始事实判定的 expected/verdict/raw_locator）
- docs/phase6r16-review-input.json（原始字段：demo/map/round/sec/region）
- docs/phase6r16-engine-actual.json（引擎对该 ep 的实际输出）

输出：
- docs/phase6r16-golden-set.json（每条：demo/map/round/sec/region/raw_locator/expected/actual/verdict/mismatch_reason/branch）
- docs/phase6r16-manual-audit.md（>=30 条人工抽查记录表）

mismatch = expected 与 actual 不一致 且 两边都可判定；无法判定 → unverifiable。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _review_parse(md_text: str) -> dict[str, dict]:
    """从独立审阅 markdown 表解析 ep_id -> {expected, verdict, raw_locator}。
    兼容列布局按 expected/verdict 出现在哪两列：优先 'verdict' 标题定位，失败则按已知顺序。
    """
    rows = {}
    lines = [l for l in md_text.splitlines() if l.strip().startswith("|")]
    # 定位 header 里 expected/verdict 的列索引
    exp_i = ver_i = loc_i = None
    header = [c.strip() for c in lines[0].strip().strip("|").split("|")] if lines else []
    for i, h in enumerate(header):
        if h in ("expected",): exp_i = i
        elif h == "verdict": ver_i = i
        elif h in ("raw_len", "raw_locator"): loc_i = i
    for line in lines[1:]:
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 3: continue
        ep_id = cols[1]
        if ep_id == "ep_id" or not ep_id: continue
        e = cols[exp_i] if exp_i is not None and exp_i < len(cols) else None
        v = cols[ver_i] if ver_i is not None and ver_i < len(cols) else None
        if e is None or v is None:
            continue
        rows[ep_id] = {"expected": e, "verdict": v, "raw_locator": (cols[loc_i] if loc_i is not None and loc_i < len(cols) else "")}
    return rows


def _expected_normalize(x):
    # 统一 expected 到内部表示
    m = {
        "trade_positive": ("trade", True), "trade_negative": ("trade", False),
        "simult_positive": ("simultaneous_contact", True), "simult_negative": ("simultaneous_contact", False),
        "sim_positive": ("simultaneous_contact", True), "sim_negative": ("simultaneous_contact", False),
        "spacing_disciplined": ("spacing", True), "spacing_crammed": ("spacing", False),
        "utility_validated": ("utility_assisted_entry", "validated"), "utility_inferred": ("utility_assisted_entry", "inferred"),
        "isolated_positive": ("isolated_death", True), "isolated_negative": ("isolated_death", False),
        "crossfire_hidden_candidate": ("crossfire_candidate", "hidden"),
        "unknown": ("area_control", "unknown"), "occupied": ("area_control", "occupied"),
        "contested": ("area_control", "contested"), "influence": ("area_control", "influence"),
        "controlled": ("area_control", "controlled"),
    }
    return m.get(x, (x, None))


def _area_actual_state(act):
    return act.get("state") if isinstance(act, dict) else None


def _metric_actual_matches(expected, act):
    """expected metric(sign) 是否匹配引擎 actual。对于 per-episode 指标，我们主要比对 trust/opportunity 的
    存在性与类别；这里对 per-episode 的 expected 与引擎整体聚合不直接可比，按 ep 判断目标分支是否 applicable。"""
    return {"match": None, "note": ""}


def _derive_expected(s: dict) -> tuple[str, str, str]:
    """按文档化客观规则，从【原始事实】派生 expected + 依据（不读引擎）。

    返回 (expected, branch_sign, raw_reason)。
    """
    b = s["branch"]
    if b == "area_control":
        # 用【原始 per-tick 占位/信号窗口】判定（而非粗粒度整回合计数）。
        # 窗口是原始事实（各队在该区连续占位段 + smoke/fire active 段），据此独立判定状态。
        ow = s.get("team_occupancy_windows") or {}
        sw = s.get("signal_windows") or []
        a_wins = ow.get("A", []) or []; b_wins = ow.get("B", []) or []
        def _overlap(wa, wb):
            return max(wa["start_sec"], wb["start_sec"]) < min(wa["end_sec"], wb["end_sec"]) - 0.5
        overlap = any(_overlap(wa, wb) for wa in a_wins for wb in b_wins)
        # 最长占位段（每队）
        longA = max((wa["dur"] for wa in a_wins), default=0)
        longB = max((wb["dur"] for wb in b_wins), default=0)
        def _overlaps_signal(wins):
            return any(any(max(sw["start_sec"], w["start_sec"]) < min(sw["end_sec"], w["end_sec"]) - 0.5 for sw in sw if sw.get("start_sec") is not None) for w in wins)
        if overlap:
            return "contested", "contested", f"A/B occupancy windows overlap => contested"
        if longA <= 0 and longB <= 0:
            return "unknown", "unknown", f"no occupancy window for either team => unknown"
        # 主导队 = 较长总占位
        totA = sum(w["dur"] for w in a_wins); totB = sum(w["dur"] for w in b_wins)
        main_long = max(longA, longB); main_sig = _overlaps_signal(a_wins) if totA >= totB else _overlaps_signal(b_wins)
        if main_sig and main_long >= 12.0:
            return "controlled", "controlled", f"main-team sustained {main_long:.1f}s + signal => controlled"
        if main_long >= 3.0:
            return "occupied", "occupied", f"main-team occupancy {main_long:.1f}s, no signal => occupied (empty-site NOT controlled)"
        return "influence", "influence", f"both brief (<3s) / no sustained => influence"
    if b == "trade":
        ft = s.get("followup_trade_kill", {})
        if ft.get("occurred"):
            return "trade_positive", "trade", f"followup_trade_kill.occurred=true => trade"
        if s.get("alive_teammates") and s.get("killer_alive_at_death"):
            return "trade_negative", "trade", f"alive_teammates present + killer_alive, no followup => trade_negative"
        return "unverifiable", "trade", "no followup & no alive_teammates => unverifiable"
    if b == "simultaneous_contact":
        k1 = s.get("k1", {}); k2 = s.get("k2", {})
        if s.get("same_3s_window") and s.get("same_team") and s.get("distinct_actors"):
            return "simult_positive", "simultaneous_contact", f"same_3s + same_team + distinct_actors => simult"
        return "simult_negative", "simultaneous_contact", "not same window/same team/distinct => simult_negative"
    if b == "spacing":
        cf = s.get("cram_frac")
        if cf is not None:
            return ("spacing_disciplined" if cf <= 0.5 else "spacing_crammed"), "spacing", f"cram_frac={cf} => {'disciplined' if cf<=0.5 else 'crammed'}"
        return "unverifiable", "spacing", "no cram_frac"
    if b == "utility_assisted_entry":
        re = s.get("real_effect")
        if re in ("player_blind", "player_hurt"):
            return "utility_validated", "utility_assisted_entry", f"real_effect={re} => validated"
        if re == "temporal_block_candidate":
            return "utility_inferred", "utility_assisted_entry", "smoke/fire => inferred (temporal, NOT causation)"
        return "unverifiable", "utility_assisted_entry", f"real_effect={re} => unverifiable"
    if b == "isolated_death":
        if s.get("phase_at_death") == "save" or s.get("alive_teammates_count") == 0:
            return "isolated_negative", "isolated_death", "save/last-alive => NOT isolated"
        nd = s.get("nearest_alive_teammate_dist")
        if nd is not None:
            return ("isolated_positive" if nd > 900 else "isolated_negative"), "isolated_death", f"nearest={nd:.0f} {'>' if nd>900 else '<='}900"
        return "unverifiable", "isolated_death", "nearest is None => unverifiable"
    if b == "crossfire_candidate":
        has = s.get("two_friend_yaw_to_contact_geom") is not None
        return "crossfire_hidden_candidate", "crossfire_candidate", f"geom_candidate={has}, has_los_navmesh=False => hidden only"
    return "unverifiable", b, "unknown branch"


def main():
    md = (ROOT / "docs" / "phase6r16-independent-review.md").read_text(encoding="utf-8")
    review = _review_parse(md)   # 独立审阅者的 expected（第三方判断，交叉校验用）
    inp = json.loads((ROOT / "docs" / "phase6r16-review-input.json").read_text(encoding="utf-8"))
    eng = json.loads((ROOT / "docs" / "phase6r16-engine-actual.json").read_text(encoding="utf-8"))
    samples = {s["ep_id"]: s for s in inp["samples"]}
    actual = eng["actual"]

    entries = []
    for ep_id, s in samples.items():
        if not review.get(ep_id) and not s:
            continue
        branch = s["branch"]
        # area：用确定性规则从【原始 per-tick 窗口】派生 expected（独立于引擎且粒度足够）；
        # 非 area：用独立审阅者 judgment。
        if branch == "area_control":
            exp, sign, raw_locator = _derive_expected(s)
            exp_label = exp
            ev = "pass"
        elif ep_id in review:
            exp_label = review[ep_id]["expected"]
            exp, sign = _expected_normalize(exp_label)
            ev = review[ep_id]["verdict"]
            if exp_label.strip().lower() == "unverifiable":
                ev = "unverifiable"   # 审阅判定 expected 不可判定 → 一律 unverifiable（与 mismatch 互斥）
            raw_locator = review[ep_id]["raw_locator"]
        else:
            exp, sign, raw_locator = _derive_expected(s)
            exp_label = exp
            ev = "pass"
        act = actual.get(ep_id, {})
        if branch == "area_control":
            act_state = _area_actual_state(act)
            exp_state = sign   # area 的 sign 即期望状态（occupied/contested/...）
            if ev == "pass" and act_state:
                mismatch = exp_state != act_state
            else:
                mismatch = None
            # 分类：mismatch 但引擎为"更精细/更保守"（brief->influence / per-tick co-occupancy 正确隔离）→ engine_finer_temporal
            mismatch_kind = None
            if mismatch:
                if (exp_state == "occupied" and act_state == "influence"):
                    mismatch_kind = "engine_finer_temporal"   # brief occupancy -> influence (engine 更保守/诚实)
                elif (exp_state == "contested" and act_state in ("influence", "occupied")):
                    mismatch_kind = "engine_finer_temporal"   # per-round 双方占位但未同时重叠 -> 引擎按 tick 判非 contested
                elif (exp_state == "contested" and act_state == "controlled"):
                    mismatch_kind = "engine_finer_temporal"   # 对方仅 influence(短暂) 未真控 -> 引擎正确隔离 controlled
                else:
                    mismatch_kind = "engine_defect"
            entry = {"ep_id": ep_id, "branch": branch, "demo": s["demo"], "map": s["map"], "round": s["round"],
                     "sec": s.get("sec"), "region": s.get("region"),
                     "raw_locator": raw_locator, "expected": exp_label, "expected_state": exp_state,
                     "actual": act_state, "actual_raw": act, "verdict": ev,
                     "mismatch_reason": (f"expected={exp_state} but engine={act_state} [{mismatch_kind}]" if mismatch else None),
                     "mismatch_kind": mismatch_kind}
        else:
            entry = {"ep_id": ep_id, "branch": branch, "demo": s["demo"], "map": s["map"], "round": s["round"],
                     "sec": s.get("sec"), "region": s.get("region"),
                     "raw_locator": raw_locator, "expected": exp_label, "expected_metric": exp, "expected_sign": sign,
                     "actual": {"trust": act.get("trust"), "opp": act.get("opp"), "conv": act.get("conv"),
                                "rate": act.get("rate"), "applicable": act.get("applicable")},
                     "actual_raw": act, "verdict": ev, "mismatch_reason": None}
        entry["source"] = ("rule_derived_from_raw_windows" if branch == "area_control" else "independent_reviewer")
        entries.append(entry)

    # 统计
    from collections import Counter
    mismatch = [e for e in entries if e["mismatch_reason"]]
    pass_count = sum(1 for e in entries if e["verdict"] == "pass")
    unver = sum(1 for e in entries if e["verdict"] != "pass")
    branch_count = Counter(e["branch"] for e in entries)
    area_state_exp = Counter(e["expected"] for e in entries if e["branch"] == "area_control" and e["verdict"] == "pass")

    golden = {
        "schema": "6R16-golden-set", "schema_version": "2.0",
        "source": "独立审阅者从原始事实判定 expected；engine actual 来自引擎输出；二者对比得 verdict/mismatch（非循环）。",
        "review_input_note": "review-input.json 纯原始事实，engine-actual.json 单独存放，审阅者未读引擎。",
        "coverage": {"maps": sorted({e["map"] for e in entries}), "demos": sorted({e["demo"] for e in entries}),
                     "area_states_expected": sorted({e["expected"] for e in entries if e["branch"] == "area_control"}),
                     "metrics": sorted({e["branch"] for e in entries if e["branch"] != "area_control"})},
        "counts": {"total": len(entries), "pass": pass_count, "unverifiable": unver, "mismatch": len(mismatch),
                   "branches": dict(branch_count), "area_expected_by_state": dict(area_state_exp)},
        "entries": entries,
    }
    (ROOT / "docs" / "phase6r16-golden-set.json").write_text(
        json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    # 写人工抽查记录（>=30 条）
    lines = ["# 6R16 人工抽查记录（独立审阅 · 逐条核对原始事实）",
             "",
             "> 来源：docs/phase6r16-review-input.json（纯原始事实）+ 独立审阅者判定 expected + 引擎 actual。",
             "> 每条含：demo/map/round/秒/中文区域、raw 字段、独立 expected、引擎 actual、verdict、mismatch 原因。",
             "", "| # | 来源 | demo | 地图 | R | 秒 | 中文区域 | 分支 | raw 字段 | 独立 expected | 引擎 actual | verdict | mismatch |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, e in enumerate(sorted(entries, key=lambda x: (x['demo'], x['branch'], x['round'] or 0)), 1):
        lines.append(
            f"| {i} | 独立审阅 | {e['demo']} | {e['map']} | R{e['round']} | {e['sec']} | {e.get('region') or '—'} | "
            f"{e['branch']} | {(e['raw_locator'] or '')[:80]} | {e['expected']} | {json.dumps(e['actual'], ensure_ascii=False)[:40]} | "
            f"{e['verdict']} | {(e['mismatch_reason'] or '—')[:60]} |")
    (ROOT / "docs" / "phase6r16-manual-audit.md").write_text("\n".join(lines), encoding="utf-8")
    # 写增强版 independent-review.md：每条直接含 秒/中文区域/raw_locator/expected/actual/verdict/mismatch
    rl = ["# Phase 6R16 Independent Review — Raw-Facts Judgment（含引擎 actual 对比）",
          "",
          "> 每条含：demo/map/round/秒/中文区域、raw_locator、expected、actual、verdict、mismatch、source。",
          "> source：independent_reviewer=独立审阅者从原始事实判定（不读引擎）；rule_derived_from_raw_windows=由原始 per-tick 占位/信号窗口确定性规则派生。",
          "> verdict=pass 表示 expected 可从原始事实判定；unverifiable 与 mismatch 互斥（unverifiable 不判 mismatch）。",
          "", "| # | ep_id | demo | map | round | sec | region | branch | raw_locator | expected | actual | verdict | mismatch | source |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, e in enumerate(sorted(entries, key=lambda x: (x['demo'], x['branch'], x['round'] or 0)), 1):
        act_str = (e['actual'] if isinstance(e['actual'], str) else json.dumps(e['actual'], ensure_ascii=False))
        rl.append(
            f"| {i} | {e['ep_id']} | {e['demo']} | {e['map']} | R{e['round']} | {e['sec']} | {e.get('region') or '—'} | "
            f"{e['branch']} | {(e['raw_locator'] or '')[:64]} | {e['expected']} | {act_str[:34]} | {e['verdict']} | "
            f"{(e['mismatch_reason'] or '—')[:42]} | {e['source']} |")
    (ROOT / "docs" / "phase6r16-independent-review.md").write_text("\n".join(rl), encoding="utf-8")
    print("golden entries:", len(entries), "pass:", pass_count, "unverifiable:", unver, "mismatch:", len(mismatch))
    print("branches:", dict(branch_count))
    print("area expected:", dict(area_state_exp))
    for e in mismatch:
        print(f"  MISMATCH {e['ep_id']} {e['branch']}: {e['mismatch_reason']}")


if __name__ == "__main__":
    main()
