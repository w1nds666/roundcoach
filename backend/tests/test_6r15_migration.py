"""6R15：Alembic 迁移 0018（coach snapshots）upgrade / downgrade 测试。

与 6R14 迁移测试同法：在 sqlite 上以「stamp 0017」方式隔离验证 0018 能 upgrade head 与 downgrade 0017。
"""
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
DB_PATH = BACKEND / ".pytest_cache" / "6r15_migration_test.db"


def _run_alembic(cmd: str, db_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    env["DEBUG"] = "true"
    return subprocess.run(
        [str(PY), "-m", "alembic", *cmd.split()],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def db_url():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    # 空库 + stamp 0017（0018 只建 2 张教练表，不依赖 0016/0017 的列/表，sqlite 不强制 FK）
    r = _run_alembic("stamp 0017", f"sqlite:///{DB_PATH.as_posix()}")
    assert r.returncode == 0, r.stderr
    yield f"sqlite:///{DB_PATH.as_posix()}"
    if DB_PATH.exists():
        DB_PATH.unlink()


def _tables() -> set[str]:
    conn = sqlite3.connect(DB_PATH)
    try:
        return {r[0] for r in conn.execute("select name from sqlite_master where type='table'").fetchall()}
    finally:
        conn.close()


def _columns(table: str) -> set[str]:
    conn = sqlite3.connect(DB_PATH)
    try:
        return {r[1] for r in conn.execute(f"pragma table_info({table})").fetchall()}
    finally:
        conn.close()


def test_migration_upgrade_creates_coach_tables(db_url):
    r = _run_alembic("upgrade head", db_url)
    assert r.returncode == 0, r.stderr
    tables = _tables()
    assert "match_coach_snapshots" in tables
    assert "profile_coach_snapshots" in tables
    mc = _columns("match_coach_snapshots")
    assert {"steam_id64", "demo_id", "match_id", "content_hash", "facts", "evidence"} <= mc
    pc = _columns("profile_coach_snapshots")
    assert {"steam_id64", "window", "summary", "top3", "repeated_problems", "trends"} <= pc


def test_migration_downgrade_removes_coach_tables(db_url):
    r = _run_alembic("upgrade head", db_url)
    assert r.returncode == 0, r.stderr
    r = _run_alembic("downgrade 0017", db_url)
    assert r.returncode == 0, r.stderr
    tables = _tables()
    assert "match_coach_snapshots" not in tables
    assert "profile_coach_snapshots" not in tables
