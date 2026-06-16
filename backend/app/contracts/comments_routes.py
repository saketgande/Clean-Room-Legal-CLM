from datetime import datetime

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.contracts.comments_service import (
    create_comment,
    delete_comment,
    list_comments,
    set_resolved,
)
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission

router = APIRouter(prefix="/contracts", tags=["comments"])


class CommentCreate(BaseModel):
    body: str
    visibility: str = "internal"  # internal | shared
    contract_version_id: str | None = None
    parent_comment_id: str | None = None
    anchor: dict | None = None
    mentioned_user_ids: list[str] = Field(default_factory=list)


class CommentResolveRequest(BaseModel):
    resolved: bool = True


class CommentResponse(BaseModel):
    id: str
    contract_id: str
    contract_version_id: str | None
    parent_comment_id: str | None
    visibility: str
    author_kind: str
    author_user_id: str | None
    author_name: str
    body: str
    anchor: dict | None
    mentioned_user_ids: list[str]
    resolved: bool
    resolved_at: datetime | None
    created_at: datetime


@router.get("/{contract_id}/comments", response_model=list[CommentResponse])
def list_contract_comments(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return list_comments(db, contract=contract)


@router.post(
    "/{contract_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED
)
def add_contract_comment(
    contract_id: str,
    payload: CommentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return create_comment(
        db,
        contract=contract,
        user=current_user,
        body=payload.body,
        visibility=payload.visibility,
        contract_version_id=payload.contract_version_id,
        parent_comment_id=payload.parent_comment_id,
        anchor=payload.anchor,
        mentioned_user_ids=payload.mentioned_user_ids,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{contract_id}/comments/{comment_id}/resolve", response_model=CommentResponse)
def resolve_contract_comment(
    contract_id: str,
    comment_id: str,
    payload: CommentResolveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return set_resolved(
        db, contract=contract, user=current_user, comment_id=comment_id, resolved=payload.resolved
    )


@router.delete("/{contract_id}/comments/{comment_id}")
def remove_contract_comment(
    contract_id: str,
    comment_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return delete_comment(db, contract=contract, user=current_user, comment_id=comment_id)
