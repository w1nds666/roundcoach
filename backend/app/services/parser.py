"""demoparser2 解析封装（计划书 4.3 services/parser.py、7.1 解析流程）。

已在真实 demo（官方 test_demo.dem，de_mirage，57.8MB）验证：
- parse_header 实际字段：map_name / server_name 等（无 player_count/tick_rate/playout_ticks）
- player_death 事件字段：user_X/user_Y（死者坐标）、attacker_X/attacker_Y（凶手坐标）、
  weapon/headshot/distance/tick 等（无 round 字段，回合号需由 tick 映射，Phase 1 实现）
- parse_grenades 返回 tick 级投掷物位置（57MB demo 约 51 万行），入库需聚合为事件级

设计要点（对应计划书 7.1）：
- parse_header：比赛头信息（地图/服务器），玩家数与时长由其他解析补充
- parse_player_info：双方阵容
- parse_events：事件（player_death / player_hurt 等）
- parse_grenades：投掷物 tick 级位置（道具分析数据源）
- parse_ticks：tick 级字段（X/Y 坐标、money、health 等，路线热力图数据源）
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from demoparser2 import DemoParser

# 常用 tick 字段（demoparser2 字段名，已在真实 demo 验证）
TICK_FIELDS_POSITION = ["X", "Y", "Z"]
TICK_FIELDS_BASIC = ["last_place_name", "money", "health", "armor", "CCSPlayerPawn.m_iTeamNum"]

# 死亡事件字段（真实字段名，实测验证）
# player 参数传 tick 属性（X/Y）→ 事件自动附加 user_X/user_Y（死者坐标）
# attacker_X/attacker_Y 为事件自身字段（6R1：显式请求，保证结构化坐标可用）
DEATH_PLAYER_FIELDS = ["X", "Y"]
DEATH_OTHER_FIELDS = [
    "attacker_steamid", "attacker_name",
    "attacker_X", "attacker_Y",
    "user_steamid", "user_name",
    "weapon", "headshot", "distance", "hitgroup",
    "tick", "thrusmoke", "penetrated", "noscope", "assistedflash",
    "assister_steamid", "assister_name",   # 6R13：助攻（击杀播报）
]

# 伤害事件字段
HURT_PLAYER_FIELDS = ["X", "Y"]
HURT_OTHER_FIELDS = [
    "attacker_steamid", "attacker_name",
    "weapon", "dmg_health", "dmg_armor", "health", "armor",
    "hitgroup", "tick",
]

# CS2（Source 2）demo 的 tick_rate 不在 CDemoFileHeader 中，需从 .dem 二进制内的
# CDemoFileInfo.playback_ticks/playback_time 推导；无法证明时置 None（调用方不得显示秒）。
# 保留该常量仅为构建器签名默认值的兼容回退（真实解析总是传经检测的 tick_rate）。
CS2_TICK_RATE = 128


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    """LEB128 varint → (value, 下一偏移)。"""
    r = 0
    s = 0
    while i < len(data):
        x = data[i]
        i += 1
        r |= (x & 0x7F) << s
        if not (x & 0x80):
            return r, i
        s += 7
        if s > 63:
            break
    return r, i


def read_cdemo_file_info(demo_path: str | Path) -> dict | None:
    """读取 CS2（Source 2）.dem 内的 CDemoFileInfo 权威回放元数据。

    结构（source2-demo crate / demo.proto，已在 5 个验收 demo 验证）：
      - 字节 0..8   = magic 'PBDEMS2\\0'
      - 字节 8..12  = u32 LE：CDemoFileInfo 消息在文件中的偏移
      - 该偏移处帧头 = 3 个 LEB128 varint（cmd, tick, size）+ size 字节 protobuf 载荷
        （cmd 的 0x40 位表示 Snap 压缩；本批 demo 均未压缩，cmd=2）
      - protobuf: field1=f32 playback_time, field2=varint playback_ticks, field3=varint playback_frames

    返回 {"playback_ticks","playback_time","playback_frames"}；无法解析返回 None。
    """
    try:
        with open(demo_path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:8] != b"PBDEMS2\x00":
                return None
            offset = struct.unpack("<I", head[8:12])[0]
            f.seek(offset)
            data = f.read(1 << 20)  # CDemoFileInfo 载荷很小，取足够字节即可
    except OSError:
        return None

    try:
        _cmd, i = _read_varint(data, 0)
        _tick, i = _read_varint(data, i)
        size, i = _read_varint(data, i)
        if i + size > len(data):
            return None
        payload = data[i:i + size]
    except Exception:  # noqa: BLE001
        return None

    playback_time = None
    playback_ticks = None
    playback_frames = None
    j = 0
    while j < len(payload):
        key, j = _read_varint(payload, j)
        field_id = key >> 3
        wire = key & 7
        if wire == 0:
            v, j = _read_varint(payload, j)
            if field_id == 2:
                playback_ticks = v
            elif field_id == 3:
                playback_frames = v
        elif wire == 5:
            if j + 4 <= len(payload):
                playback_time = struct.unpack_from("<f", payload, j)[0]
                j += 4
            else:
                break
        elif wire == 2:
            ln, j = _read_varint(payload, j)
            j += ln
        else:
            break

    if playback_ticks and playback_time:
        return {
            "playback_ticks": playback_ticks,
            "playback_time": playback_time,
            "playback_frames": playback_frames,
        }
    return None


@dataclass
class HeaderInfo:
    """比赛头信息（对应 matches 表）。"""
    map_name: str
    server_name: str
    player_count: int
    tick_rate: float | None   # 权威时基；无法证明 → None（调用方不得显示秒）
    playout_ticks: int
    duration_seconds: float | None
    raw: dict = field(default_factory=dict)


class DemoAnalyzer:
    """单个 .dem 文件的解析封装。"""

    def __init__(self, demo_path: str | Path):
        self.demo_path = str(demo_path)
        self._parser = DemoParser(self.demo_path)

    # ---------- 头信息 ----------
    def parse_header(self) -> HeaderInfo:
        """比赛头信息：地图/服务器（header 块）；玩家数、tick 数、时长由解析补充。

        说明：parse_header() 不含 tick_rate/playout_ticks/player_count，
        因此额外解析一次 tick 列表（仅 tick 字段，Rust 内核很快）。
        """
        header = self._parser.parse_header()
        map_name = header.get("map_name", "")
        server_name = header.get("server_name", "")

        player_count = len(self.parse_player_info())

        # 权威时基：CDemoFileInfo.playback_ticks/playback_time（在 .dem 二进制内，非 header）。
        # header（CDemoFileHeader）无 tick_rate 字段；若无法证明 → tick_rate=None → 调用方不得显示秒。
        # 测试常用 __new__ 构造（无 demo_path）→ 用 getattr 兜底，不会误报。
        info = read_cdemo_file_info(self.demo_path) if getattr(self, "demo_path", None) else None
        playout_ticks = int(info["playback_ticks"]) if info else 0
        tick_rate = None
        duration = None
        if info and info.get("playback_ticks") and info.get("playback_time"):
            tick_rate = round(info["playback_ticks"] / info["playback_time"], 2)
            duration = round(playout_ticks / tick_rate, 1) if playout_ticks else 0.0

        # 若 CDemoFileInfo 缺失，回退用 tick 列表推 playout_ticks（时长随 tick_rate=None 而不显示秒）
        if not info:
            tick_df = self._parser.parse_ticks(["tick"])
            ticks = sorted(tick_df["tick"].unique().tolist())
            playout_ticks = ticks[-1] if ticks else 0

        return HeaderInfo(
            map_name=map_name,
            server_name=server_name,
            player_count=player_count,
            tick_rate=round(tick_rate, 2) if tick_rate is not None else None,
            playout_ticks=playout_ticks,
            duration_seconds=round(duration, 1) if duration is not None else None,
            raw=header,
        )

    # ---------- 玩家 ----------
    def parse_player_info(self) -> list[dict]:
        """双方阵容（匹配计划书 players 表：steam_id/name/team）。"""
        df = self._parser.parse_player_info()
        return df.to_dict(orient="records")

    # ---------- 事件 ----------
    def parse_deaths(self) -> list[dict]:
        """击杀事件：双方坐标/武器/是否爆头（击杀热力图与死亡复盘数据源）。"""
        df = self._parser.parse_event("player_death", player=DEATH_PLAYER_FIELDS, other=DEATH_OTHER_FIELDS)
        return df.to_dict(orient="records")

    def parse_hurts(self) -> list[dict]:
        """伤害事件（伤害统计、ADR 数据源）。"""
        df = self._parser.parse_event("player_hurt", player=HURT_PLAYER_FIELDS, other=HURT_OTHER_FIELDS)
        return df.to_dict(orient="records")

    def parse_grenades(self) -> list[dict]:
        """投掷物 tick 级位置（道具分析数据源）。

        注意：返回 tick 级（每 tick 每投掷物一行，57MB demo 约 51 万行）。
        Phase 1 入库时需聚合为事件级（thrown/detonated 时刻），避免膨胀。
        """
        df = self._parser.parse_grenades()
        return df.to_dict(orient="records")

    # ---------- 回合 ----------
    @staticmethod
    def _normalize_winner(winner) -> int | None:
        """round_end.winner 归一化：数字 2/3 或字符串 'CT'/'T' → 2/3；无效（nan/None/其他）→ None。

        实测（MVR 真实 demo）：部分 demo 的 winner 为字符串 'CT'/'T'，此前被漏判导致比分 0:0（P1）。
        """
        if winner in (2, 3):
            return winner
        if winner == "CT":
            return 2
        if winner == "T":
            return 3
        return None

    def parse_rounds(self) -> list[dict]:
        """回合列表（6R0 重写：roster 贯穿 + tick 时间窗配对 + 队伍比分语义）。

        正确性规则：
        - tick 时间窗配对（非数组下标）：本回合 end = 首个 tick ≥ start 且 < 下一 start 的 round_end
        - 跳过热身回合（tick==0 且无有效 winner，不计入正式 rounds/比分）
        - 未闭合尾回合（round_start 无配对 round_end）不计入正式 rounds
        - winner 归一化（'CT'/'T' 字符串 → 2/3）
        - roster：首回合玩家阵营构建队伍身份（A=开局 CT 侧，贯穿整场含换边/加时）
        - 每回合记录 winner_side（阵营 2/3）、winner_team（队伍 A/B，换边正确）、
          team_side_map（该回合 A/B 分别所在阵营）
        返回 [{"round": 1, "start_tick": 65, "end_tick": 8971, "winner_side": 2,
                "winner_team": "A", "team_side_map": {"A": 2, "B": 3}}, ...]
        """
        starts = self._parser.parse_event("round_start", other=["tick"])
        ends = self._parser.parse_event("round_end", other=["tick", "winner"])
        freezes = self._parser.parse_event("round_freeze_end", other=["tick"])
        start_rows = sorted(starts.to_dict(orient="records"), key=lambda r: r["tick"])
        end_rows = sorted(ends.to_dict(orient="records"), key=lambda r: r["tick"])
        freeze_rows = sorted(freezes.to_dict(orient="records"), key=lambda r: r["tick"])

        if not start_rows:
            return []

        # roster：首回合玩家阵营 → 队伍身份（A=CT 侧开局）
        roster = self._build_roster(start_rows[0]["tick"])

        rounds = []
        end_idx = 0
        for i, s in enumerate(start_rows):
            nxt_tick = start_rows[i + 1]["tick"] if i + 1 < len(start_rows) else None
            # 时间窗配对
            end = None
            while end_idx < len(end_rows):
                e = end_rows[end_idx]
                if e["tick"] < s["tick"]:
                    end_idx += 1
                    continue
                if nxt_tick is not None and e["tick"] >= nxt_tick:
                    break  # 属于下一回合
                end = e
                end_idx += 1
                break
            winner_side = self._normalize_winner(end.get("winner") if end else None)

            # 跳过热身：tick==0 且无有效 winner
            if s["tick"] == 0 and winner_side is None:
                continue
            # 未闭合尾回合不计入正式 rounds
            if end is None:
                continue

            # 该回合的阵营映射（在 end tick 时队伍 A/B 所在阵营）
            side_map = self._side_map_at(end["tick"], roster)
            winner_team = None
            if winner_side is not None:
                winner_team = "A" if side_map.get("A") == winner_side else "B"

            # playable 原点 = round_freeze_end（该回合窗口内最近的 freeze_end）
            # 权威 timebase：replay 00:00 = freeze_end，时长 = end - freeze_end（不再固定减 10 秒）
            start_tick, end_tick = s["tick"], end["tick"]
            freeze_end_tick = next((int(f["tick"]) for f in freeze_rows if start_tick <= int(f["tick"]) < end_tick), None)

            rounds.append({
                "round": len(rounds) + 1,
                "start_tick": start_tick,
                "end_tick": end_tick,
                "freeze_end_tick": freeze_end_tick,
                "winner_side": winner_side,
                "winner_team": winner_team,
                "team_side_map": side_map,
            })
        return rounds

    def _build_roster(self, tick: int) -> dict[str, str]:
        """首回合玩家阵营 → {steamid: 'A'|'B'}（A=CT 侧，贯穿整场）。"""
        df = self._parser.parse_ticks(["CCSPlayerPawn.m_iTeamNum"], ticks=[tick])
        team_of: dict[str, str] = {}
        for r in df.to_dict(orient="records"):
            side = r.get("CCSPlayerPawn.m_iTeamNum")
            if side in (2, 3):
                team_of[str(r["steamid"])] = "A" if side == 2 else "B"
        return team_of

    def _side_map_at(self, tick: int, roster: dict[str, str]) -> dict[str, int]:
        """该 tick 时队伍 A/B 所在阵营（取多数；无玩家时继承默认 A=CT）。"""
        df = self._parser.parse_ticks(["CCSPlayerPawn.m_iTeamNum"], ticks=[tick])
        votes = {"A": {2: 0, 3: 0}, "B": {2: 0, 3: 0}}
        for r in df.to_dict(orient="records"):
            team = roster.get(str(r["steamid"]))
            side = r.get("CCSPlayerPawn.m_iTeamNum")
            if team in votes and side in (2, 3):
                votes[team][side] += 1
        side_a = max(votes["A"], key=votes["A"].get) if any(votes["A"].values()) else 2
        return {"A": side_a, "B": 3 if side_a == 2 else 2}

    @staticmethod
    def round_mapper(rounds: list[dict]) -> callable:
        """tick → round 号映射（二分查找）；tick 落在任一正式回合 [start,end] 之外 → None。

        6R10A 权威修复：旧实现用 `else 1` 兜底，把首回合 start 之前（备战/热身/刀局）
        的事件错标为 round=1，污染回合 1 的配合分析与回放（并由 `_sec` 的 max(0)
        钳成 00:00 正赛事件、掩盖边界错误）。现与 routes._round_mapper 语义对齐：
        - 首回合 start 之前 / 末回合 end 之后 / 回合间空隙 → None（"不在任何正式回合"）。
        - 调用方须把 None 视为备战/场外事件（入库存 0；统计/配合指标排除）。
        """
        import bisect

        starts = [int(r["start_tick"]) for r in rounds]
        ends = [int(r["end_tick"]) for r in rounds]

        def mapper(tick: int) -> int | None:
            idx = bisect.bisect_right(starts, tick) - 1
            if idx < 0:
                return None
            r = rounds[idx]
            if tick < r["start_tick"] or tick > r["end_tick"]:
                return None
            return r["round"]

        return mapper

    def reconcile_score(self, rounds: list[dict]) -> tuple[bool, str | None]:
        """6R0.1：authoritative team score reconciliation。

        用引擎字段 CCSTeam.m_iScore 的末局稳定值（数值集合）交叉核验重建比分，
        检测多算/漏算并返回 warning（不静默声称正确）。
        限制（如实记录）：m_iScore 在部分长局（OT/换边）存在实体交换与
        round_end tick 更新延迟，故采用末局稳定值 + 数值集合比对；
        tick 级 m_gamePhase/m_bWarmupPeriod 字段存在但未用于回合判定
        （事件配对 + roster 已正确重建比分；game_phase 作后续校验增强）。
        返回 (ok, warning)；ok=False 且 warning 描述不一致详情。
        """
        if not rounds:
            return True, None
        score_a = sum(1 for r in rounds if r.get("winner_team") == "A")
        score_b = sum(1 for r in rounds if r.get("winner_team") == "B")
        if score_a == 0 and score_b == 0:
            return True, None  # 无有效 winner（数据不足），不误报

        header = self.parse_header()
        last_tick = rounds[-1]["end_tick"]
        max_tick = int(header.playout_ticks or (last_tick + 4000))

        def read_scores(tick: int) -> list[int] | None:
            df = self._parser.parse_ticks(["CCSTeam.m_iScore", "CCSPlayerPawn.m_iTeamNum"], ticks=[tick])
            if "CCSPlayerPawn.m_iTeamNum" not in df.columns or df.empty:
                return None
            return sorted(int(v) for v in df.groupby("CCSPlayerPawn.m_iTeamNum")["CCSTeam.m_iScore"].max().tolist())

        scores = read_scores(min(last_tick + 4000, max_tick)) or read_scores(last_tick)
        if scores is None:
            return True, None  # 无法读取引擎比分（如无玩家实体），不误报
        if scores == sorted([score_a, score_b]):
            return True, None
        return False, (f"比分重建与引擎比分不一致：重建 {score_a}:{score_b}，引擎 {scores[0]}:{scores[1]}")


    # ---------- tick 级 ----------
    def parse_positions(self, tick_step: int = 1) -> list[dict]:
        """全量 tick 位置（路线热力图数据源；128tick×30min≈23万行，入库走批量）。"""
        df = self._parser.parse_ticks(TICK_FIELDS_POSITION + TICK_FIELDS_BASIC + ["tick"], ticks=range(0, 2**31, tick_step))
        return df.to_dict(orient="records")


def analyze_all(demo_path: str | Path) -> dict:
    """一键解析：返回结构化 dict（Phase 1 入库前的中间格式）。"""
    analyzer = DemoAnalyzer(demo_path)
    return {
        "header": analyzer.parse_header(),
        "players": analyzer.parse_player_info(),
        "deaths": analyzer.parse_deaths(),
        "hurts": analyzer.parse_hurts(),
        "grenades": analyzer.parse_grenades(),
        # positions 默认不取，量太大，由调用方按需调用
    }
