"""FastAPI-native dependency provider for the playbooks module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.integrations.claude import ClaudeProvider
from app.integrations.dependencies import get_claude_client
from app.playbooks.service import PlaybooksService


def get_playbooks_service(
    db: Session = Depends(get_db),
    claude_client: ClaudeProvider = Depends(get_claude_client),
) -> PlaybooksService:
    return PlaybooksService(db, claude_client=claude_client)
