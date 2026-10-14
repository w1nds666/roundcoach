"""Phase 6A/6R1：用成品化热力图参数重新生成指定 demo 的 4 模式热力图。

- 只重新生成热力图（PNG + density JSON），覆盖 MinIO 同 key 对象；不重建其他数据
- 用法：python scripts/regen_heatmaps.py [demo_id...]（默认 19-23）
- 输入为 test demo 本地原文件（与上传内容一致）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.heatmap import PALETTE_FOR_MODE, generate_heatmap_bytes, generate_heatmap_grid  # noqa: E402
from app.services.parser import DemoAnalyzer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost"
EMAIL = "mvr-acceptance@test.com"
PASSWORD = "password123"

# demo_id -> (本地文件相对 test demo 的路径, 期望地图)
TARGETS = {
    19: ("test demo/4614393950359818651_0/4614393950359818651_10000502.dem", "de_mirage"),
    20: ("test demo/4615096941117908123_0/4615096941117908123_10000485.dem", "de_anubis"),
    21: ("test demo/4626848676756337691_0/4626848676756337691_10000489.dem", "de_dust2"),
    22: ("test demo/9209049647259770252_0/9209049647259770252_0.dem", "de_inferno"),
    23: ("test demo/9219534615131736844_0/9219534615131736844_0.dem", "de_ancient"),
    1: ("backend/tests/demos/test_demo.dem", "de_mirage"),   # 历史验收 demo（回归用）
}


def upload_png(key: str, data: bytes, token: str) -> None:
    """直传 MinIO（覆盖同 key）。"""
    import io

    from minio import Minio

    client = Minio("localhost:9000", access_key="cs2minio", secret_key="cs2minio_secret", secure=False)
    client.put_object("cs2demo", key, io.BytesIO(data), length=len(data), content_type="image/png")


def main() -> None:
    targets = sys.argv[1:] or list(TARGETS.keys())
    for demo_id in targets:
        demo_id = int(demo_id)
        item = TARGETS.get(demo_id)
        if item is None:
            print(f"demo {demo_id} 不在已知清单（跳过）", flush=True)
            continue
        rel, expect_map = item
        p = ROOT / rel
        print(f"=== demo {demo_id} {expect_map} ===", flush=True)
        a = DemoAnalyzer(str(p))
        deaths = a.parse_deaths()
        header = a.parse_header()

        # match_id 从 DB 读取（保持 heatmap_cache.match_id 对应）
        token = httpx.post(f"{BASE}/api/auth/login",
                           json={"email": EMAIL, "password": PASSWORD}, timeout=30).json()["access_token"]
        rep = httpx.get(f"{BASE}/api/demos/{demo_id}/report/basic",
                        headers={"Authorization": f"Bearer {token}"}, timeout=60).json()

        match_id = None
        import subprocess

        out = subprocess.run(
            ["docker", "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo", "-t", "-A",
             "-c", f"SELECT match_id FROM demos WHERE id={demo_id};"],
            capture_output=True, text=True,
        )
        match_id = out.stdout.strip()
        print(f"  match_id={match_id} map={header.map_name}", flush=True)
        if header.map_name != expect_map:
            print(f"  WARN map mismatch: {header.map_name} != {expect_map}", flush=True)

        kill_points = [(float(d["attacker_X"]), float(d["attacker_Y"]))
                       for d in deaths if d.get("attacker_X") is not None]
        pos_df = a._parser.parse_ticks(["X", "Y", "CCSPlayerPawn.m_iTeamNum"],
                                       ticks=range(0, header.playout_ticks, 8))
        route_all, route_ct, route_t = [], [], []
        for row in pos_df.to_dict(orient="records"):
            x, y, team = row.get("X"), row.get("Y"), row.get("CCSPlayerPawn.m_iTeamNum")
            if x is None or y is None:
                continue
            route_all.append((float(x), float(y)))
            if team == 2:
                route_ct.append((float(x), float(y)))
            elif team == 3:
                route_t.append((float(x), float(y)))

        for mode, points in [("kills_all", kill_points), ("route_all", route_all),
                             ("route_ct", route_ct), ("route_t", route_t)]:
            palette = PALETTE_FOR_MODE.get(mode, "heat")
            png = generate_heatmap_bytes(points, header.map_name, palette_name=palette)
            key = f"heatmaps/{match_id}/{mode}.png"
            upload_png(key, png, token)
            grid = generate_heatmap_grid(points, header.map_name)
            gkey = f"heatmaps/{match_id}/{mode}.json"
            upload_png(gkey, json.dumps(grid, ensure_ascii=False).encode("utf-8"), token)
            print(f"  {mode}: png={len(png)}B grid_cells={len(grid['data'])}", flush=True)

        # 6R1：同步 heatmap_cache 表（upsert；cache 记录可能因历史清理缺失）
        subprocess.run(
            ["docker", "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo", "-c",
             f"INSERT INTO heatmap_cache (match_id, map_name, mode, heatmap_key) "
             f"SELECT {match_id}, '{header.map_name}', mode, 'heatmaps/{match_id}/' || mode || '.png' "
             f"FROM (VALUES ('kills_all'),('route_all'),('route_ct'),('route_t')) AS m(mode) "
             f"ON CONFLICT DO NOTHING;"],
            capture_output=True, text=True,
        )
    print("done")


if __name__ == "__main__":
    main()
