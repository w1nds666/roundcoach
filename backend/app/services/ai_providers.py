"""AI 报告 Provider 抽象（Phase 5A C 项）。

- AIReportProvider 接口：generate(prompt, metrics) → ProviderResult
- FakeAIReportProvider：确定性模板生成（测试/UI 垂直切片）
- DeepSeekAIReportProvider：本阶段仅配置结构与请求/响应 schema；
  disabled-by-default gate——任何真实调用前必须显式开启
  settings.allow_external_llm（默认 False），且不读取/写入真实 key。
  禁止真正发请求（Phase 5A 决策门之前）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.config import settings


class ProviderDisabled(Exception):
    """Provider 未启用（决策门）。"""


@dataclass
class ProviderResult:
    text: str
    token_in: int = 0
    token_out: int = 0
    cost_cents: int = 0
    meta: dict = field(default_factory=dict)


class AIReportProvider(ABC):
    name: str = "abstract"
    model: str = ""

    @abstractmethod
    def generate(self, prompt: str, metrics: dict) -> ProviderResult:
        """生成报告。metrics 为指标快照（供确定性 provider 使用）。"""


# ---------------- Fake（确定性，测试/垂直切片） ----------------

class FakeAIReportProvider(AIReportProvider):
    """确定性模板生成：从 metrics 快照提取真实数据，输出结构化 Markdown。

    内容随 metrics 变化而变化（可测），但完全离线、可复现。
    """

    name = "fake"
    model = "deterministic-template-v1"

    def generate(self, prompt: str, metrics: dict, thinking: bool = False,
                 cached_tokens: int = 0) -> ProviderResult:
        """确定性模板生成：从 metrics 快照提取真实数据，输出结构化 Markdown。

        内容随 metrics 变化而变化（可测），但完全离线、可复现。
        thinking/cached_tokens 为云 provider 兼容参数（Fake 忽略）。
        """
        m = metrics.get("match", {})
        teams = metrics.get("teams", {})
        kills = metrics.get("kills", {})
        grens = metrics.get("grenades", {})
        players = metrics.get("players", [])

        top = sorted(players, key=lambda p: p.get("adr", 0), reverse=True)[:3]
        top_lines = "\n".join(
            f"- 玩家#{p['player_index']}（{p['team']}）：ADR {p['adr']}，击杀 {p['kills']}，"
            f"伤害 {p['damage']} [players.{p['player_index']}.adr]"
            for p in top
        )
        weapons = "、".join(f"{w['weapon']}×{w['count']}" for w in kills.get("top_weapons", [])[:3])
        grenade_types = "、".join(f"{g['type']}×{g['count']}" for g in grens.get("by_type", [])[:3])

        markdown = f"""## 摘要

本场 {m.get('map_name', 'unknown')}（{m.get('score_ct', 0)}:{m.get('score_t', 0)}，{m.get('rounds', 0)} 回合）共 {kills.get('total', 0)} 次击杀、{grens.get('total', 0)} 个投掷物。[match.map_name][kills.total][grenades.total]

## 优势（3）

1. **CT 火力集中**：CT 总击杀 {teams.get('CT', {}).get('kills', 0)} 高于 T 的 {teams.get('T', {}).get('kills', 0)}，首杀 {teams.get('CT', {}).get('first_kills', 0)} 次 [teams.CT.kills][teams.T.kills]
2. **高输出选手**：ADR 最高玩家（{top[0]['player_index'] if top else '?'} 号）达 {top[0]['adr'] if top else 0} [players.{top[0]['player_index'] if top else 1}.adr]
3. **武器结构**：主要击杀武器为 {weapons} [kills.top_weapons]

## 问题（3）

1. **T 方输出不足**：T 总伤害 {teams.get('T', {}).get('damage', 0)} 低于 CT 的 {teams.get('CT', {}).get('damage', 0)} [teams.T.damage][teams.CT.damage]
2. **道具结构单一**：投掷物以 {grenade_types} 为主，缺少战术多样性 [grenades.by_type]
3. **回合火力波动**：单回合最高击杀 {kills.get('by_round_max', ('?', 0))[1] if kills.get('by_round_max') else 'unknown'} 次 [kills.by_round_max]

## 可执行建议

1. T 方开局优先围绕 ADR 最高的 {top[0]['player_index'] if top else '?'} 号选手组织进攻，利用其输出能力 [players.{top[0]['player_index'] if top else 1}.adr]
2. 增加烟雾/闪光等战术道具使用，当前结构不足以支撑快攻或回防 [grenades.by_type]
3. 复盘单回合高击杀局（{kills.get('by_round_max', ('?', 0))[0] if kills.get('by_round_max') else '?'} 回合），确认是主动进攻还是对手失误 [kills.by_round_max]

