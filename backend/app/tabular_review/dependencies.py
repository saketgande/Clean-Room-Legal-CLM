"""FastAPI-native dependency provider for the tabular_review module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.tabular_review.service import TabularReviewService


def get_tabular_review_service(db: Session = Depends(get_db)) -> TabularReviewService:
    return TabularReviewService(db)
