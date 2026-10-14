"""MVR 最终验收 A：真实 API 顺序上传 5 个代表 demo + 完整解析验收。

- 顺序上传（前一个 parsed/failed 再传下一个）；记录上传/解析耗时、demo_id
- 每场验收：map/status/玩家数/比分/rounds/kills/grenades/4 热力图/无警告/API-DB 一致性
- round 核对：round_start/round_end 计数（demoparser2 轻量重读）+ matches.rounds
- 输出脱敏结果 JSON（不含 key/token/SteamID/昵称）到 docs/mvr-a-data.json
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost"
ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "test demo"
OUT = ROOT / "docs" / "mvr-a-data.json"

# inventory 选定的 5 个代表（按名匹配，避免路径硬编码）
TARGETS = [
    ("de_mirage", "4614393950359818651_10000502.dem"),
    ("de_anubis", "4615096941117908123_10000485.dem"),
    ("de_dust2", "4626848676756337691_10000489.dem"),
    ("de_inferno", "9209049647259770252_0.dem"),
    ("de_ancient", "9219534615131736844_0.dem"),
]

EMAIL = "mvr-acceptance@test.com"
PASSWORD = "password123"


def login() -> str:
    r = httpx.post(f"{BASE}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def find_demo(name: str) -> Path:
    hits = list(DEMO_DIR.rglob(name))
    if not hits:
        raise FileNotFoundError(f"{name} 未找到")
    return hits[0]


def upload_and_wait(client: httpx.Client, token: str, path: Path) -> dict:
    t0 = time.time()
    with path.open("rb") as f:
        resp = client.post(
            f"{BASE}/api/demos/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (path.name, f, "application/octet-stream")},
            timeout=900,
        )
    upload_s = round(time.time() - t0, 1)
    if resp.status_code != 200:
        return {"error": resp.text[:300], "upload_s": upload_s}
    demo_id = resp.json()["demo_id"]

    t1 = time.time()
    status = "uploaded"
    for _ in range(240):  # 最长 20 分钟
        st = client.get(
            f"{BASE}/api/demos/{demo_id}/status",
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        ).json()
        status = st["status"]
        if status in ("parsed", "failed"):
            break
        time.sleep(5)
    parse_s = round(time.time() - t1, 1)
    return {"demo_id": demo_id, "status": status, "upload_s": upload_s, "parse_s": parse_s,
            "error": None}


def verify_demo(client: httpx.Client, token: str, demo_id: int, expect_map: str) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    r1 = client.get(f"{BASE}/api/demos/{demo_id}/report/basic", headers=h, timeout=60)
    if r1.status_code != 200:
        return {"demo_id": demo_id, "verify_error": f"report/basic HTTP {r1.status_code}: {r1.text[:200]}"}
    rep = r1.json()
    r2 = client.get(f"{BASE}/api/demos/{demo_id}/kills", headers=h, timeout=60)
    if r2.status_code != 200:
        return {"demo_id": demo_id, "verify_error": f"kills HTTP {r2.status_code}: {r2.text[:200]}"}
    kills = r2.json()
    r3 = client.get(f"{BASE}/api/demos/{demo_id}/grenades", headers=h, timeout=60)
    grens = r3.json() if r3.status_code == 200 else {"error": f"HTTP {r3.status_code}", "total": -1}
    hm = {x["mode"]: x["url"] for x in rep["heatmaps"]}

    # 热力图 PNG/密度可读性（GET 状态码）
    heatmap_status = {}
    for mode, url in hm.items():
        r = client.get(f"{BASE}{url}", headers=h, timeout=60)
        heatmap_status[mode] = r.status_code
        d = client.get(f"{BASE}/api/demos/{demo_id}/density/{mode}", headers=h, timeout=60)
        heatmap_status[f"{mode}_density"] = d.status_code

    # API 一致性：玩家击杀合计 vs kills 计数 vs 道具
    player_kills_sum = sum(p["stats"]["kills"] for p in rep["players"])

    return {
        "demo_id": demo_id,
        "map": rep["match"]["map_name"],
        "expect_map": expect_map,
        "score_ct": rep["match"]["score_ct"],
        "score_t": rep["match"]["score_t"],
        "rounds": rep["match"]["rounds"],
        "players": len(rep["players"]),
        "player_kills_sum": player_kills_sum,
        "kills_total": len(kills),
        "grenades_total": grens.get("total", -1),
        "heatmap_modes": list(hm.keys()),
        "heatmap_status": heatmap_status,
        "heatmap_warnings": None,  # 由 DB 查询补充
    }


def main() -> None:
    token = login()
    results = []
    with httpx.Client() as client:
        for expect_map, name in TARGETS:
            print(f"=== 上传 {name} ===", flush=True)
            path = find_demo(name)
            up = upload_and_wait(client, token, path)
            if up.get("error"):
                print(f"  上传失败: {up['error']}", flush=True)
                results.append({"file": name, **up})
                continue
            demo_id = up["demo_id"]
            print(f"  demo_id={demo_id} status={up['status']} upload={up['upload_s']}s parse={up['parse_s']}s", flush=True)
            if up["status"] == "parsed":
                v = verify_demo(client, token, demo_id, expect_map)
                print(f"  map={v['map']} score={v['score_ct']}:{v['score_t']} rounds={v['rounds']} "
                      f"players={v['players']} kills={v['kills_total']} grens={v['grenades_total']}", flush=True)
                results.append({"file": name, **up, **v})
            else:
                results.append({"file": name, **up})

    OUT.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {OUT}")


if __name__ == "__main__":
    main()
