"""从 2mlml/cs2-radar-images 仓库生成 config/maps.json（计划书 7.2 配置表）。

每个地图的 .txt 元数据包含 pos_x / pos_y / scale（世界坐标→雷达图像素换算系数）。
换算公式（Phase 2 实测验证）：
    radar_x = (world_x - pos_x) / scale
    radar_y = (pos_y - world_y) / scale

用法：python scripts/build_maps_config.py
输出：backend/config/maps.json（仅 de_* 竞技图）
"""
import json
import re
import urllib.request
from pathlib import Path

REPO = "2mlml/cs2-radar-images"
BRANCH = "master"  # 仓库默认分支（实测为 master，main 会 404）
API = "https://api.github.com/repos"
RAW = "https://github.com/{}/raw/{}".format(REPO, BRANCH)

OUT = Path(__file__).resolve().parent.parent / "backend" / "config" / "maps.json"


def api_get(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "dsh-orcas"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_overview_txt(text: str) -> dict:
    """解析 Valve overview 文本格式（key value 带引号与 // 注释）。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("//")[0].strip()
        m = re.search(r'"([^"]+)"\s*"([^"]*)"', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def main() -> None:
    entries = api_get(f"{API}/{REPO}/contents/")
    maps = {}
    sources = {}
    for e in entries:
        name = e["name"]
        if not (name.startswith("de_") and name.endswith(".txt")):
            continue
        map_name = name[:-4]
        meta = api_get(f"{API}/{REPO}/contents/{name}")
        text = __import__("base64").b64decode(meta["content"]).decode("utf-8")
        kv = parse_overview_txt(text)
        if "pos_x" not in kv:
            print(f"WARN: {name} missing pos_x, raw head: {text[:120]!r}")
            continue
        maps[map_name] = {
            "pos_x": float(kv["pos_x"]),
            "pos_y": float(kv["pos_y"]),
            "scale": float(kv["scale"]),
        }
        sources[map_name] = f"{RAW}/{map_name}.png"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "note": "坐标换算系数来源: 2mlml/cs2-radar-images（CS2 游戏内提取）",
        "formula": "radar_x=(world_x-pos_x)/scale; radar_y=(pos_y-world_y)/scale",
        "maps": maps,
        "radar_png_sources": sources,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"generated {OUT} with {len(maps)} maps:")
    for k in sorted(maps):
        print(f"  {k}: {maps[k]}")


if __name__ == "__main__":
    main()
