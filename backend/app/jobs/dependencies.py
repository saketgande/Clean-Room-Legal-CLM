"""FastAPI-native dependency provider for the jobs module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.jobs.service import JobsService


def get_jobs_service(db: Session = Depends(get_db)) -> JobsService:
    return JobsService(db)
