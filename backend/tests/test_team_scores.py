"""6R0 RED：换边后队伍比分正确性（roster 贯穿 + 逐回合 winner→队伍映射）。

旧实现按 winner 阵营计数（CT/T 胜局数），换边后不等于队伍比分。
场景：18 回合，前半 CT 胜 5/T 胜 6（11 回合），换边后新 T 侧（队伍 A）胜 2、新 CT 侧（队伍 B）胜 5（7 回合）。
正确队伍比分 = A:(5+2)=7, B:(6+5)=11 → 7:11（旧实现 10:8）。
"""
import pandas as pd
import pytest


class _FakeParser:
    """mock demoparser2：round_start/round_end 事件 + 按 tick 的玩家阵营（换边翻转）。"""

    def __init__(self, starts, ends, roster, switch_tick):
        self._starts = pd.DataFrame(starts)
        self._ends = pd.DataFrame(ends)
        self._roster = roster          # steamid -> "A"/"B"
        self._switch = switch_tick

    def parse_event(self, name, **kwargs):
        return self._starts if name == "round_start" else self._ends

    def parse_ticks(self, fields, ticks=None):
        # 返回每 tick 每玩家一行：steamid + 阵营（换边前 A=CT(2)/B=T(3)，换边后翻转）
        rows = []
        for t in ticks or []:
            flipped = t >= self._switch
            for sid, team in self._roster.items():
                side = 2 if (team == "A") != flipped else 3
                rows.append({"tick": t, "steamid": sid, "CCSPlayerPawn.m_iTeamNum": side})
        return pd.DataFrame(rows)


def _make_analyzer(starts, ends, roster, switch_tick):
    from app.services.parser import DemoAnalyzer

    a = DemoAnalyzer.__new__(DemoAnalyzer)
    a._parser = _FakeParser(starts, ends, roster, switch_tick)
    return a


def _build_18_round_scenario():
    # 18 回合：前半 11（tick 100..63613），换边 tick=64000，后半 7
    roster = {f"p{i}": ("A" if i <= 5 else "B") for i in range(1, 11)}  # A=CT 开局
    starts = [{"tick": 100 + i * 6000} for i in range(18)]
    # 前半 winner: CT(2) x5, T(3) x6；后半（换边后）: 新T侧(=A) x2, 新CT侧(=B) x5
    first_half = [2, 2, 3, 3, 2, 3, 3, 3, 2, 3, 3]          # CT 5 : T 6
    second_half = [3, 3, 2, 2, 2, 2, 2]                     # 新T(A) 2 : 新CT(B) 5
    winners = first_half + second_half
    ends = [{"tick": starts[i]["tick"] + 5000, "winner": w} for i, w in enumerate(winners)]
    return starts, ends, roster


def test_team_scores_after_side_switch():
    """换边后比分必须按队伍身份（7:11），而非阵营胜局（10:8）。"""
    from app.services.stats import compute_match_score

    starts, ends, roster = _build_18_round_scenario()
    a = _make_analyzer(starts, ends, roster, switch_tick=64000)
    rounds = a.parse_rounds()

    assert len(rounds) == 18
    assert compute_match_score(rounds) == (7, 11)


def test_round_winner_team_and_side_map():
    """每回合记录 winner_side（阵营）+ winner_team（队伍身份）+ team_side_map。"""
    starts, ends, roster = _build_18_round_scenario()
    a = _make_analyzer(starts, ends, roster, switch_tick=64000)
    rounds = a.parse_rounds()

    first = rounds[0]
    assert first["winner_side"] == 2          # 第 1 回合 CT 胜
    assert first["winner_team"] == "A"        # 开局 CT = 队伍 A
    assert first["team_side_map"]["A"] == 2
    assert first["team_side_map"]["B"] == 3

    switched = rounds[11]                      # 换边后第一回合
    assert switched["winner_side"] == 3        # 新 T 侧胜
    assert switched["winner_team"] == "A"      # 新 T 侧 = 队伍 A
    assert switched["team_side_map"]["A"] == 3
    assert switched["team_side_map"]["B"] == 2


def _analyzer_for(round_count, winners, switch_tick):
    """构造 n 回合 mock（winners: 2=CT 胜, 3=T 胜；换边点 switch_tick）。"""
    roster = {f"p{i}": ("A" if i <= 5 else "B") for i in range(1, 11)}
    starts = [{"tick": 100 + i * 6000} for i in range(round_count)]
    ends = [{"tick": 100 + i * 6000 + 5000, "winner": w} for i, w in enumerate(winners)]
    return _make_analyzer(starts, ends, roster, switch_tick=switch_tick)


def test_regulation_finish_13_x():
    """合法格式终局（无加时）：A 先到 13（13:9，22 回合）。"""
    from app.services.stats import compute_match_score

    # 上半场 12 回合 A 8:B 4；换边后下半场 10 回合 A 5:B 5 → A=13 B=9（A 先到 13 结束）
    winners = ([2] * 8 + [3] * 4) + ([3] * 5 + [2] * 5)
    rounds = _analyzer_for(22, winners, switch_tick=100 + 12 * 6000).parse_rounds()
    assert len(rounds) == 22
    assert compute_match_score(rounds) == (13, 9)
    # 注意：比分重建只统计事件；13:x 终局判定属“比赛格式合法性校验”，parser 不负责截断


def test_overtime_finish_16_14():
    """12:12 后单轮加时：终局 16:14（30 回合 = 24 + 加时 6）。"""
    from app.services.stats import compute_match_score

    # 24 回合 12:12（A 上半 CT 6:6，换边后 6:6）；加时 6 回合 A 4:B 2 → 16:14
    winners = ([2] * 6 + [3] * 6) + ([3] * 6 + [2] * 6) + [3, 3, 3, 3, 2, 2]
    rounds = _analyzer_for(30, winners, switch_tick=100 + 12 * 6000).parse_rounds()
    assert len(rounds) == 30
    assert compute_match_score(rounds) == (16, 14)


def test_multi_overtime_finish_19_17():
    """多轮加时：19:17（36 回合 = 24 + 加时 12，A 7:B 5）。"""
    from app.services.stats import compute_match_score

    winners = ([2] * 6 + [3] * 6) + ([3] * 6 + [2] * 6) + [3] * 7 + [2] * 5
    rounds = _analyzer_for(36, winners, switch_tick=100 + 12 * 6000).parse_rounds()
    assert len(rounds) == 36
    assert compute_match_score(rounds) == (19, 17)