## 证据

- 比分/回合：match.*
- 团队汇总：teams.ct.* / teams.t.*
- 击杀与武器：kills.*
- 道具：grenades.*
- 玩家明细：players.*（匿名序号，不包含真实身份）
- 未实现指标（补枪率/道具效率/经济/残局/路线偏好）本报告未引用，数据不足时不作判断。
"""
        # 模拟 token 估算（确定性）
        token_in = max(len(prompt) // 4, 1)
        token_out = max(len(markdown) // 4, 1)
        return ProviderResult(text=markdown, token_in=token_in, token_out=token_out, cost_cents=0)


# ---------------- DeepSeek V4（OpenAI-compatible adapter，Phase 5B mocked 实现） ----------------

class DeepSeekAIReportProvider(AIReportProvider):
    """DeepSeek V4 OpenAI-compatible adapter。

    Phase 5B：
    - model 默认 deepseek-v4-flash（2026-07-24 起旧模型已退役）
    - 默认 thinking=disabled；质量校验失败由任务层用 thinking=enabled 重试一次
    - connect/read/total timeout 显式设置；最多一次有限重试（429/5xx/timeout/invalid）
    - 测试使用 httpx MockTransport（零真实网络）；生产 transport 由注入决定
    - disabled-by-default：allow_external_llm=False 时构造即禁用（决策门）
    - key 仅从环境变量读取，绝不打印/入库/入日志
    """

    name = "deepseek"
    model = settings.deepseek_model

    def __init__(self, transport=None) -> None:
        if not settings.allow_external_llm:
            raise ProviderDisabled(
                "外部 LLM 未启用：allow_external_llm 为 False（决策门未通过，禁止外发数据）"
            )
        self._transport = transport  # 测试注入 MockTransport；生产 None（真实网络）

    def generate(self, prompt: str, metrics: dict, thinking: bool = False,
                 cached_tokens: int = 0) -> ProviderResult:
        """调用 chat/completions。metrics 为 outbound payload（已白名单化）。

        稳定错误映射：429→RateLimited、5xx→Unavailable、timeout→Timeout、
        invalid response→InvalidResponse。
        """
        import os

        import httpx

        from app.services.ai_cost import estimate_cost_cny
        from app.services.ai_outbound import build_chat_request_body
        from app.services.ai_report_prompt import SYSTEM_PROMPT, build_user_prompt

        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ProviderDisabled("DEEPSEEK_API_KEY 未配置")

        body = build_chat_request_body(
            metrics, SYSTEM_PROMPT, build_user_prompt(metrics), thinking=thinking,
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"

        timeouts = httpx.Timeout(
            settings.deepseek_total_timeout,
            connect=settings.deepseek_connect_timeout,
            read=settings.deepseek_read_timeout,
        )
        client = httpx.Client(transport=self._transport, timeout=timeouts)
        try:
            resp = client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("上游超时") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("上游不可达") from exc
        finally:
            client.close()

        if resp.status_code == 429:
            raise ProviderRateLimited("上游限流，请稍后重试")
        if resp.status_code >= 500:
            raise ProviderUnavailable("上游服务暂时不可用")
        if resp.status_code != 200:
            raise ProviderInvalidResponse(f"上游返回异常状态 {resp.status_code}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
            cached = int(usage.get("prompt_cache_hit_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse("上游响应结构无效") from exc

        cost = estimate_cost_cny(self.model, prompt_tokens, completion_tokens, cached)
        return ProviderResult(
            text=content,
            token_in=prompt_tokens,
            token_out=completion_tokens,
            cost_cents=round(cost * 100, 2),
            meta={"thinking": thinking, "cached_tokens": cached},
        )


class ProviderRateLimited(Exception):
    """上游 429。"""


class ProviderUnavailable(Exception):
    """上游 5xx/不可达。"""


class ProviderTimeout(Exception):
    """上游超时。"""


class ProviderInvalidResponse(Exception):
    """上游响应无效。"""


class BudgetExceeded(Exception):
    """预算保护拒绝（稳定错误码）。"""


def get_provider(name: str | None = None, transport=None) -> AIReportProvider:
    """按配置返回 provider；DeepSeek 未开启时抛 ProviderDisabled（稳定错误码映射）。

    transport：测试注入 httpx.MockTransport（零真实网络）；生产不传。
    """
    provider_name = name or settings.ai_provider
    if provider_name == "fake":
        return FakeAIReportProvider()
    if provider_name == "deepseek":
        return DeepSeekAIReportProvider(transport=transport)
    raise ProviderDisabled(f"未知 provider: {provider_name}")
