from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.deps import require_permission
from app.trademarks.comments_service import TrademarkCommentService
from app.trademarks.dependencies import get_trademark_comment_service

router = APIRouter(prefix="/trademarks", tags=["trademark-comments"])


class TrademarkCommentCreate(BaseModel):
    body: str
    parent_comment_id: str | None = None


class TrademarkCommentResponse(BaseModel):
    id: str
    trademark_id: str
    parent_comment_id: str | None
    author_user_id: str | None
    author_name: str
    body: str
    created_at: datetime


# Registered before /{trademark_id}/comments — not strictly required (FastAPI
# matches by segment count, and this path has one more segment than that
# route's catch-all), but keeping the bulk route first reads unambiguously.
@router.get("/comments/counts", response_model=dict[str, int])
def get_comment_counts(
    service: TrademarkCommentService = Depends(get_trademark_comment_service),
    current_user=Depends(require_permission("trademark:read")),
):
    """One {trademark_id: count} map for every trademark in the org with at
    least one comment — backs the chat-icon badge on every row in My
    Trademarks without an N+1 request per row."""
    return service.get_comment_counts(org_id=current_user.org_id)


@router.get("/{trademark_id}/comments", response_model=list[TrademarkCommentResponse])
def list_trademark_comments(
    trademark_id: str,
    service: TrademarkCommentService = Depends(get_trademark_comment_service),
    current_user=Depends(require_permission("trademark:read")),
):
    return service.list_comments(trademark_id=trademark_id, user=current_user)


@router.post("/{trademark_id}/comments", response_model=TrademarkCommentResponse, status_code=201)
def add_trademark_comment(
    trademark_id: str,
    payload: TrademarkCommentCreate,
    request: Request,
    service: TrademarkCommentService = Depends(get_trademark_comment_service),
    current_user=Depends(require_permission("trademark:create")),
):
    return service.create_comment(
        trademark_id=trademark_id,
        user=current_user,
        body=payload.body,
        parent_comment_id=payload.parent_comment_id,
        request_id=getattr(request.state, "request_id", None),
    )
