from celery import Celery

from app.config import get_settings

settings = get_settings()

celery = Celery(
    "mailmind",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "sync-all-accounts": {
            "task": "app.workers.tasks.sync_all_accounts",
            "schedule": 300.0,  # every 5 minutes
        },
        "embed-pending": {
            "task": "app.workers.tasks.embed_pending",
            "schedule": 120.0,  # every 2 minutes, picks up unembedded emails
        },
    },
)
