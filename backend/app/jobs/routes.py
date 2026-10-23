from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import JobStatus
from app.jobs.dependencies import get_jobs_service
from app.jobs.models import JobRun
from app.jobs.service import JobsService

router = APIRouter(prefix="/jobs", tags=["jobs"])

# A queued/running job older than this with no completion is treated as
# stuck (e.g. a worker died) and reaped to FAILED so it can be re-run.
STUCK_JOB_TTL = timedelta(minutes=20)


def _reap_stuck_jobs(db: Session, *, org_id: str) -> None:
    cutoff = utcnow() - STUCK_JOB_TTL
    stuck = db.scalars(
        select(JobRun).where(
            JobRun.org_id == org_id,
            or_(
                and_(
                    JobRun.status == JobStatus.QUEUED,
                    JobRun.started_at.is_(None),
                    JobRun.created_at < cutoff,
                ),
                and_(
                    JobRun.status == JobStatus.RUNNING,
                    JobRun.started_at.is_not(None),
                    JobRun.started_at < cutoff,
                ),
            ),
        )
    ).all()
    if not stuck:
        return
    for job in stuck:
        job.status = JobStatus.FAILED
        job.error_message = (
            "Timed out — no progress within the expected window "
            "(the worker may have stopped). Re-run to retry."
        )
        job.finished_at = utcnow()
    db.commit()


@router.get("")
def list_jobs(
    job_status: JobStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    """Newest first. ``limit``/``offset`` page through all of them; ``status`` narrows the list."""
    _reap_stuck_jobs(db, org_id=current_user.org_id)
    query = select(JobRun).where(JobRun.org_id == current_user.org_id)
    if job_status is not None:
        query = query.where(JobRun.status == job_status)
    # The id breaks ties so rows created in the same instant never move between pages.
    return db.scalars(
        query.order_by(JobRun.created_at.desc(), JobRun.id.desc()).limit(limit).offset(offset)
    ).all()


@router.get("/summary")
def jobs_summary(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    """How many jobs are in each state, across ALL of them (the list above is only one page)."""
    _reap_stuck_jobs(db, org_id=current_user.org_id)
    counts = dict.fromkeys((s.value for s in JobStatus), 0)
    rows = db.execute(
        select(JobRun.status, func.count())
        .where(JobRun.org_id == current_user.org_id)
        .group_by(JobRun.status)
    ).all()
    for state, n in rows:
        counts[str(getattr(state, "value", state))] = n
    return {**counts, "total": sum(counts.values())}


@router.get("/{job_id}")
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    job = db.get(JobRun, job_id)
    if job is None or job.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    job = db.get(JobRun, job_id)
    if job is None or job.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is already terminal")
    job.status = JobStatus.CANCELLED
    job.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/run")
def enqueue_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
    jobs_service: JobsService = Depends(get_jobs_service),
):
    job = db.get(JobRun, job_id)
    if job is None or job.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status not in {JobStatus.QUEUED, JobStatus.FAILED}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only queued or failed jobs can be enqueued")
    jobs_service.dispatch_job(job=job)
    job.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(job)
    return job
