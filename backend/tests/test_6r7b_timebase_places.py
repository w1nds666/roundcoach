"""6R7B gate5：权威时基（CDemoFileInfo）+ 5 demo 地点覆盖 定向回归测试。

- tick_rate 必须来自 CDemoFileInfo（playback_ticks/playback_time），且为权威值（本批 demo=64）。
- 地点词典必须覆盖 5 个验收 demo 实际出现的 last_place_name（覆盖=不落到"未知区域"）。
"""
import os
from pathlib import Path

import pytest

from app.services.parser import DemoAnalyzer, read_cdemo_file_info
from app.services.places import translate_place

ROOT = Path(__file__).resolve().parents[2]

DEMOS = {
    19: (ROOT / "test demo" / "4614393950359818651_0" / "4614393950359818651_10000502.dem", "de_mirage"),
    20: (ROOT / "test demo" / "4615096941117908123_0" / "4615096941117908123_10000485.dem", "de_anubis"),
    21: (ROOT / "test demo" / "4626848676756337691_0" / "4626848676756337691_10000489.dem", "de_dust2"),
    22: (ROOT / "test demo" / "9209049647259770252_0" / "9209049647259770252_0.dem", "de_inferno"),
    23: (ROOT / "test demo" / "9219534615131736844_0" / "9219534615131736844_0.dem", "de_ancient"),
}

# 各图实际出现的 last_place_name（demoparser2 实测；用作覆盖验证的真实语料）
REAL_PLACES = {
    "de_mirage": ["CTSpawn", "TSpawn", "BombsiteA", "BombsiteB", "PalaceInterior", "Catwalk",
                  "Connector", "TopofMid", "Middle", "TRamp", "Underpass", "PalaceAlley", "Shop",
                  "BackAlley", "SideAlley", "Apartments", "SnipersNest", "Stairs", "Jungle", "House",
                  "Truck", "Ladder", "Scaffolding"],
    "de_anubis": ["TSpawn", "CTSideUpper", "Bridge", "BombsiteB", "Middle", "OutsideLong", "Canal",
                  "Ruins", "PalaceInterior", "Connector", "Street", "Main", "BombsiteA", "Walkway",
                  "BackofB", "TStairs", "Bricks", "TSideUpper", "Alley", "Fountain", "Heaven",
                  "LowerTunnel", "SnipersNest", "TunnelStairs", "Tunnel", "CTSpawn"],
    "de_dust2": ["CTSpawn", "TSpawn", "BombsiteB", "MidDoors", "LongDoors", "LongA", "TopofMid",
                 "UpperTunnel", "UnderA", "OutsideLong", "Catwalk", "ShortStairs", "LowerTunnel",
                 "Middle", "BDoors", "BombsiteA", "ExtendedA", "ARamp", "TunnelStairs", "OutsideTunnel",
                 "Pit", "Hole", "Side", "TRamp"],
    "de_inferno": ["Banana", "BombsiteB", "CTSpawn", "TSpawn", "Apartments", "BombsiteA", "Middle",
                   "TopofMid", "Arch", "Ruins", "TRamp", "SecondMid", "Pit", "LowerMid", "Balcony",
                   "Library", "BackAlley", "Underpass", "Graveyard", "Quad", "Upstairs", "Bridge"],
    "de_ancient": ["SideEntrance", "Middle", "BombsiteB", "TSpawn", "BombsiteA", "TSideUpper", "Alley",
                   "Outside", "MainHall", "SideHall", "House", "CTSpawn", "Ruins", "TSideLower", "Ramp",
                   "Tunnel", "TopofMid", "Water"],
}


@pytest.mark.parametrize("demo_id", [19, 20, 21, 22, 23])
def test_tick_rate_authoritative_from_cdemo_file_info(demo_id):
    """tick_rate 必须由 CDemoFileInfo 推导（=playback_ticks/playback_time），非硬编码 128。"""
    path, expect_map = DEMOS[demo_id]
    if not path.exists():
        pytest.skip(f"demo 文件不存在: {path}")
    info = read_cdemo_file_info(path)
    assert info is not None, "无法读取 CDemoFileInfo（权威时基缺失）"
    assert info["playback_ticks"] and info["playback_time"]
    derived = round(info["playback_ticks"] / info["playback_time"], 2)
    assert derived == 64.0, f"demo {demo_id} 权威 tick_rate={derived}，应为 64"

    header = DemoAnalyzer(str(path)).parse_header()
    assert header.map_name == expect_map
    assert header.tick_rate == derived
    assert header.duration_seconds is not None and header.duration_seconds > 0


@pytest.mark.parametrize("demo_id", [19, 20, 21, 22, 23])
def test_place_dict_covers_real_places(demo_id):
    """地点词典必须覆盖该 demo 实际出现的 last_place_name（不落到"未知区域"）。"""
    _, map_name = DEMOS[demo_id]
    unmapped = []
    for place in REAL_PLACES[map_name]:
        if translate_place(map_name, place) == "未知区域":
            unmapped.append(place)
    assert not unmapped, f"demo {demo_id} ({map_name}) 仍有未覆盖地点: {unmapped}"


def test_dust2_community_naming():
    """Dust2 社区叫 'A 大'（非 'A 长'）。"""
    assert translate_place("de_dust2", "LongA") == "A 大"
    assert translate_place("de_dust2", "Long_A") == "A 大"


def test_cross_map_isolation_of_places():
    """跨图同名键不互相泄漏。"""
    assert translate_place("de_inferno", "Pit") == "B 坑"
    assert translate_place("de_dust2", "Pit") == "A 坑"
    assert translate_place("de_mirage", "Catwalk") == "猫道"
    assert translate_place("de_ancient", "Ramp") == "坡道"
