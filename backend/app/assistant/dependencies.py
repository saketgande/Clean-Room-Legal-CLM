"""FastAPI-native dependency provider for the assistant module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.assistant.service import AssistantService
from app.core.deps import get_db


def get_assistant_service(db: Session = Depends(get_db)) -> AssistantService:
    return AssistantService(db)
