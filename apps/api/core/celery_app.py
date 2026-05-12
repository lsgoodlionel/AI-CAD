"""Celery 应用配置"""
from celery import Celery
from celery.schedules import crontab
from core.config import settings

celery_app = Celery(
    "cad",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["tasks.ai_review", "tasks.proposal_notice"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Celery beat 定时任务
    beat_schedule={
        "advance-expired-notices": {
            "task": "tasks.proposal_notice.advance_expired_notices",
            "schedule": crontab(minute=0),   # 每小时整点执行
        },
    },
)
