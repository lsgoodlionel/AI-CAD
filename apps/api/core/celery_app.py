"""Celery 应用配置"""
from celery import Celery
from celery.schedules import crontab
from core.config import settings

celery_app = Celery(
    "cad",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "tasks.ai_review",
        "tasks.proposal_notice",
        "tasks.regulation_import",
        "tasks.regulation_api_sync",
        "tasks.batch_review",
        "tasks.model_build",
        "tasks.pipeline",
        "tasks.partition_maintenance",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    # 任务硬/软超时：新链路（模型构建、批量审图）含最长 300s 阻塞子进程，
    # 防止个别任务挂死长期占用 worker。soft 先抛 SoftTimeLimitExceeded 供收尾，
    # hard 到点强杀。
    task_time_limit=1800,
    task_soft_time_limit=1500,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        "tasks.ai_review.*": {"queue": "ai_review"},
        "tasks.regulation_import.*": {"queue": "regulation_import"},
        "tasks.regulation_api_sync.*": {"queue": "regulation_import"},
        "tasks.proposal_notice.*": {"queue": "default"},
        "tasks.batch_review.*": {"queue": "default"},
        "tasks.model_build.*": {"queue": "default"},
        # pipeline 编排任务：只做轻量建议生成，走 default 队列即可，
        # 不与 ai_review/regulation_import 等重负载队列争抢。
        "tasks.pipeline.*": {"queue": "default"},
        "tasks.partition_maintenance.*": {"queue": "default"},
    },
    # Celery beat 定时任务
    beat_schedule={
        "advance-expired-notices": {
            "task": "tasks.proposal_notice.advance_expired_notices",
            "schedule": crontab(minute=0),       # 每小时整点执行
        },
        "sync-regulation-api-sources": {
            "task": "tasks.regulation_api_sync.sync_due_sources_task",
            "schedule": crontab(minute=5),       # 每小时 :05 执行，错开整点高峰
        },
        # 每月 1 号与 15 号 00:30 滚动创建 llm_call_logs 当月+未来两月分区。
        # 双日兜底：即便 1 号漏跑，15 号仍会在月中补齐下月分区（有 default 分区兜底，
        # 不构成硬依赖，仅为让日志落入按月裁剪的正式分区）。
        "ensure-llm-log-partitions": {
            "task": "tasks.partition_maintenance.ensure_llm_log_partitions",
            "schedule": crontab(minute=30, hour=0, day_of_month="1,15"),
        },
    },
)
