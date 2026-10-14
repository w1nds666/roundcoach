"""6R19B P0 secret-scan 门禁：扫描文件是否含高危秘密原值（可复现、不假绿）。

规范：
- **只报告类型/文件/计数，绝不打印匹配的 secret/token 值**。
- **JWT / `sk-` API key / private key 永远 HIGH**，没有任何白名单/忽略可豁免。
- **SteamID64 仅允许在「精确 synthetic fixture 文件白名单」中**（测试 fixture + steamid 工具常量的偏移常数），
  其余文件命中原格式 SteamID64 → HIGH。禁止 blanket 忽略。
- 运行时 runtime 具体 token（.runtime/load_state.json）→ HIGH（精确匹配，不打印值）。
- **报告必打印 files=<N>；N==0 视为不确定（fail，非 PASS）**，避免假绿。

模式（默认 --tracked，保证 files>0，杜绝 files=0 PASS）：
  python scripts/secret_scan.py                       # --tracked：扫描全部已跟踪提交文件
  python scripts/secret_scan.py --base <sha>          # 变更集 <sha>..HEAD + 未跟踪非忽略
  python scripts/secret_scan.py --worktree            # 整个工作树（排除已知目录）
  python scripts/secret_scan.py --path <file|dir>     # 只扫给定路径（canary/开发）；可多次 --path
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 高危模式：JWT / API key / private key —— 无任何豁免（fail-closed）。
HIGH_PATTERNS = {
    "JWT_access_token": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    "API_key_sk": re.compile(r"\bsk-[A-Za-z0-9]{12,}\b"),
    "Private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
}

# SteamID64 原格式：HIGH，但仅以下 synthetic fixture 文件（含其偏移常数）豁免为 INFO。
# 白名单必须精确到文件；测试 fixture 里出现多位合成 SteamID64 是合法数据，非泄露。
STEAMID64_PATTERN = re.compile(r"\b7656119[0-9]{10}\b")
STEAMID64_FIXTURE_ALLOWLIST = (
    r"^backend/tests/",
    r"^frontend/tests/",
    r"^docs/phase6r14-contract\.md$",
    r"^docs/phase6r15-gate-evidence\.md$",
    # steamid 工具：其 17 位常数是算法偏移(STEAMID64_OFFSET)，不是用户身份
    r"^backend/app/services/steamid\.py$",
)


def _is_steamid64_fixture(rel: str) -> bool:
    return any(re.match(pat, rel) for pat in STEAMID64_FIXTURE_ALLOWLIST)


# 已知测试常量（仅 INFO）：测试口令。
INFO_PATTERNS = {
    "test_password": re.compile(r"\b(password123|loadpass123)\b"),
}

# 文本文件扩展（跳过二进制）
TEXT_EXTS = {".py", ".js", ".ts", ".vue", ".json", ".md", ".yml", ".yaml", ".txt", ".cfg", ".ini", ".toml", ".env.example", ".conf", ".css", ".html", ".sh", ".ps1", ".sql"}
# 排除目录（不扫描）
EXCLUDE_DIRS = ("/.git/", "/.venv/", "/node_modules/", "/frontend/dist/", "/.runtime/", "/scripts/load_out/", "/scripts/state/", "/test-results/")


def _specific_token_values() -> list[str]:
    """gitignored .runtime/load_state.json 的具体 JWT/share token（精确匹配，不打印值）。"""
    st = ROOT / ".runtime" / "load_state.json"
    if not st.exists():
        return []
    try:
        import json
        d = json.loads(st.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    vals = []
    if d.get("owner", {}).get("token"):
        vals.append(d["owner"]["token"])
    if d.get("share_token"):
        vals.append(d["share_token"])
    for u in d.get("users", []):
        if u.get("token"):
            vals.append(u["token"])
    return vals


def _git_output(*args: str) -> list[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()


def _paths_for_mode(mode: str, arg: str | None) -> list[str]:
    if mode == "tracked":
        return [p for p in _git_output("ls-files") if p.strip()]
    if mode == "base":
        base = arg or "86a9c9f"
        # 已提交区间 <base>..HEAD + 未提交 dirty（unstaged + staged）+ 未跟踪非忽略
        committed = _git_output("diff", "--name-only", f"{base}..HEAD")
        unstaged = _git_output("diff", "--name-only")
        staged = _git_output("diff", "--cached", "--name-only")
        untracked = _git_output("ls-files", "--others", "--exclude-standard")
        return [p for p in committed + unstaged + staged + untracked if p.strip()]
    if mode == "worktree":
        out = []
        for p in ROOT.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if any(x in rel for x in EXCLUDE_DIRS):
                continue
            out.append(rel)
        return out
    if mode == "path":
        out = []
        for spec in arg.split(","):
            p = ROOT / spec
            if p.is_file():
                out.append(spec)
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        out.append(f.relative_to(ROOT).as_posix())
        return out
    return []


def _scan(paths: list[str]) -> int:
    findings = []
    specific = _specific_token_values()
    for rel in paths:
        fp = ROOT / rel
        if fp.suffix not in TEXT_EXTS:
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for label, pat in HIGH_PATTERNS.items():
            n = len(pat.findall(text))
            if n:
                findings.append({"severity": "HIGH", "type": label, "file": rel, "count": n})
        # SteamID64：白名单 fixture 文件允许（INFO），否则 HIGH
        steam_n = len(STEAMID64_PATTERN.findall(text))
        if steam_n:
            sev = "INFO" if _is_steamid64_fixture(rel) else "HIGH"
            findings.append({"severity": sev, "type": "SteamID64_full", "file": rel, "count": steam_n})
        for sv in specific:
            if sv and sv in text:
                findings.append({"severity": "HIGH", "type": "Runtime_token", "file": rel, "count": text.count(sv)})
        for label, pat in INFO_PATTERNS.items():
            n = len(pat.findall(text))
            if n:
                findings.append({"severity": "INFO", "type": label, "file": rel, "count": n})

    high = [f for f in findings if f["severity"] == "HIGH"]
    if not paths:
        print("[secret-scan] files=0 NO_FILES_SCANNED_inconclusive")
        print("[secret-scan] FAIL: 未扫描任何有效文件（files=0），结果不确定，非 PASS。")
        return 2
    print(f"[secret-scan] files={len(paths)} findings={len(findings)} high={len(high)}")
    for f in findings:
        print(f"  {f['severity']:5} {f['type']:22} count={f['count']:3} file={f['file']}")
    if high:
        print("[secret-scan] FAIL: 高危秘密原值被检入/残留，禁止提交（值未显示）。")
        return 1
    print("[secret-scan] PASS: 无高危秘密原值。")
    return 0


def main() -> int:
    args = sys.argv[1:]
    mode = "tracked"
    arg = None
    for i, a in enumerate(args):
        if a == "--base":
            mode, arg = "base", (args[i + 1] if i + 1 < len(args) else None)
        elif a == "--tracked":
            mode = "tracked"
        elif a == "--worktree":
            mode = "worktree"
        elif a == "--path":
            # 允许多个 --path，收集到一个逗号串
            mode = "path"
            vals = [a]
            j = i
            while j + 1 < len(args) and not args[j + 1].startswith("--"):
                vals.append(args[j + 1]); j += 1
            arg = (arg + "," if arg else "") + ",".join(vals)
        elif a == "--help":
            print(__doc__)
            return 0
    paths = _paths_for_mode(mode, arg)
    return _scan(paths)


if __name__ == "__main__":
    sys.exit(main())
