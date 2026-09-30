"""FastAPI-native dependency provider for the obligations module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.integrations.dependencies import get_resend_client
from app.integrations.resend import EmailSender
from app.obligations.service import ObligationsService


def get_obligations_service(
    db: Session = Depends(get_db),
    resend: EmailSender = Depends(get_resend_client),
) -> ObligationsService:
    return ObligationsService(db, resend=resend)
