"""6R19 负载/稳定性种子脚本：构造可复现的负载数据（不改产品逻辑）。

职责（分两步看，本文件只做 seed）：
1. 通过真实注册 API 创建 N 个负载用户（唯一邮箱 loaduser<i>@load.test，密码 loadpass123）。
2. 把已解析 demo（默认 id=1）所有权重分配给负载主用户，使其能访问 dashboard/summary/replay
   （避免为负载测试反复上传大 demo；这只改 dev 运行库，不涉及 git 提交状态）。
3. 为负载主用户创建一个分享 token（owner 创建），供公开 /api/share/resolve 负载使用。
4. 把状态写入 scripts/load_state.json，供 load_test.py 使用。

约束：只操作本机 docker 技术栈（cs2demo-*）；不改产品规则；不输出任何 secret。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

BASE = os.environ.get("LOAD_BASE_URL", "http://localhost:80")
DOCKER = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
# 6R19 P0 泄密防护：状态含 synthetic JWT/share token，只写入 gitignored 的 .runtime/ 目录，绝不入库。
STATE_PATH = Path(__file__).resolve().parent.parent / ".runtime" / "load_state.json"
PASSWORD = "loadpass123"


def psql(sql: str) -> str:
    out = subprocess.run(
        [DOCKER, "exec", "cs2demo-postgres-1", "psql", "-U", "cs2", "-d", "cs2demo", "-t", "-A", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    return out.stdout.strip()


def psql_scalar(sql: str) -> str:
    line = psql(sql).splitlines()
    for ln in line:
        ln = ln.strip()
        if ln.isdigit():
            return ln
    return ""


def register(client: httpx.Client, email: str) -> tuple[int, str]:
    """注册并返回 (user_id, access_token)。已存在则登录复用。"""
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    if r.status_code == 200:
        data = r.json()
        return data["user"]["id"], data["access_token"]
    r = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "nickname": "load"})
    if r.status_code == 200:
        data = r.json()
        return data["user"]["id"], data["access_token"]
    raise RuntimeError(f"register/login failed: {r.status_code} {r.text[:200]}")


def main() -> None:
    n = int(os.environ.get("LOAD_USERS", "20")) if os.environ.get("LOAD_USERS") else 20
    demo_id = None

    with httpx.Client(base_url=BASE, timeout=30) as client:
        owner_uid, owner_token = register(client, "loadowner@loadtester.com")
        users = []
        for i in range(1, n + 1):
            uid, token = register(client, f"loaduser{i}@loadtester.com")
            users.append({"email": f"loaduser{i}@loadtester.com", "uid": uid, "token": token})
        # 6R19 P0/回归修复：不再重分配给 fixture demo（会破坏依赖 demo 归属的 E2E，如 report-4b 的 /demo/1）。
        # 改为使用 loadowner **自家** 已解析的 demo（本会话由上传产生，如 demo 24）；无则上传 test_demo.dem。
        own = psql(f"SELECT id FROM demos WHERE user_id={owner_uid} AND status='parsed' ORDER BY id LIMIT 1;")
        demo_id = int(own.splitlines()[0].strip()) if own and own.splitlines()[0].strip().isdigit() else None
        if demo_id is None:
            with open(Path(__file__).resolve().parent.parent / "backend" / "tests" / "demos" / "test_demo.dem", "rb") as fp:
                up = client.post("/api/demos/upload", headers={"Authorization": f"Bearer {owner_token}"},
                                 files={"file": ("load_demo.dem", fp, "application/octet-stream")})
            if up.status_code == 200:
                demo_id = up.json().get("demo_id")
            if demo_id is None:
                raise RuntimeError("load seed 无法为 owner 准备已解析 demo")
        # owner 创建一个分享 token（即便 demo_id 也未解析也可用，load 读路径仍可用）
        h = {"Authorization": f"Bearer {owner_token}"}
        r = client.post(f"/api/demos/{demo_id}/share", headers=h, json={"expires_in_hours": 24})
        if r.status_code == 200:
            token = r.json()["token"]
        else:
            # 可能已存在，重新创建（create 覆盖旧 token）
            rgs = client.get(f"/api/demos/{demo_id}/share", headers=h)
            raise RuntimeError(f"share create failed: {r.status_code} {r.text[:200]}")
    state = {
        "base_url": BASE,
        "owner": {"email": "loadowner@loadtester.com", "uid": owner_uid, "token": owner_token},
        "demo_id": demo_id,
        "share_token": token,
        "users": users,
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    print(f"[seed] wrote {STATE_PATH} with {n} users; demo_id={demo_id}; share token len={len(token)}")


if __name__ == "__main__":
    main()
