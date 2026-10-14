"""6R14：Alembic 迁移 upgrade / downgrade 测试（仅针对 0016/0017 本轮新增）。

说明：项目早期迁移链（0001-0015）是在 Postgres 上执行的，SQLite 对「ALTER 约束」不支持，
因此本测试在 sqlite 上以「引导 users 基表 + stamp 0015」方式隔离地验证本轮两个迁移
（0016 steam identity / 0017 team workspace）能 upgrade head 与 downgrade 0015。
这验证：可回滚、新表/新列齐备、existing users 行（新列 nullable）兼容。
"""
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
DB_PATH = BACKEND / ".pytest_cache" / "6r14_migration_test.db"

BASE_TABLES_SQL = [
    """CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email VARCHAR(255) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        nickname VARCHAR(64) NOT NULL,
        platform VARCHAR(32) NOT NULL,
        rank VARCHAR(16) NOT NULL,
        subscription_tier VARCHAR(16) NOT NULL,
        subscription_expire DATETIME,
        ai_consent_version VARCHAR(16),
        ai_consent_at DATETIME,
        created_at DATETIME NOT NULL
    )""",
]


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
    # 引导 0015 之前的 users 基表（使 add_column 可作用于既有表）
    conn = sqlite3.connect(DB_PATH)
    for sql in BASE_TABLES_SQL:
        conn.execute(sql)
    conn.commit()
    conn.close()
    # 把 alembic 版本戳记为 0015（跳过不可在 sqlite 执行的早期迁移链）
    r = _run_alembic("stamp 0015", f"sqlite:///{DB_PATH.as_posix()}")
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


def test_migration_upgrade_adds_6r14_tables(db_url):
    r = _run_alembic("upgrade head", db_url)
    assert r.returncode == 0, r.stderr
    tables = _tables()
    for expected in ("steam_identities", "openid_states", "identity_audits", "team_workspaces", "team_memberships", "team_audits"):
        assert expected in tables, f"缺少表 {expected}: {tables}"
    users_cols = _columns("users")
    assert "last_password_auth_at" in users_cols
    assert "steam_cooldown_until" in users_cols
    # 唯一约束存在
    si = _columns("steam_identities")
    assert "subject" in si and "steam_id64" in si and "status" in si


def test_migration_downgrade_removes_6r14_tables(db_url):
    r = _run_alembic("upgrade head", db_url)
    assert r.returncode == 0, r.stderr
    r = _run_alembic("downgrade 0015", db_url)
    assert r.returncode == 0, r.stderr
    tables = _tables()
    for gone in ("steam_identities", "openid_states", "identity_audits", "team_workspaces", "team_memberships", "team_audits"):
        assert gone not in tables, f"downgrade 后仍存在表 {gone}"
    users_cols = _columns("users")
    assert "last_password_auth_at" not in users_cols
    assert "steam_cooldown_until" not in users_cols


def test_migration_existing_user_compatible(db_url):
    r = _run_alembic("upgrade head", db_url)
    assert r.returncode == 0, r.stderr
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "insert into users (email, password_hash, nickname, platform, rank, subscription_tier, created_at) "
            "values ('legacy@test.com', 'x', 'legacy', '', '', 'free', datetime('now'))"
        )
        conn.commit()
        # 新列 nullable，旧行可读
        n = conn.execute("select count(*) from users where email='legacy@test.com'").fetchone()[0]
        assert n == 1
        row = conn.execute("select last_password_auth_at, steam_cooldown_until from users where email='legacy@test.com'").fetchone()
        assert row == (None, None)
    finally:
        conn.close()
