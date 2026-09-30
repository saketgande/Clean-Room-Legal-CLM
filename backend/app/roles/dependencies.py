"""FastAPI-native dependency provider for the roles module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.roles.service import RoleService


def get_role_service(db: Session = Depends(get_db)) -> RoleService:
    return RoleService(db)
