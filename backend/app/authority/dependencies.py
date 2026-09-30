"""FastAPI-native dependency provider for the authority module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.authority.service import AuthorityService
from app.core.deps import get_db


def get_authority_service(db: Session = Depends(get_db)) -> AuthorityService:
    return AuthorityService(db)
