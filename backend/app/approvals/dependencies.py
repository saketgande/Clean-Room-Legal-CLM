"""FastAPI-native dependency provider for the approvals module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.approvals.service import ApprovalsService
from app.core.deps import get_db
from app.integrations.dependencies import get_resend_client
from app.integrations.resend import EmailSender


def get_approvals_service(
    db: Session = Depends(get_db),
    resend: EmailSender = Depends(get_resend_client),
) -> ApprovalsService:
    return ApprovalsService(db, resend=resend)
