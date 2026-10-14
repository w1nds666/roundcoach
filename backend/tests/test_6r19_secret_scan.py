"""6R19B P0 泄密门禁：secret-scan 必须有效扫描（files>0），canary 必须失败且不打印秘密值。

覆盖：
1. 已提交状态 `--tracked`（全部已跟踪文件）扫描 green 且 files>0（杜绝 files=0 假绿）。
2. `--base 86a9c9f`（6R19 变更区间）扫描 green 且 files>0。
3. canary：临时受控样本注入 JWT / `sk-` API key / private key → 必须 FAIL；换成干净内容 → PASS；
   且任何情况下输出不得打印 secret 值。
4. SteamID64 白名单负向：非 fixture 文件（如临时样例）含 SteamID64 → FAIL；fixture 下为 INFO 不失败。
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
SCAN = ROOT / "scripts" / "secret_scan.py"

# 合成受控秘密（canary）—— 只为证明检测。为避免本测试文件自身的源码文本被 scan 误判为泄露，
# 每个 canary 用「分片 + 运行时拼接」构造，确保源码中不存在任何可匹配的 JWT/`sk-`/private-key 字面量。
def _canary_jwt() -> str:
    h = "eyJhbGciOiJIUzI1NiJ9"
    p = "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    s = "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    return f"{h}.{p}.{s}"


def _canary_sk() -> str:
    return "sk-" + "canyontest1234567890abcdef"


def _canary_privkey() -> str:
    return "-----BEGIN " + "PRIVATE KEY-----" + "\\nMIIBVAIBADANBgkq...\\n" + "-----END " + "PRIVATE KEY-----"


CANARY_STEAM = "76561198812054537"  # 合成 fixture；位处 backend/tests/，scan 记 INFO（白名单）
LEAK_JWT = _canary_jwt()
LEAK_SK = _canary_sk()
LEAK_PRIVKEY_BODY = "MIIBVAIBADANBgkq"  # 私钥内容（用于断言输出不泄露）

# canary 样例写入 gitignored 的 .runtime/canary_tmp/（避免 pytest tmp_path 在 win sandbox 的清理权限问题）
CANARY_DIR = ROOT / ".runtime" / "canary_tmp"


def _canary_file(name: str) -> Path:
    CANARY_DIR.mkdir(parents=True, exist_ok=True)
    return CANARY_DIR / name


def test_canary_secret_patterns_fail_then_clean_passes():
    """canary：注入 JWT/sk-/private-key 必须 FAIL；换成干净内容 PASS；输出不打印秘密值。"""
    f = _canary_file("poisoned.txt")
    f.write_text(f"# sample\njwt={LEAK_JWT}\napi={LEAK_SK}\nkey={_canary_privkey()}\n", encoding="utf-8")
    r = _run("--path", str(f))
    assert r.returncode != 0, "含 JWT/sk-/私钥 必须视为高危并失败（fail-closed）"
    # 输出不得打印任何 secret 值
    for secret in (LEAK_JWT, LEAK_SK, LEAK_PRIVKEY_BODY):
        assert secret not in r.stdout, f"输出泄露秘密值：{secret}"
        assert secret not in r.stderr, f"stderr 泄露秘密值：{secret}"
    # 换成干净内容 → PASS
    f.write_text("# clean\nno secrets here\n", encoding="utf-8")
    r = _run("--path", str(f))
    assert r.returncode == 0, r.stdout + r.stderr
    f.unlink(missing_ok=True)


def test_canary_steamid64_non_fixture_fails():
    """SteamID64：非白名单文件必须 FAIL；白名单 fixture（backend/tests/）为 INFO 不失败。"""
    f = _canary_file("steam.txt")
    f.write_text(f"steam={CANARY_STEAM}\n", encoding="utf-8")
    r = _run("--path", str(f))
    assert r.returncode == 1, "非 fixture 文件含 SteamID64 必须视为高危失败"
    assert "SteamID64_full" in r.stdout
    f.unlink(missing_ok=True)
    # 白名单 fixture 下为 INFO（不失败）
    r2 = _run("--tracked")
    assert r2.returncode == 0, r2.stdout + r2.stderr


def _run(*args: str):
    return subprocess.run([str(PY), str(SCAN), *args], cwd=str(ROOT), capture_output=True, text=True, timeout=180)


def test_committed_tracked_scan_green_files_gt_0():
    """已提交状态 --tracked：必须扫到文件且 green（无 files=0 假绿）。"""
    r = _run("--tracked")
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"files=(\d+)", r.stdout)
    assert m and int(m.group(1)) > 0, f"files 必须 >0：{r.stdout}"


def test_base_range_scan_green_files_gt_0():
    """--base 86a9c9f：6R19 变更区间，必须扫到文件且 green。"""
    r = _run("--base", "86a9c9f")
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"files=(\d+)", r.stdout)
    assert m and int(m.group(1)) > 0, f"files 必须 >0：{r.stdout}"
