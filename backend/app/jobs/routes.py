from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.core.enums import JobStatus
from app.jobs.models import JobRun
from app.jobs.service import dispatch_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return db.scalars(
        select(JobRun)
        .where(JobRun.org_id == current_user.org_id)
        .order_by(JobRun.created_at.desc())
        .limit(100)
    ).all()


@router.get("/{job_id}")
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    job = db.get(JobRun, job_id)
    if job is None or job.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
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
    current_user=Depends(get_current_user),
):
    job = db.get(JobRun, job_id)
    if job is None or job.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status not in {JobStatus.QUEUED, JobStatus.FAILED}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only queued or failed jobs can be enqueued")
    dispatch_job(db, job=job)
    job.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(job)
    return job
