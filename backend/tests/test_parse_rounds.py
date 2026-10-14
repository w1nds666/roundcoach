"""parse_rounds 正确性回归（MVR 真实 demo P1 + 6R0 队伍比分语义）。

覆盖：热身回合跳过、winner 字符串归一化（'CT'/'T'）、未闭合尾回合不计入、
tick 时间窗配对、winner_side/winner_team 字段。
"""
import pytest


class _FakeParser:
    """mock demoparser2：按构造数据返回 round_start/round_end DataFrame。"""
    def __init__(self, starts, ends):
        import pandas as pd

        self._starts = pd.DataFrame(starts)
        self._ends = pd.DataFrame(ends)

    def parse_event(self, name, **kwargs):
        return self._starts if name == "round_start" else self._ends

    def parse_ticks(self, fields, ticks=None):
        import pandas as pd

        # 无玩家数据：roster 空 → side_map 默认 A=CT(2)/B=T(3)
        return pd.DataFrame(columns=["tick", "steamid", "CCSPlayerPawn.m_iTeamNum"])


def _make_analyzer(starts, ends):
    from app.services.parser import DemoAnalyzer

    a = DemoAnalyzer.__new__(DemoAnalyzer)
    a._parser = _FakeParser(starts, ends)
    return a


def test_warmup_round_skipped():
    """tick=0 且 winner 无效的热身回合不计入正式 rounds。"""
    starts = [{"tick": 0}, {"tick": 3896}, {"tick": 9397}]
    ends = [{"tick": 0, "winner": float("nan")}, {"tick": 9077, "winner": "T"}, {"tick": 14576, "winner": "CT"}]
    rounds = _make_analyzer(starts, ends).parse_rounds()
    assert len(rounds) == 2
    assert rounds[0]["round"] == 1
    assert rounds[0]["start_tick"] == 3896
    assert rounds[1]["winner_side"] == 2      # 'CT' → 2
    assert rounds[1]["winner_team"] == "A"    # 默认 side_map A=CT


def test_string_winner_normalized():
    """字符串 'CT'/'T' 归一化为 2/3（此前漏判导致比分 0:0）。"""
    starts = [{"tick": 100}, {"tick": 200}]
    ends = [{"tick": 150, "winner": "CT"}, {"tick": 250, "winner": "T"}]
    rounds = _make_analyzer(starts, ends).parse_rounds()
    assert [r["winner_side"] for r in rounds] == [2, 3]
    assert [r["winner_team"] for r in rounds] == ["A", "B"]


def test_numeric_winner_preserved():
    starts = [{"tick": 100}]
    ends = [{"tick": 150, "winner": 2}]
    rounds = _make_analyzer(starts, ends).parse_rounds()
    assert rounds[0]["winner_side"] == 2


def test_unclosed_tail_round_excluded():
    """round_start 多 1（未闭合尾回合）不计入正式 rounds。"""
    starts = [{"tick": 100}, {"tick": 200}, {"tick": 300}]
    ends = [{"tick": 150, "winner": "CT"}, {"tick": 250, "winner": "T"}]
    rounds = _make_analyzer(starts, ends).parse_rounds()
    assert len(rounds) == 2
    assert rounds[-1]["round"] == 2


def test_invalid_winner_does_not_score():
    """winner 无效（None/其他）的回合计入 rounds 但不影响比分。"""
    from app.services.stats import compute_match_score

    starts = [{"tick": 100}, {"tick": 200}]
    ends = [{"tick": 150, "winner": None}, {"tick": 250, "winner": "T"}]
    rounds = _make_analyzer(starts, ends).parse_rounds()
    assert len(rounds) == 2
    assert compute_match_score(rounds) == (0, 1)


def test_tick_window_pairing_not_index():
    """tick 时间窗配对：end 归属 start 之后、下一 start 之前（非数组下标）。"""
    # 第 1 个 start 与第 2 个 start 之间有两个 end（restart 场景）：只配对窗口内第一个
    starts = [{"tick": 100}, {"tick": 500}]
    ends = [{"tick": 150, "winner": "CT"}, {"tick": 300, "winner": "T"}, {"tick": 600, "winner": "CT"}]
    rounds = _make_analyzer(starts, ends).parse_rounds()
    assert len(rounds) == 2
    assert rounds[0]["end_tick"] == 150     # 窗口内第一个 end
    assert rounds[1]["end_tick"] == 600     # 第二窗口的 end


