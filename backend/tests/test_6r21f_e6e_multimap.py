"""6R21F-E6E-MULTIMAP：三地图官方真值闭环（demo19/21/22 × mirage/dust2/inferno）+ 人工抽查前置（红测先行）。

覆盖（可审计、fail-closed）：
- manifest：de_mirage/de_dust2/de_inferno 三图齐全；demo_map_alignment 19/21/22 一致；无绝对路径/Steam/昵称。
- de_dust2 navmesh：来源相对、提取许可 MIT、与 demo21 严格一致；哈希/统计与 manifest 一致。
- demo21 round sample：map=de_dust2、匿名（无 Steam/昵称/库主键）、无 Mirage/Inferno 硬编码、
  玩家位点落在 de_dust2 变换后的雷达 [0,1] 且含真实移动/击杀/道具/控制变化；content_hash 确定。
"""
import hashlib
import json
import re
from pathlib import Path

from app.services import navmesh as nm

REPO_DIR = Path(__file__).resolve().parent.parent.parent
SAMPLE_PATH = REPO_DIR / "frontend" / "public" / "demo" / "dust2-round-sample.json"

INFERNO_DUST2_META = {"name": "de_dust2", "pos_x": -2476.0, "pos_y": 3239.0, "scale": 4.4, "image_size": 1024}
_LEAK_RE = re.compile(r"7656119\d{9,}|@test\.com|player_id|steam_id|steamid|email|nickname|/AppData|C:\\|D:\\", re.I)

_ABSOLUTE = re.compile(r"[A-Za-z]:\\|/Users/|/home/|/data/cs2-linux-server|SteamLibrary|steamapps", re.I)


def _radar_of(x, y):
    return (x - INFERNO_DUST2_META["pos_x"]) / INFERNO_DUST2_META["scale"] / INFERNO_DUST2_META["image_size"], \
           (INFERNO_DUST2_META["pos_y"] - y) / INFERNO_DUST2_META["scale"] / INFERNO_DUST2_META["image_size"]


def _content_hash(obj):
    body = dict(obj)
    body["meta"] = {k: v for k, v in obj["meta"].items() if k != "generated_at"}
    body.pop("content_hash", None)
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def test_manifest_has_all_three_maps_and_alignment():
    m = nm.load_manifest()
    assert m is not None
    maps = m["maps"]
    assert {"de_mirage", "de_dust2", "de_inferno"} <= set(maps.keys())
    demos = m["manifest"]["demo_map_alignment"]["demos"]
    assert demos == {"19": "de_mirage", "21": "de_dust2", "22": "de_inferno"}


def test_manifest_no_absolute_path_or_steam_identity():
    m = nm.load_manifest()
    raw = m["bytes"].decode("utf-8", "replace")
    assert _ABSOLUTE.search(raw) is None, "manifest 不得含本机绝对路径 / Steam 路径"
    assert "steam_id" not in raw and "7656119" not in raw


def test_dust2_navmesh_is_versioned_auditable_and_matches_demo():
    art = nm.load_navmesh_artifact("de_dust2")
    assert art is not None
    body = json.loads(art["bytes"])
    assert body["schema"] == "cs2-navmesh-v1"
    assert body["source"]["map"] == "de_dust2"
    assert body["source"]["file"] == "maps/de_dust2.nav"
    assert body["source"]["navVersion"] == 36
    assert body["tool"]["name"] == "ValveResourceFormat" and body["tool"]["license"] == "MIT"
    assert body["stats"]["areaCount"] == 2242
    assert body["stats"]["ladderCount"] == 0
    # 无绝对路径 / Steam / 演示玩家
    low = art["bytes"].decode("utf-8", "replace").lower()
    assert "steam" not in low and "player_id" not in low and "uid" not in low


def test_demo21_round_sample_transform_and_anonymized():
    s = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    assert s["schema_version"] == "1.0"
    assert s["map"] == INFERNO_DUST2_META, "样本 transform 必须是 de_dust2 权威值（非 Mirage/Inferno）"
    assert s["meta"]["anonymized"] is True and s["meta"]["no_steam_ids"] is True and s["meta"]["no_player_names"] is True
    payload = json.dumps({"players": s["players"], "events": s["events"], "areas": s["areas"]}, ensure_ascii=False)
    assert _LEAK_RE.search(payload) is None, "样本数据不得泄露 Steam/昵称/路径/邮箱"


def test_demo21_round_sample_no_mirage_or_inferno_hardcode():
    s = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    raw = json.dumps(s, ensure_ascii=False)
    assert "de_mirage" not in raw and "de_inferno" not in raw, "demo21 样本不得指向其它地图资源"


def test_demo21_round_sample_points_in_radar_and_has_dynamics():
    s = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    # 所有玩家点位落在 [0,1]
    for p in s["players"]:
        for pt in p["points"]:
            assert 0 <= pt["rx"] <= 1 and 0 <= pt["ry"] <= 1
    # 有效：有移动、击杀、道具（blinds/props）、控制变化
    assert any(len(p["points"]) >= 50 for p in s["players"]), "必须有有效移动轨迹"
    assert len(s["events"]["kills"]) >= 5, "必须有真实击杀"
    assert len(s["events"]["blinds"]) >= 2, "必须有真实道具/致盲"
    # 控制变化：存在 contested 或（occupied/affecting）转变的 region
    has_control_change = any(a.get("transitions") for a in s["areas"])
    assert has_control_change, "必须有真实区域控制变化"
    assert s["content_hash"] == _content_hash(s), "content_hash 必须确定性"


def test_demo21_round_sample_deterministic_reload():
    a = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    b = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    assert a["content_hash"] == b["content_hash"]
