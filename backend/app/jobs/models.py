from sqlalchemy import Column, DateTime, Integer, JSON, String, Text

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
    job_type = Column(String(160), index=True, nullable=False)
    resource_type = Column(String(120), index=True, nullable=False)
    resource_id = Column(String(36), index=True, nullable=False)
    idempotency_key = Column(String(255), unique=True, index=True, nullable=True)
    status = Column(String(40), index=True, nullable=False, default=JobStatus.QUEUED)
    progress = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    error_stack = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    celery_task_id = Column(String(255), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
