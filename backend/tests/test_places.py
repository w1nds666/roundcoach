"""6R7B gate3：中文叫位词典回归测试（跨图隔离 + Dust2 A大 + inferno Pit 纠错）。"""
from app.services.places import PLACE_DICT_VERSION, map_place_catalog, place_coverage, translate_place


def test_dust2_longA_is_A_da_not_A_chang():
    """Dust2 社区叫 'A 大'，不是 'A 长'（用户明确要求）。"""
    assert translate_place("de_dust2", "LongA") == "A 大"
    assert translate_place("de_dust2", "Long_A") == "A 大"
    assert translate_place("de_dust2", "Long") == "A 大"
    assert translate_place("de_dust2", "Long_A_Doors") == "A 大门"


def test_cross_map_leak_prevented():
    """先按图查 → 各图同名键不被他图覆盖（inferno Pit=B坑、mirage Catwalk=猫道、ancient Ramp=坡道）。"""
    assert translate_place("de_inferno", "Pit") == "B 坑"       # 不受 dust2 A 坑影响
    assert translate_place("de_dust2", "Pit") == "A 坑"
    assert translate_place("de_mirage", "Catwalk") == "猫道"     # 不受 dust2 天桥影响
    assert translate_place("de_dust2", "Catwalk") == "天桥"
    assert translate_place("de_ancient", "Ramp") == "坡道"       # 不受 mirage A 坡影响
    assert translate_place("de_ancient", "Water") == "水区"
    assert translate_place("de_anubis", "UpperTunnel") == "未知区域"  # anubis 无 UpperTunnel 键


def test_common_global_callouts():
    """全局通用叫位在各图均生效。"""
    for m in ("de_dust2", "de_mirage", "de_anubis", "de_inferno", "de_ancient"):
        assert translate_place(m, "BombsiteA") == "A 区"
        assert translate_place(m, "BombsiteB") == "B 区"
        assert translate_place(m, "CTSpawn") == "CT 出生点"
        assert translate_place(m, "TSpawn") == "T 出生点"
        assert translate_place(m, "Middle") == "中路"


def test_unmapped_returns_unknown():
    """未映射 → '未知区域'，绝不回退坐标。"""
    assert translate_place("de_mirage", "NonexistentPlace") == "未知区域"
    assert translate_place("de_mirage", None) == "未知区域"
    assert translate_place("de_mirage", "") == "未知区域"


def test_place_coverage_counts_and_unmapped():
    """覆盖量化：mirage 上 5 个真实（含可映射/不可映射）样本。"""
    seen = ["CTSpawn", "PalaceInterior", "BombsiteA", "Nonexistent1", "Nonexistent2", None]
    rep = place_coverage("de_mirage", seen)
    assert rep["version"] == PLACE_DICT_VERSION
    assert rep["map"] == "de_mirage"
    assert rep["seen"] == 5                     # 排除 None
    assert rep["mapped"] == 3                   # CTSpawn/PalaceInterior/BombsiteA
    assert rep["unmapped"] == sorted(["Nonexistent1", "Nonexistent2"])


def test_map_catalog_has_common_and_map_specific():
    cat = map_place_catalog("de_dust2")
    assert cat["BombsiteA"] == "A 区"
    assert cat["LongA"] == "A 大"
    assert cat["Pit"] == "A 坑"
