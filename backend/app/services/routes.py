"""6R4：个人动态路线回放数据层。

- 受控采样（时间/距离/事件保真）把逐玩家逐回合轨迹压缩为版本化 JSON artifact，
  按 match 存 MinIO（gzip），不把数百万点逐行塞进 MySQL。
- 事件保真：spawn/kill/death/round_end 时刻总是保留（即使被距离/时间采样过滤）。
- 完整性：ordered / dropped_points / sampled_points / kept_points / coverage /
  first_tick / last_tick / sampling / warnings 逐轨迹记录。
- 幂等：同一 demo 解析 → 相同字节 → 相同 sha256；重跑解析不重复生成。
- artifact 只存世界坐标（x/y/z）；雷达归一化坐标（rx/ry）由 API 读取时按
  当前 maps.json 换算，避免 artifact 与某版地图配置耦合。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math

from app.services.parser import CS2_TICK_RATE, read_cdemo_file_info

ROUTE_SCHEMA_VERSION = "1.2"  # 1.2：权威时基 tick_rate 来自 CDemoFileInfo（旧 1.1 的 128 硬编码时基已作废）

# 受控采样参数（时间 + 距离 + 事件保真）
ROUTE_TICK_STEP = 16          # 基础时间采样步长（tick；64tick 下 ≈0.25s/点）
ROUTE_MIN_DISTANCE = 30.0     # 世界单位：与上个保留点距离 < 此值则丢弃（压缩静止段）
ROUTE_MAX_GAP_TICKS = 256     # 若与上个保留点 tick 间隔 >= 此值，无条件保留（跨大移动/传送）
ROUTE_EVENTS = ("spawn", "kill", "death", "round_end")

SAMPLING_MODE = "time+distance+event"


def _clean(v) -> float | None:
    """JSON 安全坐标：保留合法 0；NaN/Inf/None → None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def gzip_json(obj: dict) -> bytes:
    """对象 → gzip JSON 字节（MinIO artifact）。

    mtime=0：gzip 头固定时间戳，保证同一输入跨秒压缩产生相同字节（幂等/可复现 sha）。
    """
    return gzip.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8"), mtime=0)


def gunzip_json(data: bytes) -> dict:
    """gzip JSON 字节 → 对象。"""
    return json.loads(gzip.decompress(data).decode("utf-8"))


def _round_mapper(rounds: list[dict]):
    """tick → round 号（二分；返回 None 表示不在任何正式回合）。"""
    import bisect

    starts = [r["start_tick"] for r in rounds]
    ends = [r["end_tick"] for r in rounds]

    def mapper(tick: int) -> int | None:
        idx = bisect.bisect_right(starts, tick) - 1
        if idx < 0:
            return None
        r = rounds[idx]
        if tick < r["start_tick"] or tick > r["end_tick"]:
            return None
        return r["round"]

    return mapper


def _thin(points: list[dict], min_dist: float, max_gap: int) -> tuple[list[dict], int]:
    """距离/间隔受控采样：保留事件点；其余点距上一点太近且间隔不够大则丢弃。

    返回 (kept, dropped_count)。
    """
    kept: list[dict] = []
    dropped = 0
    last = None
    for p in points:
        if p.get("events"):
            kept.append(p)
            last = p
            continue
        if last is None:
            kept.append(p)
            last = p
            continue
        dx = (p.get("x") or 0) - (last.get("x") or 0)
        dy = (p.get("y") or 0) - (last.get("y") or 0)
        dist = (dx * dx + dy * dy) ** 0.5
        gap = p["tick"] - last["tick"]
        if dist >= min_dist or gap >= max_gap:
            kept.append(p)
            last = p
        else:
            dropped += 1
    return kept, dropped


_EVENT_PRIORITY = {"death": 0, "kill": 1, "spawn": 2, "round_end": 3}


def _merge_same_tick(points: list[dict]) -> list[dict]:
    """合并同 tick 的点：叠加 events 数组，位置/存活取最高优先级事件（death>kill>spawn>round_end）。

    保证同一玩家同 tick 的 kill/death/spawn/round_end 同时保留（不再相互覆盖）。
    每个点同时保留兼容的主标记 event（= 最高优先级事件）。
    """
    points.sort(key=lambda p: p["tick"])
    merged: list[dict] = []
    i = 0
    while i < len(points):
        tick = points[i]["tick"]
        group = [points[i]]
        j = i + 1
        while j < len(points) and points[j]["tick"] == tick:
            group.append(points[j])
            j += 1
        # 位置/存活来源：最高优先级事件（存在坐标者优先）
        best = min(group, key=lambda p: (
            min((_EVENT_PRIORITY.get(e, 99) for e in p["events"]), default=99),
            p["x"] is None,
        ))
        events: list[str] = []
        for p in group:
            for e in p["events"]:
                if e not in events:
                    events.append(e)
        # death 存在 → 该 tick 视为 not alive
        alive = False if any("death" in p["events"] for p in group) else bool(best["alive"])
        merged.append({
            "tick": tick, "time": best["time"], "x": best["x"], "y": best["y"], "z": best["z"],
            "alive": alive, "events": events, "source": best["source"], "place": best.get("place"),
        })
        i = j
    for p in merged:
        p["event"] = sorted(p["events"], key=lambda e: _EVENT_PRIORITY.get(e, 99))[0] if p["events"] else None
    return merged


