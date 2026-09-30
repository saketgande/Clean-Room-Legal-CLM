"""FastAPI-native dependency provider for the prompt_library module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.prompt_library.service import PromptLibraryService


def get_prompt_library_service(db: Session = Depends(get_db)) -> PromptLibraryService:
    return PromptLibraryService(db)
