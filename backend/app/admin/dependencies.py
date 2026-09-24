"""FastAPI-native dependency provider for the admin module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.admin.service import AdminService
from app.core.deps import get_db


def get_admin_service(db: Session = Depends(get_db)) -> AdminService:
    return AdminService(db)
