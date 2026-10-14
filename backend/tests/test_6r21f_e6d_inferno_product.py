"""6R21F-E6D-INFERNO-PRODUCT-INTEGRATION：demo22 × de_inferno 产品接入门禁（红测先行）。

目标：把 demo22 × de_inferno 的版本化、匿名化真实回放样本接入产品 MapControlReplay，
按权威 mapName 选择 backend /api/maps/de_inferno/navmesh 与对应 transform；不复用 Mirage 坐标/样本/mask。

覆盖（可审计、fail-closed）：
- demo→地图选择：demo22 权威 map=de_inferno（三源一致）；de_inferno navmesh source.map 严格一致。
- transform 映射：样本 map.pos_x/pos_y/scale/image_size == maps.json de_inferno 权威值（非 Mirage）。
- 无地图外坐标：所有玩家/事件点位经 transform 后落在 de_inferno NavMesh 的世界边界内（radar [0,1] 且落在 navmesh bbox 内）。
- 无 Mirage 硬编码：样本 map.name==de_inferno，radar/sample URL 路径不含 mirage。
- 匿名化：样本不含 steam_id64 / player_name / 库主键 / 邮箱 / 昵称 / 本机路径。
- seek/确定性：content_hash 重算一致（同输入→同 hash），重复加载 hash 不变。
"""
import hashlib
import json
import re
from pathlib import Path

from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
SAMPLE_PATH = REPO_DIR / "frontend" / "public" / "demo" / "6r22-round-sample.json"

# de_inferno 权威 transform（maps.json），与 backend/config/maps.json 一致。
INFERNO_META = {"name": "de_inferno", "pos_x": -2087.0, "pos_y": 3870.0, "scale": 4.9, "image_size": 1024}
# de_inferno NavMesh 世界边界（config/navmesh/manifest.json/tool 摘要）：x[-1727.5,2661.5] y[-762.5,3511]
NAV_BOUNDS = {"xmin": -1727.5, "xmax": 2661.5, "ymin": -762.5, "ymax": 3511.0}

_LEAK_RE = re.compile(r"7656119\d{9,}|@test\.com|player_id|steam_id|steamid|email|nickname|/AppData|C:\\|D:\\", re.I)


def _radar_of(x, y):
    return (x - INFERNO_META["pos_x"]) / INFERNO_META["scale"] / INFERNO_META["image_size"], \
           (INFERNO_META["pos_y"] - y) / INFERNO_META["scale"] / INFERNO_META["image_size"]


def _navmesh_radar_bbox():
    xlo, ylo = _radar_of(NAV_BOUNDS["xmin"], NAV_BOUNDS["ymax"])
    xhi, yhi = _radar_of(NAV_BOUNDS["xmax"], NAV_BOUNDS["ymin"])
    return xlo, ylo, xhi, yhi


def load_sample():
    assert SAMPLE_PATH.is_file(), f"demo22 样本必须存在: {SAMPLE_PATH}"
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1) demo→地图选择 + de_inferno navmesh 服务
# ---------------------------------------------------------------------------

def _import_navmesh():
    from app.services import navmesh as nm
    return nm


def test_demo22_authoritative_map_is_inferno():
    nm = _import_navmesh()
    m = nm.load_manifest()
    assert m is not None
    assert m["manifest"]["demo_map_alignment"]["demos"].get("22") == "de_inferno", \
        "demo22 权威 map_name 必须是 de_inferno（三源一致）"


def test_de_inferno_navmesh_source_map_strict():
    nm = _import_navmesh()
    art = nm.load_navmesh_artifact("de_inferno")
    assert art is not None
    body = json.loads(art["bytes"])
    assert body["schema"] == "cs2-navmesh-v1"
    assert body["source"]["map"] == "de_inferno"
    assert body["stats"]["areaCount"] == 2738
    assert body["stats"]["ladderCount"] == 0


# ---------------------------------------------------------------------------
# 2) transform 映射 + 无 Mirage 硬编码
# ---------------------------------------------------------------------------

def test_sample_transform_is_de_inferno_not_mirage():
    s = load_sample()
    assert s["map"] == INFERNO_META, "样本 transform 必须是 de_inferno 权威值（非 Mirage）"
    assert s["meta"]["map"] == "de_inferno"
    assert s["map"]["name"] != "de_mirage", "不允许 Mirage 硬编码服务 demo22"
    # provenance 必须说明 de_inferno 权威 transform（非 Mirage），来源必须匿名。
    assert "de_inferno" in s["meta"]["provenance"], "provenance 应注明 de_inferno 权威 transform"
    assert "匿名" in s["meta"]["provenance"]


