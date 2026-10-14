"""6R19 负载/稳定性测试运行器（本机渐进，可停止，带硬停止阈值）。

用法：
  python scripts/load_seed.py                 # 先播种负载用户/demo/share
  python scripts/load_test.py run             # 默认 read 1→5→10→20 + login + share 场景
  python scripts/load_test.py read <c> <sec>  # 单读场景
  python scripts/load_test.py login <c> <sec>
  python scripts/load_test.py soak <minutes>  # 固定并发 soak（默认 30 分钟）
  python scripts/load_test.py upload <n>      # 真实上传（57MB）n 次 + 状态轮询

设计：
- asyncio + httpx（复用项目已有依赖），不引入新依赖。
- 注意：login/register/steam/share-resolve 在应用层已被速率限制（Redis 固定窗口，安全控制）。
  → 主并发阶段只用「未限流读路径」（dashboard/summary/status）测容器真实吞吐；
    login/share 单独以低并发表征「受限流保护的可用 RPS」，并如实记录上限。
- 硬停止：错误率>2% 且 p99>5s 持续 → 提前终止并记录 hard_stop=True。
- 输出 scripts/load_out/<name>.json。
- 安全：只打 localhost:80；不攻击外部；不输出 token/secret。
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("LOAD_BASE_URL", "http://localhost:80")
HERE = Path(__file__).resolve().parent
# 6R19 P0 泄密防护：状态含 synthetic JWT/share token，读/写只落在 gitignored 的 .runtime/ 目录。
STATE_PATH = HERE.parent / ".runtime" / "load_state.json"
OUT_DIR = HERE.parent / ".runtime" / "load_out"
PASSWORD = "loadpass123"
HARD_ERROR_RATE = float(os.environ.get("LOAD_HARD_ERROR_RATE", "0.02"))
HARD_P99_MS = float(os.environ.get("LOAD_HARD_P99_MS", "5000"))
DEMO_FILE = HERE.parent / "backend" / "tests" / "demos" / "test_demo.dem"


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(p * len(sorted_vals))))
    return sorted_vals[idx]


def _load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _auth_owner(state):
    return {"Authorization": f"Bearer {state['owner']['token']}"}


def _auth_user(state):
    return {"Authorization": f"Bearer {state['users'][0]['token']}"}


def _read_specs(state):
    """未限流读路径：dashboard / summary / status（前端主负载）。"""
    d = state["demo_id"]
    return [
        {"name": "dashboard", "method": "GET", "path": "/api/demos", "headers": _auth_user(state)},
        {"name": "summary", "method": "GET", "path": f"/api/demos/{d}/report/basic", "headers": _auth_owner(state)},
        {"name": "status", "method": "GET", "path": f"/api/demos/{d}/status", "headers": _auth_owner(state)},
    ]


def _login_specs(state):
    return [{"name": "login", "method": "POST", "path": "/api/auth/login",
             "body": {"email": state["users"][0]["email"], "password": PASSWORD}}]


def _share_specs(state):
    return [{"name": "share-resolve", "method": "POST", "path": "/api/share/resolve",
             "body": {"token": state["share_token"]}}]


async def _one(client, spec):
    t0 = time.perf_counter()
    try:
        kwargs = {}
        if spec.get("headers"):
            kwargs["headers"] = spec["headers"]
        if spec.get("body") is not None:
            kwargs["json"] = spec["body"]
        r = await client.request(spec["method"], spec["path"], **kwargs)
        status = r.status_code
    except Exception:  # noqa: BLE001
        dt = (time.perf_counter() - t0) * 1000
        return {"name": spec["name"], "ms": dt, "ok": False, "net": True}
    dt = (time.perf_counter() - t0) * 1000
    ok = 200 <= status < 300
    return {"name": spec["name"], "ms": dt, "ok": ok, "status": status, "net": False}


async def _worker(client, specs, duration, stats, stop_flag):
    end = time.monotonic() + duration
    while time.monotonic() < end and not stop_flag.is_set():
        for spec in specs:
            if stop_flag.is_set():
                return
            stats.append(await _one(client, spec))


async def _warmup():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        t0 = time.perf_counter()
        try:
            r = await c.get("/health")
            return (time.perf_counter() - t0) * 1000 if r.status_code == 200 else None
        except Exception:  # noqa: BLE001
            return None


async def _monitor(stats, stop_flag):
    while not stop_flag.is_set():
        await asyncio.sleep(1.0)
        if len(stats) < 30:
            continue
        ok = sum(1 for s in stats if s["ok"])
        errs = len(stats) - ok
        rate = errs / len(stats)
        ms = sorted(s["ms"] for s in stats)
        p99 = _pct(ms, 0.99) or 0
        if rate > HARD_ERROR_RATE and p99 > HARD_P99_MS:
            stop_flag.set()
            return


async def _run_stage(name, specs, conc, duration, note=""):
    stats = []
    stop_flag = asyncio.Event()
    warm = await _warmup()
    clients = [httpx.AsyncClient(base_url=BASE, timeout=60, limits=httpx.Limits(max_connections=conc * 2)) for _ in range(conc)]
    start = time.perf_counter()
    tasks = [asyncio.create_task(_worker(c, specs, duration, stats, stop_flag)) for c in clients]
    monitor = asyncio.create_task(_monitor(stats, stop_flag))
    await asyncio.gather(*tasks)
    monitor.cancel()
    elapsed = time.perf_counter() - start
    for c in clients:
        await c.aclose()
    return _summarize(name, conc, specs, stats, elapsed, warm, stop_flag.is_set(), note)


def _summarize(name, conc, specs, stats, elapsed, warm, hard_stop, note):
    ok = sum(1 for s in stats if s["ok"])
    errs = len(stats) - ok
    rate = errs / len(stats) if stats else 0
    ms = sorted(s["ms"] for s in stats)
    by_name = {}
    for s in stats:
        by_name.setdefault(s["name"], []).append(s["ms"])
    per_endpoint = {}
    for nm, lat in by_name.items():
        sl = sorted(lat)
        per_endpoint[nm] = {"requests": len(lat),
                            "p50_ms": round(_pct(sl, 0.50) or 0, 2),
                            "p95_ms": round(_pct(sl, 0.95) or 0, 2),
                            "p99_ms": round(_pct(sl, 0.99) or 0, 2)}
    rps = len(stats) / elapsed if elapsed else 0
    return {"scenario": name, "concurrency": conc, "duration_s": round(elapsed, 2), "note": note,
            "requests": len(stats), "rps": round(rps, 2),
            "p50_ms": round(_pct(ms, 0.50) or 0, 2), "p95_ms": round(_pct(ms, 0.95) or 0, 2),
            "p99_ms": round(_pct(ms, 0.99) or 0, 2),
            "avg_ms": round(statistics.mean(ms), 2) if ms else None,
            "errors": errs, "error_rate": round(rate, 5), "hard_stop": hard_stop,
            "cold_health_ms": round(warm, 2) if warm is not None else None, "per_endpoint": per_endpoint}


def _save(results, filename):
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / filename).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


async def run_default():
    state = _load_state()
    read = _read_specs(state)
    results = []
    for conc in (1, 5, 10, 20):
        results.append(await _run_stage(f"read{conc}", read, conc, 30))
    results.append(await _run_stage("login", _login_specs(state), 5, 20, note="受 login 限流(60/min)保护；RPS 为限流边界"))
    results.append(await _run_stage("share", _share_specs(state), 5, 20, note="受 share-resolve 限流(30/min)保护；RPS 为限流边界"))
    _save(results, "load_result.json")
    print(json.dumps(results, indent=2, ensure_ascii=False))


async def run_read(conc, secs):
    state = _load_state()
    r = await _run_stage("read", _read_specs(state), conc, secs)
    _save([r], "single.json")
    print(json.dumps(r, indent=2, ensure_ascii=False))


async def run_login(conc, secs):
    state = _load_state()
    r = await _run_stage("login", _login_specs(state), conc, secs)
    _save([r], "login.json")
    print(json.dumps(r, indent=2, ensure_ascii=False))


async def run_share(conc, secs):
    state = _load_state()
    r = await _run_stage("share", _share_specs(state), conc, secs)
    _save([r], "share.json")
    print(json.dumps(r, indent=2, ensure_ascii=False))


async def run_upload(n):
    """真实上传 57MB demo n 次 + 轮询任务状态（轻量上传/任务状态）。"""
    state = _load_state()
    headers = _auth_owner(state)
    data = DEMO_FILE.read_bytes()
    out = []
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as c:
        for i in range(n):
            t0 = time.perf_counter()
            r = await c.post("/api/demos/upload", headers=headers,
                             files={"file": (f"load_{i}.dem", data, "application/octet-stream")})
            dt = (time.perf_counter() - t0) * 1000
            status = r.status_code
            body = {}
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                pass
            out.append({"upload_ms": round(dt, 1), "http": status, "code": body.get("detail", {}).get("code"),
                        "demo_id": body.get("demo_id")})
    _save([{"note": "真实上传(57MB)+任务状态", "uploads": out}], "upload.json")
    print(json.dumps(out, indent=2, ensure_ascii=False))


async def run_soak(minutes, conc=10):
    state = _load_state()
    specs = _read_specs(state)
    duration = minutes * 60
    stats = []
    stop_flag = asyncio.Event()
    clients = [httpx.AsyncClient(base_url=BASE, timeout=60, limits=httpx.Limits(max_connections=conc * 2)) for _ in range(conc)]
    start = time.perf_counter()
    tasks = [asyncio.create_task(_worker(c, specs, duration, stats, stop_flag)) for c in clients]
    monitor = asyncio.create_task(_monitor(stats, stop_flag))
    await asyncio.gather(*tasks)
    monitor.cancel()
    elapsed = time.perf_counter() - start
    for c in clients:
        await c.aclose()
    r = _summarize("soak", conc, specs, stats, elapsed, None, stop_flag.is_set(), f"{minutes}min soak")
    _save([r], "soak_result.json")
    print(json.dumps(r, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        asyncio.run(run_default())
    elif cmd == "read":
        asyncio.run(run_read(int(sys.argv[2]), int(sys.argv[3])))
    elif cmd == "login":
        asyncio.run(run_login(int(sys.argv[2]), int(sys.argv[3])))
    elif cmd == "share":
        asyncio.run(run_share(int(sys.argv[2]), int(sys.argv[3])))
    elif cmd == "upload":
        asyncio.run(run_upload(int(sys.argv[2]) if len(sys.argv) > 2 else 1))
    elif cmd == "soak":
        asyncio.run(run_soak(int(sys.argv[2]) if len(sys.argv) > 2 else 30))
    else:
        print(__doc__)
        sys.exit(1)
