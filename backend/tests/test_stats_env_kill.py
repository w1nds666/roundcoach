"""6R1：环境击杀的受害者死亡必须计入 player_stats.deaths（actor 无效不得跳过 victim 死亡）。"""
from app.services.stats import compute_basic_stats


def test_env_kill_counts_victim_death():
    """环境杀（actor 不在玩家列表）：victim 的 deaths 仍 +1（kills 不计）。"""
    deaths = [
        # 正常击杀：A 杀 B
        {"tick": 100, "round": 1, "attacker_steamid": "111", "user_steamid": "222",
         "weapon": "ak47", "headshot": False},
        # 环境杀：actor=nan（C4 爆炸），victim=B
        {"tick": 200, "round": 1, "attacker_steamid": float("nan"), "user_steamid": "222",
         "weapon": "planted_c4", "headshot": False},
    ]
    stats = compute_basic_stats(deaths, [], ["111", "222"], [{"round": 1}])
    assert stats["222"]["deaths"] == 2, "环境杀受害者死亡必须计入"
    assert stats["111"]["kills"] == 1
    assert stats["111"]["deaths"] == 0


def test_unknown_actor_kill_does_not_count_kill():
    """actor 不在玩家列表（旁观者/无效）：不计 kills 但计 victim 死亡。"""
    deaths = [
        {"tick": 100, "round": 1, "attacker_steamid": "999", "user_steamid": "222",
         "weapon": "world", "headshot": False},
    ]
    stats = compute_basic_stats(deaths, [], ["111", "222"], [{"round": 1}])
    assert stats["222"]["deaths"] == 1
    assert stats["111"]["kills"] == 0
    assert sum(s["kills"] for s in stats.values()) == 0
