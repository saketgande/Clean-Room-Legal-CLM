from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import JobStatus
from app.jobs.models import JobRun


class JobsService:
    """Queue and dispatch background AI/maintenance jobs.

    Part of the DI migration (see backend/DI_MIGRATION.md). Constructed with
    a ``db`` session (request/task-scoped).
    """

    def __init__(self, db: Session):
        self.db = db

    def create_job(
        self,
        *,
        org_id: str,
        job_type: str,
        resource_type: str,
        resource_id: str,
        created_by_user_id: str | None,
        idempotency_key: str | None = None,
        metadata: dict | None = None,
    ) -> JobRun:
        db = self.db
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

    def dispatch_job(self, *, job: JobRun) -> JobRun:
        from app.jobs.tasks import run_ai_job

        db = self.db
        if job.status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED, JobStatus.RUNNING}:
            return job
        if job.status == JobStatus.QUEUED and job.celery_task_id:
            return job
        if job.status == JobStatus.FAILED:
            job.status = JobStatus.QUEUED
            job.error_message = None
            job.error_stack = None
            job.finished_at = None
        task = run_ai_job.delay(job.id)
        job.celery_task_id = task.id
        db.add(job)
        return job


# --- DI-MIGRATION: temporary wrappers ---------------------------------------
# Tracked in backend/DI_MIGRATION.md — remove once no importers remain.

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
    return JobsService(db).create_job(
        org_id=org_id, job_type=job_type, resource_type=resource_type, resource_id=resource_id,
        created_by_user_id=created_by_user_id, idempotency_key=idempotency_key, metadata=metadata,
    )


def dispatch_job(db: Session, *, job: JobRun) -> JobRun:
    return JobsService(db).dispatch_job(job=job)
