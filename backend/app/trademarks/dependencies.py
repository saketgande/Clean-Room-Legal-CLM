"""FastAPI-native dependency provider for the trademarks module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.integrations.claude import ClaudeProvider
from app.integrations.dependencies import get_claude_client, get_reducto_client, get_storage_service
from app.integrations.reducto import ReductoClient
from app.integrations.storage import StorageBackend
from app.trademarks.service import TrademarksService


def get_trademarks_service(
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_service),
    reducto: ReductoClient = Depends(get_reducto_client),
    claude_client: ClaudeProvider = Depends(get_claude_client),
) -> TrademarksService:
    return TrademarksService(db, storage=storage, reducto=reducto, claude_client=claude_client)
