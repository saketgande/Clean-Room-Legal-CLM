from celery import Celery
from celery.schedules import crontab

from app.core.config import settings, validate_runtime_settings

validate_runtime_settings(settings)

# Error reporting in the worker process. Mirrors the guarded init in main.py so
# unhandled task exceptions are captured. No-op when the DSN is unset or
# sentry-sdk isn't installed (so local dev + CI are unaffected).
if settings.sentry_dsn:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            send_default_pii=False,
        )
    except ImportError:
        pass

celery_app = Celery(
    "legal_clm",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.jobs.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    # Worker hardening for at-least-once delivery. acks_late + reject_on_worker_lost
    # requeue a task if the worker dies mid-run instead of acking on receipt and
    # silently dropping it; prefetch=1 stops one worker from hoarding a backlog
    # while others idle. Time limits cap a runaway task: the soft limit raises
    # SoftTimeLimitExceeded (catchable) before the hard limit kills the process.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    task_soft_time_limit=720,
    result_expires=3600,
    # visibility_timeout must exceed the hard task_time_limit so Redis doesn't
    # redeliver a long-but-healthy task to a second worker.
    broker_transport_options={"visibility_timeout": 3600},
)

# Periodic maintenance run by celery beat (systemd service: aegis-beat).
# Each task is import-safe and a no-op when its tables are empty, so enabling
# beat never destabilises a fresh deployment.
celery_app.conf.beat_schedule = {
    "send-obligation-reminders": {
        "task": "app.jobs.tasks.send_obligation_reminders",
        "schedule": crontab(hour=8, minute=0),  # daily 08:00 UTC
    },
    "prune-expired-tokens": {
        "task": "app.jobs.tasks.prune_expired_tokens",
        "schedule": crontab(minute=0),  # hourly
    },
    "mark-overdue-approvals": {
        "task": "app.jobs.tasks.mark_overdue_approvals",
        "schedule": crontab(hour=7, minute=30),  # daily
    },
    "run-renewal-window-check": {
        "task": "app.jobs.tasks.run_renewal_window_check",
        "schedule": crontab(hour=7, minute=45),  # daily
    },
    # Retention sweepers. audit_log is intentionally excluded — it is immutable
    # and kept forever for compliance.
    "prune-request-log": {
        "task": "app.jobs.tasks.prune_request_log",
        "schedule": crontab(hour=3, minute=0),  # daily; retention > 90d
    },
    "prune-ai-call-log": {
        "task": "app.jobs.tasks.prune_ai_call_log",
        "schedule": crontab(hour=3, minute=15),  # daily; retention > 180d
    },
    "prune-resource-timeline-event": {
        "task": "app.jobs.tasks.prune_resource_timeline_event",
        "schedule": crontab(hour=3, minute=30),  # daily; retention > 365d
    },
}