def test_duplicate_round_start_no_double_count():
    """重复 round_start（同 tick 或 restart 事件）：不得重复计数。"""
    starts = [{"tick": 100}, {"tick": 100}, {"tick": 500}]   # tick=100 重复
    ends = [{"tick": 150, "winner": "CT"}, {"tick": 600, "winner": "T"}]
    rounds = _make_analyzer(starts, ends).parse_rounds()
    # 重复 start=100 无配对 end（被 500 窗口截断）→ 跳过，不产生额外回合
    assert len(rounds) == 2
    assert rounds[0]["start_tick"] == 100
    assert rounds[1]["start_tick"] == 500
    assert rounds[1]["end_tick"] == 600


def test_restart_start_within_previous_window():
    """game restart：新 start 出现在上一 end 之后、下一 start 之前 → 正常配对为下一回合。"""
    starts = [{"tick": 100}, {"tick": 400}]   # restart 在 400（上一 end=350 之后）
    ends = [{"tick": 150, "winner": "CT"}, {"tick": 350, "winner": "T"}, {"tick": 600, "winner": "CT"}]
    rounds = _make_analyzer(starts, ends).parse_rounds()
    assert len(rounds) == 2
    assert rounds[0]["end_tick"] == 150
    assert rounds[1]["end_tick"] == 600


class _ScoreParser(_FakeParser):
    """扩展 mock：parse_ticks 返回 CCSTeam.m_iScore（引擎比分核验）。"""
    def __init__(self, starts, ends, engine_scores):
        super().__init__(starts, ends)
        self._engine_scores = engine_scores   # {"stable": [a, b], "last": [a, b]}

    def parse_header(self):
        return {"playout_ticks": 10_000_000}

    def parse_player_info(self):
        import pandas as pd

        return pd.DataFrame(columns=["steamid", "name", "team_number"])

    def parse_ticks(self, fields, ticks=None):
        import pandas as pd

        if ticks is None:
            return pd.DataFrame(columns=["tick"])
        rows = []
        for t in ticks or []:
            if t in self._engine_scores:
                a, b = self._engine_scores[t]
                rows.append({"tick": t, "steamid": "p1", "CCSPlayerPawn.m_iTeamNum": 2, "CCSTeam.m_iScore": a})
                rows.append({"tick": t, "steamid": "p6", "CCSPlayerPawn.m_iTeamNum": 3, "CCSTeam.m_iScore": b})
        return pd.DataFrame(rows)


def _analyzer_with_scores(starts, ends, scores):
    from app.services.parser import DemoAnalyzer

    a = DemoAnalyzer.__new__(DemoAnalyzer)
    a._parser = _ScoreParser(starts, ends, scores)
    return a


def test_reconcile_score_ok():
    """reconciliation：引擎比分与重建一致 → ok 无警告。"""
    starts = [{"tick": 100}, {"tick": 600}]
    ends = [{"tick": 150, "winner": "CT"}, {"tick": 700, "winner": "T"}]
    # 重建：A=1(CT 胜), B=1(T 胜)；引擎稳定值 [1,1]
    a = _analyzer_with_scores(starts, ends, {700 + 4000: [1, 1], 700: [1, 1]})
    rounds = a.parse_rounds()
    ok, warning = a.reconcile_score(rounds)
    assert ok is True
    assert warning is None


def test_reconcile_score_mismatch_warns():
    """reconciliation：不一致 → ok=False + 明确 warning（多算/漏算检测）。"""
    starts = [{"tick": 100}, {"tick": 600}]
    ends = [{"tick": 150, "winner": "CT"}, {"tick": 700, "winner": "T"}]
    # 引擎 [1, 3] 与重建 [1,1] 不一致（漏算 2 局）
    a = _analyzer_with_scores(starts, ends, {700 + 4000: [1, 3], 700: [1, 1]})
    rounds = a.parse_rounds()
    ok, warning = a.reconcile_score(rounds)
    assert ok is False
    assert warning is not None
    assert "不一致" in warning


def test_reconcile_score_no_winner_no_false_positive():
    """无有效 winner 时不误报（数据不足场景）。"""
    starts = [{"tick": 100}]
    ends = [{"tick": 150, "winner": None}]
    a = _analyzer_with_scores(starts, ends, {150 + 4000: [0, 0], 150: [0, 0]})
    rounds = a.parse_rounds()
    ok, warning = a.reconcile_score(rounds)
    assert ok is True
    assert warning is None
