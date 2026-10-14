"""AI 报告生成任务（Phase 5B：DeepSeek V4 mocked 支持 + 成本/consent 保护）。

状态机：pending → running → succeeded | failed
- 幂等：创建时由 (demo_id, input_hash, prompt_version) 唯一约束保证
- 有限重试：max_retries=1；重试后仍失败 → failed（稳定错误信息）
- 超时：provider.generate 包在超时保护内
- 质量门：生成结果必须通过结构/引用校验；失败且 provider 为 deepseek 时
  最多一次 thinking=enabled 重试；Fake 不重试（确定性）
- 预算保护：单份估算上限 + 全局月预算，超限稳定拒绝（不静默扣费）
- consent gate：provider=deepseek 且用户未同意外发 → failed（稳定错误）
- 安全：输出 sanitize；日志只含 report_id/provider/model/status/latency/token/稳定错误码
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import AIReport, User
from app.services.ai_cost import check_monthly_budget, estimate_cost_cny
from app.services.ai_outbound import build_outbound_payload
from app.services.ai_providers import (
    BudgetExceeded,
    ProviderDisabled,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    get_provider,
)
from app.services.ai_report_prompt import PROMPT_VERSION
from app.services.ai_report_quality import sanitize_markdown, validate_report_structure
from app.tasks import celery_app

GENERATE_TIMEOUT_SECONDS = 140
# 云 provider 质量失败后的 thinking 重试（最多一次）
THINKING_RETRY = True


@celery_app.task(name="app.tasks.ai_report_tasks.generate_ai_report", max_retries=1, default_retry_delay=5)
def generate_ai_report(report_id: int, transport=None) -> dict:
    """执行报告生成。transport 仅测试注入（MockTransport，零真实网络）。"""
    db = SessionLocal()
    started = time.monotonic()
    try:
        report = db.get(AIReport, report_id)
        if report is None:
            raise ValueError(f"report {report_id} not found")
        if report.status in ("succeeded", "running"):
            return {"report_id": report.id, "status": report.status}

        report.status = "running"
        report.error = None
        db.commit()

        provider = get_provider(report.provider, transport=transport)
        outbound = build_outbound_payload(report.metrics_snapshot)

        # consent gate：云外发必须用户已同意（consent 版本校验）
        if report.provider == "deepseek":
            user = db.get(User, report.user_id)
            if not user or not user.ai_consent_version or not user.ai_consent_at:
                report.status = "failed"
                report.error = "需先同意将匿名比赛统计发送至 DeepSeek 后生成"
                report.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
                return {"report_id": report.id, "status": "failed",
                        "error": "需先同意将匿名比赛统计发送至 DeepSeek 后生成"}

        # 预算保护（估算上限 + 全局月预算）
        est_cost = estimate_cost_cny(provider.model, 3000, 2000)  # 保守估算（上限场景）
        if est_cost > 0.05:
            raise BudgetExceeded("超出单份报告成本预算，已拒绝生成（不静默扣费）")
        if not check_monthly_budget(db, est_cost):
            raise BudgetExceeded("本月 AI 报告预算已用尽，请下月再试")

        # 首次生成：thinking=disabled（低延迟/低 token）
        result = _run_with_timeout(provider.generate, "", outbound, False)
        violations = validate_report_structure(result.text, report.metrics_snapshot)

        # 质量失败：deepseek 最多一次 thinking=enabled 重试
        if violations and report.provider == "deepseek" and THINKING_RETRY:
            result = _run_with_timeout(provider.generate, "", outbound, True)
            violations = validate_report_structure(result.text, report.metrics_snapshot)

        if violations:
            report.status = "failed"
            report.error = "质量校验未通过：" + "；".join(violations[:5])
            report.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            return {"report_id": report.id, "status": "failed", "quality_violations": violations}

        report.markdown = sanitize_markdown(result.text)
        report.token_in = result.token_in
        report.token_out = result.token_out
        report.cost_cents = result.cost_cents
        report.model = result.meta.get("model", provider.model) or provider.model
        report.status = "succeeded"
        report.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        latency_ms = int((time.monotonic() - started) * 1000)
        # 结构化日志：仅元数据，无 prompt/响应/指标内容（Phase 5B 安全要求）
        _log_structured(report.id, report.provider, report.model, "succeeded",
                        latency_ms, report.token_in, report.token_out, report.cost_cents)
        return {"report_id": report.id, "status": "succeeded"}
    except BudgetExceeded as exc:
        db.rollback()
        _mark_failed(db, report_id, str(exc))
        return {"report_id": report_id, "status": "failed", "error": str(exc)}
    except (ProviderRateLimited, ProviderUnavailable, ProviderTimeout, ProviderInvalidResponse) as exc:
        db.rollback()
        _mark_failed(db, report_id, f"上游生成失败：{_stable_provider_error(exc)}")
        return {"report_id": report_id, "status": "failed", "error": str(exc)}
    except ProviderDisabled as exc:
        db.rollback()
        _mark_failed(db, report_id, str(exc))
        return {"report_id": report_id, "status": "failed", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        try:
            raise generate_ai_report.retry(exc=exc)
        except Exception:  # noqa: BLE001
            _mark_failed(db, report_id, "报告生成失败，请稍后重试")
            return {"report_id": report_id, "status": "failed"}
    finally:
        db.close()


def _stable_provider_error(exc: Exception) -> str:
    if isinstance(exc, ProviderRateLimited):
        return "限流，请稍后重试"
    if isinstance(exc, ProviderUnavailable):
        return "服务暂时不可用"
    if isinstance(exc, ProviderTimeout):
        return "生成超时"
    if isinstance(exc, ProviderInvalidResponse):
        return "上游响应异常"
    return "未知错误"


def _log_structured(report_id, provider, model, status, latency_ms, token_in, token_out, cost_cents):
    """结构化日志（不含 prompt/响应内容/metrics/key）。"""
    import logging

    logger = logging.getLogger("ai_report")
    logger.info(
        "ai_report report_id=%s provider=%s model=%s status=%s latency_ms=%s tokens=%s+%s cost_cents=%s",
        report_id, provider, model, status, latency_ms, token_in, token_out, cost_cents,
    )


def _run_with_timeout(fn, *args, timeout: int = GENERATE_TIMEOUT_SECONDS, **kwargs):
    """简单超时保护。"""
    import threading

    result: dict = {}

    def worker():
        try:
            result["value"] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            result["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise ProviderTimeout("报告生成超时")
    if "error" in result:
        raise result["error"]
    return result["value"]


def _mark_failed(db, report_id: int, message: str) -> None:
    report = db.get(AIReport, report_id)
    if report is not None:
        report.status = "failed"
        report.error = message[:500]
        report.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
