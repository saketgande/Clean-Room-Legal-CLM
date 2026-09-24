"""FastAPI-native dependency provider for the grants module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.grants.service import GrantService


def get_grant_service(db: Session = Depends(get_db)) -> GrantService:
    return GrantService(db)
