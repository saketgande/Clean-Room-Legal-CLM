"""FastAPI-native dependency provider for the search module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.search.service import SearchService


def get_search_service(db: Session = Depends(get_db)) -> SearchService:
    return SearchService(db)
