"""6R19 soak 监测：采样 Postgres 活动连接、Redis 队列、worker 容器内存，供泄漏判定。

用法：python scripts/soak_monitor.py <minutes>  （默认 30）
输出 scripts/load_out/soak_monitor.json。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

DOCKER = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
OUT = Path(__file__).resolve().parent.parent / ".runtime" / "load_out" / "soak_monitor.json"


def _sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _pg_conns() -> int:
    r = _sh([DOCKER, "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo", "-t", "-A",
             "-c", "SELECT count(*) FROM pg_stat_activity;"])
    return int(r) if r.isdigit() else -1


def _redis_queue() -> str:
    r = _sh([DOCKER, "exec", "cs2demo-redis-1", "redis-cli", "LLEN", "celery"])
    return r or "0"


def _worker_mem() -> str:
    r = _sh([DOCKER, "stats", "cs2demo-worker-1", "cs2demo-backend-1",
             "--no-stream", "--format", "{{.Name}} mem={{.MemUsage}} cpu={{.CPUPerc}}"])
    return r.replace("\n", " | ")


def main() -> None:
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    end = time.time() + minutes * 60
    samples = []
    while time.time() < end:
        samples.append({
            "ts": round(time.time(), 1),
            "pg_conns": _pg_conns(),
            "celery_queue": _redis_queue(),
            "worker_mem_cpu": _worker_mem(),
        })
        time.sleep(20)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(samples, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[soak-monitor] {len(samples)} samples -> {OUT}")


if __name__ == "__main__":
    main()
