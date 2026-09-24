"""FastAPI-native dependency provider for the word_addin module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.word_addin.service import WordAddinService


def get_word_addin_service(db: Session = Depends(get_db)) -> WordAddinService:
    return WordAddinService(db)
