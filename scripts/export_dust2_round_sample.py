"""6R21F-E6E：导出 demo21（de_dust2）匿名、版本化、可审计的真实回合回放样本（产品控制场真值源）。

诚实来源原则（对应 E6E 合同，不伪造）：
- 数据**只**来自 demo21（match 61）的权威 tactical artifact v3.0
  （MinIO `tactical/61/3.0/tactical.json.gz`，sha256=48da8b31e80b1053a863f71d7a4fec15159758ce29a3342febc822a1b7292fa2），
  **不**是手绘路径；R2/R3 解析纠错保持生效（与 inferno 同一 tactical 解析管线，复用，不复制）。
- demo21 权威 map = de_dust2（三源一致：.dem 头 / DB demos / DB matches / maps.json）。
- 坐标换算用 de_dust2 权威 transform：radar_x=(x-pos_x)/scale/1024；radar_y=(pos_y-y)/scale/1024
  （maps.json：de_dust2 pos_x=-2476.0 pos_y=3239.0 scale=4.4）；复用同一雷达变换公式。
- 输出**不含** steam_id / 玩家名 / 数据库主键；玩家以匿名 uid（T1..T5 / CT1..CT5）标识。
- 区域几何来自 `backend/config/regions/de_dust2.json`（真实 last_place_name 样本凸包）。
- 区域状态复用 `app.services.area_state.build_region_state_machine_for_round`（6R16 状态机）。
- 击杀/补枪来自真实 killfeed；补枪 = 队友阵亡后 TRADE_WINDOW_SEC 内击杀该击杀者。

运行（宿主机 venv + 指向 localhost:9000 的 MinIO 环境）：
  python scripts/export_dust2_round_sample.py [round_no]
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT_DIR / "frontend" / "public" / "demo"
OUT_PATH = OUT_DIR / "dust2-round-sample.json"

# --- demo21 固定来源（可审计常量）---
MATCH_ID = 61
ARTIFACT_KEY = "tactical/61/3.0/tactical.json.gz"
ARTIFACT_SHA256 = "48da8b31e80b1053a863f71d7a4fec15159758ce29a3342febc822a1b7292fa2"
SCHEMA_VERSION = "1.0"
DEFAULT_ROUND = 2
RADAR_IMAGE_SIZE = 1024

MAP_NAME = "de_dust2"
MAP_META = {"name": MAP_NAME, "pos_x": -2476.0, "pos_y": 3239.0, "scale": 4.4, "image_size": RADAR_IMAGE_SIZE}

# A/B 队伍归属（源自 demo21 players 表 team_identity；artifact 本身不含该字段，故作为可审计常量）。
TEAM_OF = {
    551: "A", 552: "A", 553: "A", 554: "B", 555: "A",
    556: "B", 557: "A", 558: "B", 559: "B", 560: "B",
}

TRADE_WINDOW_SEC = 5.0


def _norm_place(p) -> str:
    return "".join(ch.lower() for ch in str(p or "") if ch.isalnum())


def _tx(x: float, y: float, meta: dict) -> tuple[float, float]:
    rx = (x - meta["pos_x"]) / meta["scale"] / meta["image_size"]
    ry = (meta["pos_y"] - y) / meta["scale"] / meta["image_size"]
    return round(rx, 5), round(ry, 5)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _content_hash(obj) -> str:
    h = hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def _sec(tick, freeze, tick_rate) -> float | None:
    if not tick_rate or freeze is None:
        return None
    return round((tick - freeze) / tick_rate, 2)


def _player_uid(pid: int, team_of: dict, side_by_pid: dict) -> str:
    side = side_by_pid.get(pid, "CT" if team_of.get(pid) == "A" else "T")
    team_members = sorted([p for p in team_of if side_by_pid.get(p) == side])
    idx = team_members.index(pid) + 1
    return f"{side}{idx}"


def build_player_timeline(rnd, meta, tick_rate, team_of):
    players = rnd.get("players", {})
    freeze = rnd.get("freeze_end_tick") or rnd.get("start_tick")
    side_by_pid = {int(pid): pd.get("side", "CT" if team_of.get(int(pid)) == "A" else "T")
                   for pid, pd in players.items()}
    out = []
    for pid in sorted(int(p) for p in players):
        pd = players.get(str(pid))
        pts = pd.get("points", [])
        seq = []
        last_tick = None
        for p in pts:
            t = p.get("tick")
            if t is None or (freeze is not None and t < freeze):
                continue
            if last_tick is None or (t - last_tick) >= 16:
                rx, ry = _tx(p["x"], p["y"], meta)
                seq.append({"t": _sec(t, freeze, tick_rate), "rx": _clamp01(rx), "ry": _clamp01(ry),
                            "alive": bool(p.get("alive", True)), "yaw": p.get("yaw")})
                last_tick = t
        if pts:
            lastp = pts[-1]
            if lastp.get("tick") is not None and (freeze is None or lastp["tick"] >= freeze):
                if not seq or seq[-1]["t"] != _sec(lastp["tick"], freeze, tick_rate):
                    rx, ry = _tx(lastp["x"], lastp["y"], meta)
                    seq.append({"t": _sec(lastp["tick"], freeze, tick_rate), "rx": _clamp01(rx),
                                "ry": _clamp01(ry), "alive": bool(lastp.get("alive", True)), "yaw": lastp.get("yaw")})
        out.append({"uid": _player_uid(pid, team_of, side_by_pid), "side": side_by_pid.get(pid),
                    "team": team_of.get(int(pid)), "outcome": pd.get("outcome", "unknown") if pd else "unknown",
                    "points": seq})
    out.sort(key=lambda x: (x["side"] == "T", x["uid"]))
    return out, side_by_pid


def build_events(rnd, team_of, side_by_pid, tick_rate, freeze):
    players = [int(p) for p in rnd.get("players", {})]
    team_of_map = {int(k): v for k, v in team_of.items()}

    def uid(pid):
        return _player_uid(pid, team_of_map, side_by_pid)

    kills = []
    for k in rnd.get("killfeed", []):
        att, vic = k.get("attacker_player_id"), k.get("victim_player_id")
        if att is None or vic is None:
            continue
        kills.append({"time": _sec(k.get("tick"), freeze, tick_rate), "attacker": uid(att), "victim": uid(vic),
                      "attacker_team": team_of_map.get(att), "victim_team": team_of_map.get(vic),
                      "weapon": k.get("weapon"), "headshot": bool(k.get("headshot")), "self_kill": bool(k.get("self_kill"))})
    kills.sort(key=lambda x: (x["time"] or 0))

    trades = []
    by_att = {}
    for k in rnd.get("killfeed", []):
        att, vic = k.get("attacker_player_id"), k.get("victim_player_id")
        if att is None or vic is None or att == vic:
            continue
        by_att.setdefault(att, []).append((k.get("tick"), vic))
    for k in rnd.get("killfeed", []):
        att, vic = k.get("attacker_player_id"), k.get("victim_player_id")
        if att is None or vic is None or att == vic:
            continue
        if team_of_map.get(att) == team_of_map.get(vic):
            continue
        killer_team = team_of_map.get(att)
        for (kt, kvic) in by_att.get(att, []):
            for m in rnd.get("killfeed", []):
                mat, mvic, mtick = m.get("attacker_player_id"), m.get("victim_player_id"), m.get("tick")
                if mvic == att and team_of_map.get(mat) == team_of_map.get(vic) and team_of_map.get(mat) != killer_team:
                    if mtick is not None and kt is not None and 0 <= _sec(mtick, freeze, tick_rate) - _sec(kt, freeze, tick_rate) <= TRADE_WINDOW_SEC:
                        trades.append({"time": _sec(kt, freeze, tick_rate), "traded_time": _sec(mtick, freeze, tick_rate),
                                       "victim": uid(vic), "killer": uid(att), "trader": uid(mat)})
                        break
    trades = [t for i, t in enumerate(trades) if trades.index(t) == i]

    blinds = []
    for b in rnd.get("blinds", []):
        att, vic = b.get("attacker_player_id"), b.get("victim_player_id")
        if att is None or vic is None:
            continue
        blinds.append({"time": _sec(b.get("tick"), freeze, tick_rate), "attacker": uid(att), "victim": uid(vic),
                       "duration": b.get("blind_duration")})
    blinds.sort(key=lambda x: (x["time"] or 0))

    he = []
    for d in rnd.get("he_damage", []):
        att, vic = d.get("attacker_player_id"), d.get("victim_player_id")
        if att is None or vic is None:
            continue
        he.append({"time": _sec(d.get("tick"), freeze, tick_rate), "attacker": uid(att), "victim": uid(vic),
                   "dmg": d.get("damage")})
    he.sort(key=lambda x: (x["time"] or 0))

    return kills, trades, blinds, he


def build_area_payload(rnd, meta, tick_rate, team_of):
    from app.services.region_geometry import load_region_geometry
    from app.services.area_state import build_region_state_machine_for_round

    geo = load_region_geometry(meta["name"])
    if not geo:
        return []
    region_list = geo.get("regions", [])
    pts_by_pid = {int(pid): pd for pid, pd in rnd.get("players", {}).items()}
    members = {"A": [p for p in pts_by_pid if team_of.get(p) == "A"], "B": [p for p in pts_by_pid if team_of.get(p) == "B"]}
    props = rnd.get("props", [])
    blinds_a = rnd.get("blinds", [])
    out = []
    for region in region_list:
        rk = region["place_key"]
        entry = {"place_key": rk, "place_cn": region.get("place_cn"), "centroid": region.get("centroid"),
                 "geometric": region.get("geometric"), "coverage": region.get("coverage"),
                 "confidence": region.get("confidence"), "polygon": region.get("polygon", []),
                 "segments": {"A": [], "B": []}, "transitions": []}
        for t in ("A", "B"):
            thrower_my = set(p.get("thrower_player_id") for p in props if p.get("thrower_player_id") in members[t])
            sm = build_region_state_machine_for_round(region, pts_by_pid, rnd, tick_rate, members, props, blinds_a, thrower_my, _norm_place)
            segs = sm.get("segments", {}).get(t, [])
            clipped = []
            for s in segs:
                e = s["end_sec"]
                st = max(0.0, s["start_sec"])
                if e <= 0:
                    continue
                clipped.append({"start": round(st, 2), "end": round(e, 2), "state": s["state"],
                                "my_alive_avg": s.get("my_alive_avg"), "enemy_alive_max": s.get("enemy_alive_max"),
                                "signal": s.get("signal")})
            entry["segments"][t] = clipped
            entry["transitions"] += [{"team": t, "state": x["state"], "start": round(max(0.0, x["start_sec"]), 2),
                                      "end": round(x["end_sec"], 2)} for x in sm.get("transitions", []) if x.get("team") == t and x["end_sec"] > 0]
        entry["transitions"].sort(key=lambda x: (x["start"], x["team"]))
        out.append(entry)
    out.sort(key=lambda r: r["place_key"])
    return out


def build_evidence(areas, events, round_meta):
    evidence = []
    strong = []
    for a in areas:
        for t in ("A", "B"):
            for s in a["segments"].get(t, []):
                if s["state"] in ("controlled", "contested") and s["end"] - s["start"] >= 4.0:
                    strong.append((t, a.get("place_cn") or a["place_key"], s["state"], s["start"], s["end"], a["place_key"]))
    strong.sort(key=lambda x: x[3])
    for t, cn, state, start, end, pk in strong[:3]:
        evidence.append({"time": start, "kind": "control", "place_key": pk, "place_cn": cn, "state": state, "team": t,
                         "title": f"{cn}「{'控制' if state=='controlled' else '争夺'}」",
                         "detail": f"第 {round_meta.get('number')} 回合 · {cn} · {'控制成立' if state=='controlled' else '双方争夺'}"})
    kt = [e for e in events.get("kills", []) if e["time"] is not None]
    if kt:
        k0 = kt[0]
        evidence.append({"time": k0["time"], "kind": "kill", "place_key": "", "title": "回合首杀",
                         "detail": f"第 {round_meta.get('number')} 回合首杀 · {k0['attacker']} 击杀 {k0['victim']}"})
    evidence.sort(key=lambda x: x["time"])
    return evidence


def main() -> int:
    from app.services.storage import storage

    round_no = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROUND
    data = storage.get_bytes(ARTIFACT_KEY)
    if hashlib.sha256(data).hexdigest() != ARTIFACT_SHA256:
        print("ERROR: artifact sha256 mismatch; refusing to export")
        return 1
    raw = json.loads(gzip.decompress(data))
    if raw.get("match_id") != MATCH_ID:
        print("ERROR: unexpected match_id")
        return 1
    if raw.get("map_name") != MAP_NAME:
        print(f"ERROR: unexpected map_name {raw.get('map_name')}")
        return 1

    tick_rate = raw.get("tick_rate")
    rounds = raw.get("rounds", [])
    rnd = next((r for r in rounds if r.get("round") == round_no), None)
    if rnd is None:
        print(f"ERROR: round {round_no} missing")
        return 1

    meta = dict(MAP_META)
    freeze = rnd.get("freeze_end_tick") or rnd.get("start_tick")
    players, side_by_pid = build_player_timeline(rnd, meta, tick_rate, TEAM_OF)
    kills, trades, blinds, he = build_events(rnd, TEAM_OF, side_by_pid, tick_rate, freeze)
    areas = build_area_payload(rnd, meta, tick_rate, TEAM_OF)
    evidence = build_evidence(areas, {"kills": kills}, {"number": round_no})
    max_duration = _sec(rnd["end_tick"], freeze, tick_rate)
    team_side_map = rnd.get("team_side_map", {})

    sample = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "source": "demo21 tactical artifact v3.0 (match 61) via radar transform",
            "artifact_sha256": ARTIFACT_SHA256, "map": meta["name"], "round": round_no, "match_id": MATCH_ID,
            "tick_rate": tick_rate, "anonymized": True, "no_steam_ids": True, "no_player_names": True,
            "generator": "scripts/export_dust2_round_sample.py",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "team_side_map": {k: ("CT" if v == 2 else "T") for k, v in team_side_map.items()},
            "winner_team": rnd.get("winner_team"), "winner_side": rnd.get("winner_side"),
            "provenance": "世界坐标→雷达归一化坐标 (radar_x=(x-pos_x)/scale/1024; radar_y=(pos_y-y)/scale/1024)；"
                          "de_dust2 权威 transform (pos_x=-2476, pos_y=3239, scale=4.4)；区域几何来自 last_place_name 真实样本凸包；"
                          "区域状态来自 6R16 状态机；击杀/致盲/HE 来自真实 killfeed/blind/hurt；"
                          "补枪=队友阵亡后 5s 内击杀该击杀者；全部匿名，不含 SteamID/昵称/库主键。",
        },
        "map": meta,
        "round": {"number": round_no, "max_duration": max_duration, "freeze_sec": 0.0,
                  "winner_team": rnd.get("winner_team"), "winner_side": rnd.get("winner_side"),
                  "team_side_map": {k: ("CT" if v == 2 else "T") for k, v in team_side_map.items()}},
        "players": players, "areas": areas,
        "events": {"kills": kills, "trades": trades, "blinds": blinds, "he_damage": he},
        "evidence": evidence,
    }
    _content = dict(sample)
    _content["meta"] = {k: v for k, v in sample["meta"].items() if k != "generated_at"}
    sample["content_hash"] = _content_hash(_content)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {OUT_PATH} (round {round_no})")
    print(f"  content_hash   = {sample['content_hash']}")
    print(f"  winner_team    = {sample['round']['winner_team']}")
    print(f"  players        = {len(players)}")
    print(f"  areas          = {len(areas)}")
    print(f"  kills          = {len(kills)}")
    print(f"  trades         = {len(trades)}")
    print(f"  blinds         = {len(blinds)}")
    print(f"  evidence       = {len(evidence)}")
    print(f"  max_duration   = {max_duration}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
