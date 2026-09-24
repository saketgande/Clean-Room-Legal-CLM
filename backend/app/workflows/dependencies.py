"""FastAPI-native dependency provider for the workflows module.

Part of the DI migration (see backend/DI_MIGRATION.md).
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.workflows.service import WorkflowService


def get_workflow_service(db: Session = Depends(get_db)) -> WorkflowService:
    return WorkflowService(db)
