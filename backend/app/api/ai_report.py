"""AI 诊断报告 API（Phase 5A E 项）。

- 全部接口登录 + 对象权限（非本人 demo → 404）+ 跨用户隔离
- entitlement：free/未授权明确锁定；单次购买本阶段仅占位（不接支付）
- 幂等：同 demo + input_hash + prompt_version 复用已有报告
- 创建后投递 Celery（Fake provider 离线生成，无外部请求）
- 6R11.2：通用 report trust/status 规则——provider=fake / 模板 model / 输入哈希与当前 demo 不一致 /
  markdown 含未解析模板键 → 判为「测试/过期报告」，不得当作正式 AI 结论渲染。
"""
import re

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.demo import _get_own_demo
from app.api.deps import get_current_user
from app.database import get_db
from app.errors import AI_REPORT_NOT_FOUND, DEMO_NOT_PARSED, ApiError
from app.models import AIReport, Demo, User
from app.services.ai_metrics import build_metrics_snapshot, compute_input_hash
from app.services.rate_limit import rate_limit
from app.services.tier import has_feature, require_tier
from app.services.ai_report_prompt import PROMPT_VERSION
from app.tasks.ai_report_tasks import generate_ai_report

router = APIRouter(prefix="/api/demos", tags=["ai-report"])
consent_router = APIRouter(prefix="/api/ai-consent", tags=["ai-report"])

# 当前外发同意版本（UI 展示文案同步维护）
AI_CONSENT_VERSION = "2026-08-15.v1"

# 未解析模板键：`[a.b]` / `[match.map_name]` 这类内部 token（绝不出现在正式结论主文）
_TEMPLATE_KEY_RE = re.compile(r"\[[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+]")


def _has_unresolved_placeholders(markdown: str | None) -> bool:
    return bool(markdown) and bool(_TEMPLATE_KEY_RE.search(markdown))


def _metrics_summary(r: AIReport) -> dict:
    """报告生成时所基于的 match 摘要（供技术详情/不一致说明，不泄露坐标等敏感项）。"""
    s = r.metrics_snapshot or {}
    m = s.get("match", {})
    d = s.get("demo", {})
    return {
        "map_name": d.get("map_name"),
        "score_ct": m.get("score_ct"),
        "score_t": m.get("score_t"),
        "rounds": m.get("rounds"),
        "demo_id": d.get("id"),
    }


def _trust_status(r: AIReport, current_input_hash: str | None) -> dict:
    """通用信任判定：任一命中即不可信（测试/过期/占位）。"""
    reasons = []
    if r.provider == "fake":
        reasons.append("fake_provider")
    if r.model == "deterministic-template-v1":
        reasons.append("template_provider")
    if current_input_hash and r.input_hash and r.input_hash != current_input_hash:
        reasons.append("stale_snapshot")
    if _has_unresolved_placeholders(r.markdown):
        reasons.append("unresolved_placeholders")
    return {"trusted": not reasons, "reasons": reasons}


def _current_input_hash(db: Session, demo_id: int) -> str | None:
    """当前 demo 的指标快照输入哈希（用于判 stale）；demo 未解析时返回 None。"""
    try:
        return compute_input_hash(build_metrics_snapshot(db, demo_id))
    except ValueError:
        return None


@consent_router.get("")
def get_ai_consent(current_user: User = Depends(get_current_user)):
    """查询当前用户的外发同意状态。"""
    return {
        "version": current_user.ai_consent_version,
        "consented_at": current_user.ai_consent_at.isoformat() if current_user.ai_consent_at else None,
        "required_version": AI_CONSENT_VERSION,
    }


@consent_router.post("")
def grant_ai_consent(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """记录同意（仅存版本与时间，不记录其他个人信息）。"""
    from datetime import datetime, timezone

    current_user.ai_consent_version = AI_CONSENT_VERSION
    current_user.ai_consent_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"ok": True, "version": AI_CONSENT_VERSION}


