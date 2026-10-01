from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, UniqueConstraint

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import JobStatus


class JobRun(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    __table_args__ = (
        # Keys are looked up per org (jobs.service.create_job), so uniqueness is scoped
        # the same way; the old global index made two orgs' identical keys collide.
        UniqueConstraint("org_id", "idempotency_key", name="uq_job_run_org_idempotency_key"),
    )

    job_type = Column(String(160), index=True, nullable=False)
    resource_type = Column(String(120), index=True, nullable=False)
    resource_id = Column(String(36), index=True, nullable=False)
    idempotency_key = Column(String(255), index=True, nullable=True)
    status = Column(String(40), index=True, nullable=False, default=JobStatus.QUEUED)
    progress = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    error_stack = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    celery_task_id = Column(String(255), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
