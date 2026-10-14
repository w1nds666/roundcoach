"""6R7B：版本化中文社区叫位词典（人类可读地点）。

- 主 UI 不显示世界坐标；用 demoparser2 `last_place_name`（英文内部 place 名）映射到中文。
- 词典键以 5 个验收 demo 的**真实出现** `last_place_name` 为准（见 place_coverage / 实测），
  不凭空假设；未映射 → "未知区域"，绝不回退坐标。
- 版本化（v2）：修正了跨图同名键被全局缓存抢先命中的 bug（改为"先按图查、再全局兜底"），
  并依据 Dust2 社区叫法将 `LongA` 修正为 "A 大"、inferno `Pit` 修正为 "B 坑"。
"""
from __future__ import annotations

PLACE_DICT_VERSION = "2"

# 通用叫位（跨图一致，全局兜底）
_COMMON = {
    "BombsiteA": "A 区", "BombsiteB": "B 区", "A_Site": "A 区", "B_Site": "B 区",
    "CTSpawn": "CT 出生点", "TSpawn": "T 出生点", "CT_Spawn": "CT 出生点", "T_Spawn": "T 出生点",
    "Middle": "中路", "TopofMid": "顶中路", "Mid": "中路", "MidDoors": "中门",
    "Mid_Doors": "中门", "Center": "中路", "Unknown": "未知区域", "Default": "未知区域",
}

# 各图细分叫位（键 = 5 demo 实际出现的 last_place_name，社区中文叫法；覆盖率在报告中量化）
_MAPS: dict[str, dict[str, str]] = {
    "de_dust2": {
        "LongA": "A 大", "Long_A": "A 大", "Long": "A 大",          # 社区叫"A 大"，非"A 长"
        "LongDoors": "A 大门", "Long_A_Doors": "A 大门",
        "UpperTunnel": "B 洞上", "LowerTunnel": "B 洞下", "B_Tunnels": "B 洞",
        "UnderA": "A 大下", "OutsideLong": "A 长外", "ShortStairs": "A 小楼梯",
        "ExtendedA": "A 区外", "OutsideTunnel": "B 洞外", "Hole": "洞", "Side": "侧路",
        "Catwalk": "天桥", "BDoors": "B 门", "ARamp": "A 坡", "Pit": "A 坑",
        "TunnelStairs": "隧道楼梯", "TRamp": "T 坡", "Tunnel": "隧道", "Xbox": "Xbox",
    },
    "de_mirage": {
        "PalaceInterior": "王宫", "Palace": "王宫", "Catwalk": "猫道",
        "Connector": "连接", "TRamp": "T 坡", "Underpass": "涵洞",
        "PalaceAlley": "王宫小巷", "Shop": "商店", "BackAlley": "后巷", "SideAlley": "小巷",
        "Apartments": "公寓", "Apps": "公寓", "B_Apts": "B 公寓", "B_Apps": "B 公寓",
        "SnipersNest": "狙击位", "Stairs": "楼梯", "Jungle": "丛林", "House": "房子",
        "Truck": "卡车", "Ladder": "梯子", "Scaffolding": "脚手架",
        "A_Ramp": "A 坡", "Ramp": "A 坡", "Window": "窗口", "Balcony": "阳台",
        "Kitchen": "厨房", "New_Short": "新短廊", "Short": "短廊",
    },
    "de_anubis": {
        "CTSideUpper": "CT 侧上", "TSideUpper": "T 侧上", "Bridge": "桥", "Canal": "运河",
        "OutsideLong": "A 长外", "PalaceInterior": "王宫", "Connector": "连接", "Street": "街道",
        "Walkway": "走道", "BackofB": "B 后方", "TStairs": "T 楼梯", "Bricks": "砖区",
        "Alley": "小巷", "Fountain": "喷泉", "Heaven": "高处", "LowerTunnel": "B 洞下",
        "SnipersNest": "狙击位", "TunnelStairs": "隧道楼梯", "Tunnel": "隧道", "Ruins": "遗迹",
        "Main": "主线", "Water": "水区",
    },
    "de_inferno": {
        "Banana": "香蕉道", "Apartments": "公寓", "Apts": "公寓", "Arch": "拱门",
        "Ruins": "遗迹", "TRamp": "T 坡", "SecondMid": "侧中路", "Pit": "B 坑",
        "LowerMid": "下中路", "Balcony": "阳台", "Library": "图书馆", "BackAlley": "后巷",
        "Underpass": "涵洞", "Graveyard": "墓地", "Quad": "四方院", "Upstairs": "楼上",
        "Bridge": "桥", "T_Apts": "T 公寓", "Car": "汽车", "Altar": "祭坛", "Short": "短廊",
    },
    "de_ancient": {
        "SideEntrance": "侧入口", "TSideUpper": "T 侧上", "TSideLower": "T 侧下",
        "Outside": "外场", "MainHall": "主厅", "SideHall": "侧厅", "House": "房子",
        "Alley": "小巷", "Ramp": "坡道", "Tunnel": "隧道", "Water": "水区", "Ruins": "遗迹",
        "Cave": "洞穴", "Donut": "甜甜圈", "Main": "主线", "A_Main": "A 主线", "B_Main": "B 主线",
        "Canal": "运河", "Vent": "通风口", "Jungle": "丛林",
    },
}


def _norm(s: str) -> str:
    """归一化：去下划线/空格/连字符 + 小写，使 'CT_Spawn'↔'CTSpawn' 命中。"""
    return "".join(ch for ch in s.lower() if ch.isalnum())


# 全局兜底（仅 _COMMON；避免跨图同名键先到先得导致各图覆盖失效）
_NORM_COMMON: dict[str, str] = {_norm(k): v for k, v in _COMMON.items()}
# 各图归一化键
_NORM_MAP: dict[str, dict[str, str]] = {
    map_name: {_norm(k): v for k, v in entries.items()}
    for map_name, entries in _MAPS.items()
}


def translate_place(map_name: str, place: str | None) -> str:
    """last_place_name → 中文叫位；未映射返回 '未知区域'，绝不回退坐标。

    先按图查（避免跨图同名键泄漏），再用全局通用叫位兜底。
    """
    if not place:
        return "未知区域"
    key = _norm(place)
    # 图级词典优先（inferno Pit=B 坑、dust2 LongA=A 大，不受他图同名覆盖）
    per_map = _NORM_MAP.get(map_name)
    if per_map and key in per_map:
        return per_map[key]
    if key in _NORM_COMMON:
        return _NORM_COMMON[key]
    return "未知区域"


def map_place_catalog(map_name: str) -> dict[str, str]:
    """该图全部可映射叫位（通用 + 图级）。"""
    return {**_COMMON, **_MAPS.get(map_name, {})}


def place_coverage(map_name: str, seen_places: list[str | None]) -> dict:
    """覆盖率报告：已见 place 中可映射占比 + 未映射样本。"""
    total = len([p for p in seen_places if p])
    mapped = sum(1 for p in seen_places if p and translate_place(map_name, p) != "未知区域")
    unmapped = [p for p in seen_places if p and translate_place(map_name, p) == "未知区域"]
    return {
        "version": PLACE_DICT_VERSION,
        "map": map_name,
        "seen": total,
        "mapped": mapped,
        "coverage": round(mapped / total, 3) if total else 0.0,
        "unmapped": sorted(set(unmapped))[:40],
    }