@router.get("/{demo_id}/ai-report/entitlement")
def ai_report_entitlement(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """AI 报告权限查询（free 明确锁定；pro 可用；单次购买占位 Phase 6）。"""
    _get_own_demo(demo_id, current_user, db)  # 对象权限：非本人 404
    if has_feature(current_user, "ai_report"):
        return {"can_generate": True, "reason": "pro", "single_purchase_available": False}
    return {"can_generate": False, "reason": "subscription_required", "single_purchase_available": False}


def _report_payload(r: AIReport, current_input_hash: str | None = None) -> dict:
    return {
        "id": r.id,
        "demo_id": r.demo_id,
        "status": r.status,
        "prompt_version": r.prompt_version,
        "provider": r.provider,
        "model": r.model,
        "error": r.error,
        "token_in": r.token_in,
        "token_out": r.token_out,
        "cost_cents": r.cost_cents,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "trust": _trust_status(r, current_input_hash),
        "metrics_summary": _metrics_summary(r),
    }


@router.post("/{demo_id}/ai-report")
def create_ai_report(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("ai_report")),
):
    """创建 AI 诊断报告（pro 专属；幂等：同 input_hash 复用）。"""
    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "pro", "AI 诊断报告属于进阶版功能，升级后解锁")

    if demo.match_id is None:
        raise ApiError(409, DEMO_NOT_PARSED, "demo 尚未解析完成")

    snapshot = build_metrics_snapshot(db, demo_id)
    input_hash = compute_input_hash(snapshot)

    # 幂等：同 demo + input_hash + prompt_version 已存在 → 返回现有
    existing = db.scalar(
        select(AIReport).where(
            AIReport.demo_id == demo_id,
            AIReport.input_hash == input_hash,
            AIReport.prompt_version == PROMPT_VERSION,
        )
    )
    if existing is not None:
        return {"report": _report_payload(existing, input_hash), "reused": True}

    # provider 从配置读取（fake | deepseek）；云 provider 需 consent + allow_external_llm
    from app.config import settings

    provider = settings.ai_provider
    model = settings.deepseek_model if provider == "deepseek" else "deterministic-template-v1"
    report = AIReport(
        user_id=current_user.id,
        demo_id=demo_id,
        status="pending",
        input_hash=input_hash,
        prompt_version=PROMPT_VERSION,
        provider=provider,
        model=model,
        metrics_snapshot=snapshot,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    generate_ai_report.delay(report.id)
    db.refresh(report)  # 同步执行场景（测试/急切模式）下返回最新状态
    return {"report": _report_payload(report, input_hash), "reused": False}


@router.get("/{demo_id}/ai-report")
def list_ai_reports(
    demo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """该 demo 的报告列表（新→旧）。"""
    _get_own_demo(demo_id, current_user, db)
    rows = db.scalars(
        select(AIReport).where(
            AIReport.demo_id == demo_id,
            AIReport.user_id == current_user.id,   # 6R19：授权的第二因子，避免只依赖 demo 归属代理
        ).order_by(AIReport.id.desc()).limit(10)
    ).all()
    cur = _current_input_hash(db, demo_id)
    return [_report_payload(r, cur) for r in rows]


@router.get("/{demo_id}/ai-report/{report_id}")
def get_ai_report(
    demo_id: int,
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """报告详情（含 markdown 与指标快照摘要）。"""
    demo = _get_own_demo(demo_id, current_user, db)
    report = db.scalar(
        select(AIReport).where(
            AIReport.id == report_id, AIReport.demo_id == demo.id,
            AIReport.user_id == current_user.id,   # 6R19：账号级授权第二因子
        )
    )
    if report is None:
        raise ApiError(404, AI_REPORT_NOT_FOUND, "报告不存在")
    payload = _report_payload(report, _current_input_hash(db, demo_id))
    payload["markdown"] = report.markdown
    payload["input_hash"] = report.input_hash
    return payload


@router.post("/{demo_id}/ai-report/{report_id}/retry")
def retry_ai_report(
    demo_id: int,
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("ai_report")),
):
    """失败报告重试（仅 failed 状态）。"""
    demo = _get_own_demo(demo_id, current_user, db)
    require_tier(current_user, "pro", "AI 诊断报告属于进阶版功能，升级后解锁")
    report = db.scalar(
        select(AIReport).where(
            AIReport.id == report_id, AIReport.demo_id == demo.id,
            AIReport.user_id == current_user.id,   # 6R19：账号级授权第二因子
        )
    )
    if report is None:
        raise ApiError(404, AI_REPORT_NOT_FOUND, "报告不存在")
    if report.status != "failed":
        return {"report": _report_payload(report, _current_input_hash(db, demo_id))}
    report.status = "pending"
    report.error = None
    db.commit()
    generate_ai_report.delay(report.id)
    db.refresh(report)  # 同步执行场景返回最新状态
    return {"report": _report_payload(report, _current_input_hash(db, demo_id))}
