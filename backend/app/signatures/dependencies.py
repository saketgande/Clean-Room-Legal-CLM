"""FastAPI-native dependency provider for the signatures module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.integrations.dependencies import get_docusign_client, get_resend_client, get_storage_service
from app.integrations.docusign import SignatureProvider
from app.integrations.resend import EmailSender
from app.integrations.storage import StorageBackend
from app.signatures.service import SignaturesService


def get_signatures_service(
    db: Session = Depends(get_db),
    docusign: SignatureProvider = Depends(get_docusign_client),
    storage: StorageBackend = Depends(get_storage_service),
    resend: EmailSender = Depends(get_resend_client),
) -> SignaturesService:
    return SignaturesService(db, docusign=docusign, storage=storage, resend=resend)
