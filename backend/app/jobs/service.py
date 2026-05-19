from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.models import JobRun


def create_job(
    db: Session,
    *,
    org_id: str,
    job_type: str,
    resource_type: str,
    resource_id: str,
    created_by_user_id: str | None,
    idempotency_key: str | None = None,
    metadata: dict | None = None,
) -> JobRun:
    # Honour the idempotency key: re-running a flow (e.g. accepting an edit
    # re-queues a version's extraction jobs) must reuse the existing run
    # instead of inserting a duplicate, which would violate the unique
    # ix_job_run_idempotency_key constraint and 500 the request.
    if idempotency_key is not None:
        existing = db.scalar(
            select(JobRun).where(
                JobRun.org_id == org_id,
                JobRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
    job = JobRun(
        org_id=org_id,
        job_type=job_type,
        resource_type=resource_type,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        metadata_json=metadata or {},
        created_by_user_id=created_by_user_id,
        updated_by_user_id=created_by_user_id,
    )
    db.add(job)
    return job


def dispatch_job(db: Session, *, job: JobRun) -> JobRun:
    from app.jobs.tasks import run_ai_job

    task = run_ai_job.delay(job.id)
    job.celery_task_id = task.id
    db.add(job)
    return job
