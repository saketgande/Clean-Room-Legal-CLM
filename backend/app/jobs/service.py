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
