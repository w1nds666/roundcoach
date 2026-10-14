"""AI 报告质量与安全校验（Phase 5A D 项）。

- validate_report_structure：必含 摘要/3 优势/3 问题/建议/证据；引用的 metric key 必须真实存在
- sanitize_markdown：剥离 HTML/危险内容（存储层清洗；前端渲染再做 DOM 消毒）
- 离线 eval：真实性/具体性/证据覆盖/空洞话术/越界结论
"""
from __future__ import annotations

import re

# 报告必需结构
REQUIRED_SECTIONS = ["## 摘要", "## 优势", "## 问题", "## 可执行建议", "## 证据"]

# 空洞话术模式（eval 用）
HOLLOW_PATTERNS = [
    r"总之，?继续努力",
    r"相信你(一定|肯定|必)能",
    r"加油.{0,6}(!|！)",
    r"没有数据，?仅凭经验",
    r"根据我的经验",
]

# 越界结论模式（医学/赌博/财务保证）
FORBIDDEN_PATTERNS = [
    r"(保证|包你|稳赚|稳赢|必赚|必赢)",
    r"(治愈|治疗|康复|诊断疾病)",
    r"(下注|投注|赔率|赌博)",
    r"(投资回报|理财建议|股票)",
]

METRIC_KEY_RE = re.compile(r"\[([a-zA-Z0-9_.]+)\]")


def extract_metric_keys(markdown: str) -> list[str]:
    return METRIC_KEY_RE.findall(markdown)


def validate_report_structure(markdown: str, metrics: dict) -> list[str]:
    """返回违规列表（空 = 通过）。"""
    violations = []
    if not markdown or not markdown.strip():
        return ["报告为空"]

    for sec in REQUIRED_SECTIONS:
        if sec not in markdown:
            violations.append(f"缺少必需章节: {sec}")

    # 优势/问题各至少 3 条（数字编号 1. 2. 3.）；章节标题可能带括号说明（如 ## 优势（3））
    for sec, count in [("## 优势", 3), ("## 问题", 3)]:
        m = re.search(rf"{re.escape(sec)}(（[^）]*）)?\n(.*?)(?=\n## |\Z)", markdown, re.S)
        if m:
            items = re.findall(r"^\s*\d+\.", m.group(2), re.M)
            if len(items) < count:
                violations.append(f"{sec} 条目不足（{len(items)}<{count}）")
        else:
            violations.append(f"缺少章节内容: {sec}")

    # 引用 metric key 真实性校验
    known = set(metrics.get("_metric_keys", []))
    if not known:  # 从 snapshot 推导
        known = _derive_keys(metrics)
    for key in extract_metric_keys(markdown):
        if key not in known:
            violations.append(f"引用了不存在的 metric key: [{key}]")

    # 越界/空洞话术
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, markdown):
            violations.append(f"包含越界表述: {pat}")
    for pat in HOLLOW_PATTERNS:
        if re.search(pat, markdown):
            violations.append(f"包含空洞话术: {pat}")

    return violations


def _derive_keys(metrics: dict) -> set[str]:
    """从 metrics snapshot 递归推导全部 metric key。"""
    keys: set[str] = set()

    def walk(node: dict, prefix: str = "") -> None:
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(v, path)
            else:
                keys.add(path)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                # 列表元素键（如 players.1.adr）
                for item in v[:1]:
                    walk(item, f"{path}.[idx]")
                    break

    walk(metrics)
    # 支持 players.<index>.adr 引用形式
    players = metrics.get("players") or []
    for i, p in enumerate(players, start=1):
        if isinstance(p, dict):
            for pk in p:
                keys.add(f"players.{i}.{pk}")
    return keys


def sanitize_markdown(markdown: str) -> str:
    """存储层清洗：去除 HTML 标签/事件属性/链接/图片，仅保留 Markdown 文本结构。"""
    if not markdown:
        return markdown
    # 去除 HTML 标签（含属性）
    s = re.sub(r"<[^>]*>", "", markdown)
    # 去除 markdown 链接与图片（禁止外链）
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return s
