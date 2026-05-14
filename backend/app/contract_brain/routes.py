from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.contract_brain.models import BrainQuery
from app.core.deps import get_db, require_permission

router = APIRouter(prefix="/contract-brain", tags=["contract-brain"])


class BrainAskRequest(BaseModel):
    question: str
    query_scope: str = "portfolio"
    contract_id: str | None = None
    project_id: str | None = None


@router.post("/ask")
def ask_contract_brain(
    payload: BrainAskRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    query = BrainQuery(
        org_id=current_user.org_id,
        query_scope=payload.query_scope,
        question=payload.question,
        contract_id=payload.contract_id,
        project_id=payload.project_id,
        answer=None,
        citations=[],
        retrieval_metadata={"status": "queued_for_retrieval"},
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(query)
    db.commit()
    db.refresh(query)
    return query
