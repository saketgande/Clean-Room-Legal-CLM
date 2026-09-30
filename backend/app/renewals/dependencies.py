"""FastAPI-native dependency provider for the renewals module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.integrations.dependencies import get_resend_client
from app.integrations.resend import EmailSender
from app.renewals.service import RenewalsService


def get_renewals_service(
    db: Session = Depends(get_db),
    resend: EmailSender = Depends(get_resend_client),
) -> RenewalsService:
    return RenewalsService(db, resend=resend)