def _side_of(side_map: dict, team_identity: str | None) -> str | None:
    if not team_identity or team_identity not in side_map:
        return None
    return "CT" if side_map[team_identity] == 2 else "T"


def build_trajectory(
    start_tick: int,
    end_tick: int,
    side: str | None,
    sampled: list[dict],
    kills: list[dict],
    death: dict | None,
    tick_rate: float,
) -> dict:
    """由采样点 + 事件构建单个玩家单回合轨迹。

    sampled: [{tick,x,y,z,alive}]（tick 升序）；kills: [{tick,x,y}]；death: {tick,x,y}|None。
    tick_rate 为权威时基；若为 None/不可证明，则不换算秒（各点 time=None），由 API 标记 time_unverifiable。
    """
    def t(nt: int) -> float | None:
        if not tick_rate:
            return None
        return round((nt - start_tick) / tick_rate, 3)

    raw: list[dict] = []

    # 采样点（无事件；events 空）
    for p in sampled:
        raw.append({
            "tick": int(p["tick"]), "time": t(int(p["tick"])),
            "x": _clean(p.get("x")), "y": _clean(p.get("y")), "z": _clean(p.get("z")),
            "alive": bool(p.get("alive")), "events": [], "source": "player_position",
            "place": p.get("place") or None,
        })

    # 事件点（spawn / kill / death / round_end）——总是保留（事件保真）
    def add_event(tick: int, x, y, z, alive: bool, event: str, source: str):
        raw.append({
            "tick": int(tick), "time": t(int(tick)),
            "x": _clean(x), "y": _clean(y), "z": _clean(z),
            "alive": alive, "events": [event], "source": source,
        })

    # kill 标记（该玩家作为击杀者）
    for k in kills:
        add_event(k["tick"], k["x"], k["y"], None, True, "kill", "event")

    # death 标记（该玩家作为受害者）
    if death is not None:
        add_event(death["tick"], death["x"], death["y"], None, False, "death", "event")

    # 判定终局：有 death 事件 → dead；否则存活
    died = death is not None
    outcome = "dead" if died else "alive"

    # 死亡玩家：截断到死亡时刻（死亡后位置无意义，避免轨迹越过死亡点继续延伸）
    if died and death is not None:
        raw = [p for p in raw if p["tick"] <= death["tick"]]

    if not raw:
        return {
            "side": side, "outcome": "unknown", "points": [],
            "integrity": {
                "ordered": True, "dropped_points": 0, "sampled_points": 0, "kept_points": 0,
                "coverage": 0.0, "first_tick": None, "last_tick": None,
                "sampling": {"tick_step": ROUTE_TICK_STEP, "mode": SAMPLING_MODE},
                "warnings": ["该回合玩家无轨迹数据"],
            },
        }

    raw.sort(key=lambda p: p["tick"])

    # spawn 标记：保证存在。取最接近 start_tick 的点（不足则用最早点补一个 spawn）
    if not any("spawn" in p["events"] for p in raw):
        earliest = raw[0]
        if earliest["tick"] - start_tick <= ROUTE_TICK_STEP:
            earliest["events"].insert(0, "spawn")
            earliest["source"] = "round_start"
        else:
            add_event(start_tick, earliest["x"], earliest["y"], earliest["z"], earliest["alive"], "spawn", "round_start")
            raw.sort(key=lambda p: p["tick"])

    # round_end 标记：仅存活玩家（死亡玩家轨迹止于 death，不再补 round_end 点）
    if outcome == "alive":
        latest = max((p for p in raw if "death" not in p["events"]), key=lambda p: p["tick"], default=None)
        if latest is not None:
            if end_tick - latest["tick"] <= ROUTE_TICK_STEP:
                latest["events"].append("round_end")
                latest["source"] = "round_end"
            else:
                add_event(end_tick, latest["x"], latest["y"], latest["z"], True, "round_end", "round_end")

    # 合并同 tick 事件（不互相覆盖）+ 生成兼容主标记 event
    raw = _merge_same_tick(raw)

    # 受控采样（距离/间隔），事件点不参与丢弃
    kept, dropped = _thin(raw, ROUTE_MIN_DISTANCE, ROUTE_MAX_GAP_TICKS)
    kept.sort(key=lambda p: p["tick"])

    # 完整性
    ticks = [p["tick"] for p in kept]
    ordered = ticks == sorted(ticks)
    first_tick = ticks[0] if ticks else None
    last_tick = ticks[-1] if ticks else None
    span = end_tick - start_tick
    coverage = round(max(0.0, min(1.0, ((last_tick or start_tick) - (first_tick or start_tick)) / span)), 3) if span > 0 else 0.0

    warnings: list[str] = []
    if not ordered:
        warnings.append("轨迹点 tick 非严格有序")
    if died:
        warnings.append("该回合玩家中途死亡，轨迹在死亡时刻终止")
    if coverage < 0.5:
        warnings.append("轨迹覆盖不足一半回合，可能缺少位置采样")

    return {
        "side": side,
        "outcome": outcome,
        "points": kept,
        "integrity": {
            "ordered": ordered,
            "dropped_points": dropped,
            "sampled_points": len(sampled),
            "kept_points": len(kept),
            "coverage": coverage,
            "first_tick": first_tick,
            "last_tick": last_tick,
            "sampling": {"tick_step": ROUTE_TICK_STEP, "mode": SAMPLING_MODE},
            "warnings": warnings,
        },
    }


