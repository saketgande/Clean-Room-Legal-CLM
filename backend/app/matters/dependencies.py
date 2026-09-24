"""FastAPI-native dependency provider for the matters module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.matters.service import MattersService


def get_matters_service(db: Session = Depends(get_db)) -> MattersService:
    return MattersService(db)
