"""FastAPI-native dependency provider for the organizations module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.organizations.service import OrganizationsService


def get_organizations_service(db: Session = Depends(get_db)) -> OrganizationsService:
    return OrganizationsService(db)