def build_route_artifact(
    analyzer,
    match_id: int,
    map_name: str,
    playout_ticks: int,
    rounds: list[dict],
    players: list[dict],
    identity_of: dict[str, str],
    player_id_of: dict[str, int],
    deaths: list[dict],
    tick_rate: float | None = None,
) -> dict:
    """构建整场 per-match 版本化路线 artifact（含全部玩家全部回合）。

    tick_rate 来自权威时基（CDemoFileInfo）；为 None 表示不可证明 → 各点 time=None，
    artifact 标记 tick_rate=null，API 层据此阻止显示秒（绝不回退硬编码 128）。
    """
    mapper = _round_mapper(rounds)

    # 1. 采样位置（一次性 parse_ticks；含 last_place_name 供人类地点显示）
    pos_df = analyzer._parser.parse_ticks(
        ["X", "Y", "Z", "health", "CCSPlayerPawn.m_iTeamNum", "steamid", "tick", "last_place_name"],
        ticks=range(0, int(playout_ticks or 0) + 1, ROUTE_TICK_STEP),
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
            continue  # 无位置采样点不记录（覆盖不足会在完整性里体现）
        health = row.get("health")
        alive = health is not None and health > 0
        sampled_by_rp.setdefault((rnd, sid), []).append({
            "tick": tick, "x": x, "y": y, "z": z, "alive": alive,
            "place": row.get("last_place_name") or None,
        })

    # 2. kill / death 事件索引
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

    # 3. 逐回合逐玩家构建轨迹
    artifact_rounds = []
    for r in rounds:
        rnd = r["round"]
        side_map = r.get("team_side_map") or {}
        start_tick, end_tick = r["start_tick"], r["end_tick"]
        traj: dict[str, dict] = {}
        for p in players:
            sid = str(p["steamid"])
            pid = player_id_of.get(sid)
            if pid is None:
                continue
            side = _side_of(side_map, identity_of.get(sid))
            traj[str(pid)] = build_trajectory(
                start_tick, end_tick, side,
                sampled_by_rp.get((rnd, sid), []),
                kills_by_rp.get((rnd, sid), []),
                deaths_by_rp.get((rnd, sid)),
                tick_rate,
            )
        artifact_rounds.append({
            "round": rnd, "start_tick": start_tick, "end_tick": end_tick,
            "freeze_end_tick": r.get("freeze_end_tick"),
            "winner_side": r.get("winner_side"), "winner_team": r.get("winner_team"),
            "side_map": side_map, "trajectories": traj,
        })

    # 权威时基元数据（供渲染/审计复现）：来源于 .dem 二进制内 CDemoFileInfo。
    timebase = {"authority": None}
    if tick_rate:
        try:
            info = read_cdemo_file_info(getattr(analyzer, "demo_path", None)) if getattr(analyzer, "demo_path", None) else None
            if info:
                timebase = {"authority": "cdemo_file_info", **info}
        except Exception:  # noqa: BLE001
            timebase = {"authority": None}

    return {
        "schema_version": ROUTE_SCHEMA_VERSION,
        "match_id": match_id,
        "map_name": map_name,
        "tick_rate": tick_rate,
        "timebase": timebase,
        "rounds": artifact_rounds,
    }


def artifact_sha256(data: bytes) -> str:
    """压缩 artifact 的 sha256（幂等/追溯）。"""
    return hashlib.sha256(data).hexdigest()


def route_object_key(match_id: int, schema_version: str = ROUTE_SCHEMA_VERSION) -> str:
    return f"routes/{match_id}/{schema_version}/routes.json.gz"
