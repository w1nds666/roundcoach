"""6R16：从 demo19-23 战术 artifact（真实 last_place_name 样本）生成版本化区域几何 artifact。

- 只读 MinIO 的 tactical/{match_id}/3.0/tactical.json.gz + DB（map/meta），按地图聚合真实样本点。
- 经 unified radar transform（maps.json）生成确定性 hull + coverage + content_hash。
- 写入 backend/config/regions/{map}.json（版本化、带 source/version/checksum），供前端区域图层与
  区域控制状态机使用。**绝不凭空画多边形**；样本不足只给中心/虚线 + 低置信。
- 幂等、确定性：同输入 → 同 hash。只写产物，不写库。
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from minio import Minio  # noqa: E402

from app.services.region_geometry import (  # noqa: E402
    REGION_SCHEMA_VERSION, build_regions, evaluate_region_geometry,
)
from app.services.routes import gunzip_json  # noqa: E402
from app.services.heatmap import load_maps_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "backend" / "config" / "regions"
DOCKER = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe"
# demo_id -> match_id
DEMOS = {19: 59, 20: 60, 21: 61, 22: 62, 23: 63}
SCHEMA = "3.0"
IMAGE_SIZE = 1024
# place_key 归一化（去空格/下划线/小写），与 places 插件一致
_NORM_CN = {
    # 中文本地图级叫位将由 places.translate_place 给出；此处仅存档 raw place
}


def _psql_rows(sql: str) -> list[list[str]]:
    out = subprocess.run([DOCKER, "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo",
                          "-t", "-A", "-F", "|", "-c", sql],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [line.split("|") for line in out.stdout.splitlines() if line.strip()]


def _load_artifact(match_id: int) -> dict:
    client = Minio("localhost:9000", access_key="cs2minio", secret_key="cs2minio_secret", secure=False)
    key = f"tactical/{match_id}/{SCHEMA}/tactical.json.gz"
    data = client.get_object("cs2demo", key).read()
    return gunzip_json(data)


def _norm_place(p) -> str:
    return "".join(ch.lower() for ch in str(p or "") if ch.isalnum())


def main():
    maps_cfg = load_maps_config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 聚合：map -> {place_key: {place_cn, points:[]}}
    acc: dict[str, dict[str, dict]] = {}
    for demo_id, match_id in DEMOS.items():
        art = _load_artifact(match_id)
        map_name = art.get("map_name")
        place_cn_map = {}
        try:
            from app.services.places import translate_place
            place_cn_map = {}
        except Exception:  # noqa: BLE001
            place_cn_map = {}
        for r in art.get("rounds", []):
            for pid, pd in r.get("players", {}).items():
                for pt in pd.get("points", []):
                    place = pt.get("place")
                    x, y = pt.get("x"), pt.get("y")
                    if not place or x is None or y is None:
                        continue
                    pk = _norm_place(place)
                    bucket = acc.setdefault(map_name, {}).setdefault(
                        pk, {"place_cn": place, "points": []})
                    bucket["points"].append((float(x), float(y)))
    print(f"聚合完成，地图：{list(acc.keys())}", flush=True)

    summary = {}
    for map_name, by_place in sorted(acc.items()):
        meta = maps_cfg.get(map_name)
        if meta is None:
            print(f"  {map_name}: 无 maps.json meta，跳过", flush=True)
            continue
        # 中文叫位
        from app.services.places import translate_place
        for pk, info in by_place.items():
            info["place_cn"] = translate_place(map_name, info.get("place_cn"))
        regions = build_regions(by_place, map_name, {**meta, "image_size": IMAGE_SIZE})
        ev = evaluate_region_geometry(regions["regions"])
        # 扁平化 artifact：regions 直接为区域清单（含 geometry_trust），避免双层嵌套
        out = {
            "region_schema_version": REGION_SCHEMA_VERSION,
            "map": map_name,
            "generated_from": "tactical artifact v3.0 demo19-23 real last_place_name samples",
            "maps_meta": {"pos_x": meta["pos_x"], "pos_y": meta["pos_y"], "scale": meta["scale"],
                          "image_size": IMAGE_SIZE, "version": maps_cfg.get("version", "?")},
            "regions": regions["regions"],
            "region_meta": {"source": "last_place_name samples", "auto": True,
                            "version": REGION_SCHEMA_VERSION, "checksum": regions["content_hash"]},
            "geometry_trust": ev,
            "content_hash": regions["content_hash"],
        }
        out_file = OUT_DIR / f"{map_name}.json"
        out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        summary[map_name] = {"regions": regions["region_count"], "geometric": regions["geometric_region_count"],
                             "coverage": regions["coverage"], "geometry_trust": ev,
                             "content_hash": regions["content_hash"], "file": str(out_file)}
        print(f"  {map_name}: {regions['region_count']} 区 / {regions['geometric_region_count']} 几何 "
              f"coverage={regions['coverage']} trust={ev.get('trust')} -> {out_file}", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
