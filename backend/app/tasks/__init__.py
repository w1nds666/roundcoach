"""Celery 任务包。parse_tasks（Phase 1）+ ai_report_tasks（Phase 5A）。"""
from celery import Celery

from app.config import settings

celery_app = Celery(
    "cs2demo",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.parse_tasks", "app.tasks.ai_report_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    worker_prefetch_multiplier=1,
)