def test_sample_radar_sample_urls_not_mirage():
    s = load_sample()
    assert s["map"]["name"] == "de_inferno"
    # 样本内任何 URL/资源引用不得指向 mirage（无 Mirage 硬编码 mask/坐标）。
    raw = json.dumps(s, ensure_ascii=False)
    assert "de_mirage" not in raw, "demo22 样本不得引用 de_mirage 资源"


# ---------------------------------------------------------------------------
# 3) 无地图外坐标（玩家/事件点位落于 de_inferno NavMesh 边界内）
# ---------------------------------------------------------------------------

def test_all_player_points_in_navmesh_bbox():
    s = load_sample()
    xlo, ylo, xhi, yhi = _navmesh_radar_bbox()
    assert 0 <= xlo < xhi <= 1 and 0 <= ylo < yhi <= 1, "navmesh bbox 必须映射进 [0,1] radar 画布"
    for p in s["players"]:
        for pt in p["points"]:
            assert 0 <= pt["rx"] <= 1 and 0 <= pt["ry"] <= 1, "点位必须在 radar [0,1]"
            assert xlo - 1e-6 <= pt["rx"] <= xhi + 1e-6, f"{p['uid']} 点位 rx={pt['rx']} 超出 navmesh bbox"
            assert ylo - 1e-6 <= pt["ry"] <= yhi + 1e-6, f"{p['uid']} 点位 ry={pt['ry']} 超出 navmesh bbox"


def test_area_geometry_in_navmesh_bbox():
    s = load_sample()
    xlo, ylo, xhi, yhi = _navmesh_radar_bbox()
    for a in s["areas"]:
        c = a["centroid"]
        assert xlo - 1e-6 <= c["rx"] <= xhi + 1e-6, f"{a['place_key']} 质心 rx 超出"
        assert ylo - 1e-6 <= c["ry"] <= yhi + 1e-6, f"{a['place_key']} 质心 ry 超出"


# ---------------------------------------------------------------------------
# 4) 匿名化（无 Steam / 昵称 / 路径 / 库主键）
# ---------------------------------------------------------------------------

def test_sample_anonymized_no_leak():
    s = load_sample()
    assert s["meta"].get("anonymized") is True
    assert s["meta"].get("no_steam_ids") is True
    assert s["meta"].get("no_player_names") is True
    # 只扫描数据载荷（players/events/areas），不含 meta 的 no_steam_ids/no_player_names 诚实标记字段名。
    payload = json.dumps({
        "players": s["players"], "events": s["events"], "areas": s["areas"],
    }, ensure_ascii=False)
    assert _LEAK_RE.search(payload) is None, "样本数据不得泄露 steam_id / 玩家名 / 库主键 / 路径 / 邮箱"


def test_player_uids_are_anonymous():
    s = load_sample()
    uids = [p["uid"] for p in s["players"]]
    assert len(uids) == 10
    assert all(re.fullmatch(r"(T|CT)[1-5]", u) for u in uids), f"玩家 uid 必须为匿名 T1..T5/CT1..CT5: {uids}"


# ---------------------------------------------------------------------------
# 5) winner 诚实反映实际（R2 胜方 B —— 本场实际最终胜方 13:11）
# ---------------------------------------------------------------------------

def test_sample_round_winner_reflects_actual_victory():
    s = load_sample()
    assert s["round"]["winner_team"] == "B"
    assert s["meta"]["winner_team"] == "B"
    assert s["round"]["max_duration"] > 0


# ---------------------------------------------------------------------------
# 6) seek / 确定性：content_hash 重算一致（同输入→同 hash）
# ---------------------------------------------------------------------------

def _content_hash(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def test_sample_content_hash_deterministic():
    s = load_sample()
    body = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))  # 重读，验证重复加载一致
    assert body["content_hash"] == s["content_hash"]
    # 重算（剔除 generated_at 与 hash 自身），必须等于样本内 hash
    recompute = dict(s)
    recompute["meta"] = {k: v for k, v in s["meta"].items() if k != "generated_at"}
    recompute.pop("content_hash", None)
    assert _content_hash(recompute) == s["content_hash"], "content_hash 必须确定性（同输入→同 hash）"


def test_sample_generated_at_not_part_of_hash():
    s = load_sample()
    assert "generated_at" in s["meta"]
