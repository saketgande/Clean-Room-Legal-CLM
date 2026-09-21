"""FastAPI-native dependency provider for the walls module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.walls.service import WallService


def get_wall_service(db: Session = Depends(get_db)) -> WallService:
    return WallService(db)
