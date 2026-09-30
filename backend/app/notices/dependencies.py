"""FastAPI-native dependency provider for the notices module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.notices.service import NoticesService


def get_notices_service(db: Session = Depends(get_db)) -> NoticesService:
    return NoticesService(db)
