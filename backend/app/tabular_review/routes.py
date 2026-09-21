from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.access import is_org_admin
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import TabularCellStatus
from app.matters.access import get_project_for_user
from app.tabular_review.dependencies import get_tabular_review_service
from app.tabular_review.models import (
    TabularReview,
    TabularReviewCell,
)
from app.tabular_review.service import TabularReviewService

router = APIRouter(prefix="/tabular-reviews", tags=["tabular-reviews"])

# A review whose cells have not all finished within this window is treated
# as stuck (e.g. a worker died) so it can resolve instead of showing
# "Running" forever.
STUCK_REVIEW_TTL = timedelta(minutes=20)
_TERMINAL_CELL = {
    TabularCellStatus.COMPLETE,
    TabularCellStatus.NEEDS_REVIEW,
    TabularCellStatus.FAILED,
}
_ACTIVE_REVIEW_STATUSES = {"running", "pending", "draft"}


def _reconcile_review_status(db: Session, *, review: TabularReview) -> bool:
    """Derive a review's status from its cells.

    Cells are processed by independent Celery jobs and nothing transitions
    the parent review off ``running`` on its own, so a review whose worker
    died would otherwise display "Running" forever. This also reaps cells
    stuck past the TTL so the run can resolve and individual cells re-run.
    Returns True if the review row was mutated (caller commits).
    """
    if review.status not in _ACTIVE_REVIEW_STATUSES:
        return False
    cells = db.scalars(
        select(TabularReviewCell).where(
            TabularReviewCell.org_id == review.org_id,
            TabularReviewCell.tabular_review_id == review.id,
        )
    ).all()
    if not cells:
        return False
    pending = [c for c in cells if c.status not in _TERMINAL_CELL]
    changed = False
    if pending:
        last_active = review.updated_at or review.created_at
        if utcnow() - last_active < STUCK_REVIEW_TTL:
            if review.status != "running":
                review.status = "running"
                return True
            return False
        for cell in pending:
            cell.status = TabularCellStatus.FAILED
            cell.error_message = (
                "Timed out — no result within the expected window "
                "(the worker may have stopped). Re-run this cell to retry."
            )
        changed = True
    answered = any(
        c.status in {TabularCellStatus.COMPLETE, TabularCellStatus.NEEDS_REVIEW}
        for c in cells
    )
    new_status = "completed" if answered else "failed"
    if review.status != new_status:
        review.status = new_status
        changed = True
    return changed


class TabularColumnCreate(BaseModel):
    name: str
    prompt: str = Field(min_length=3)


class TabularReviewCreate(BaseModel):
    name: str
    matter_id: str | None = None
    contract_ids: list[str] = Field(default_factory=list)
    columns: list[TabularColumnCreate] = Field(min_length=1)


class TabularColumnsAdd(BaseModel):
    columns: list[TabularColumnCreate] = Field(min_length=1)


class TabularContractsAdd(BaseModel):
    contract_ids: list[str] = Field(min_length=1)


class TabularChatRequest(BaseModel):
    message: str = Field(min_length=2)


def _get_review_for_user(db: Session, *, review_id: str, current_user) -> TabularReview:
    review = db.get(TabularReview, review_id)
    if review is None or review.org_id != current_user.org_id or review.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tabular review not found")
    if not _review_is_accessible(db, review=review, current_user=current_user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tabular review not found")
    return review


def _review_is_accessible(
    db: Session,
    *,
    review: TabularReview,
    current_user,
    accessible_contract_ids: set[str] | None = None,
) -> bool:
    if is_org_admin(current_user) or review.created_by_user_id == current_user.id:
        return True
    if review.matter_id:
        try:
            get_project_for_user(db, matter_id=review.matter_id, user=current_user)
        except HTTPException:
            return False
        return True
    if accessible_contract_ids is not None:
        # Batched fast path: caller already resolved the user's full
        # accessible-contract set in one query (see list_reviews) instead of
        # one get_contract_for_user() call per contract per review.
        return bool(review.source_contract_ids) and set(
            review.source_contract_ids
        ).issubset(accessible_contract_ids)
    for contract_id in review.source_contract_ids or []:
        try:
            get_contract_for_user(db, contract_id=contract_id, user=current_user)
        except HTTPException:
            return False
    return bool(review.source_contract_ids)


@router.get("")
def list_reviews(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    reviews = db.scalars(
        select(TabularReview).where(
            TabularReview.org_id == current_user.org_id,
            TabularReview.deleted_at.is_(None),
        )
    ).all()
    # Resolve the user's whole accessible-contract set ONCE (was previously
    # re-derived per contract per review — O(reviews x contracts) queries).
    accessible_contract_ids = set(
        db.scalars(
            select(Contract.id).where(
                Contract.org_id == current_user.org_id,
                accessible_contract_filter(current_user),
            )
        ).all()
    )
    visible = [
        review
        for review in reviews
        if _review_is_accessible(
            db,
            review=review,
            current_user=current_user,
            accessible_contract_ids=accessible_contract_ids,
        )
    ]
    mutated = [_reconcile_review_status(db, review=review) for review in visible]
    if any(mutated):
        db.commit()
    return visible


@router.post("", status_code=status.HTTP_201_CREATED)
def create_review(
    payload: TabularReviewCreate,
    current_user=Depends(require_permission("assistant:use_ai_tools")),
    service: TabularReviewService = Depends(get_tabular_review_service),
):
    return service.create_review(payload=payload, current_user=current_user)


@router.post("/{review_id}/columns", status_code=status.HTTP_201_CREATED)
def add_columns(
    review_id: str,
    payload: TabularColumnsAdd,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use_ai_tools")),
    service: TabularReviewService = Depends(get_tabular_review_service),
):
    """Add columns to an existing review and back-fill only the new cells.

    Existing answers are never re-run — only the new (contract x column)
    cells are created and dispatched, mirroring the reference behaviour.
    """
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    return service.add_columns(review=review, payload=payload, current_user=current_user)


@router.post("/{review_id}/contracts", status_code=status.HTTP_201_CREATED)
def add_contracts(
    review_id: str,
    payload: TabularContractsAdd,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use_ai_tools")),
    service: TabularReviewService = Depends(get_tabular_review_service),
):
    """Add contracts (files) to an existing review and back-fill new cells."""
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    return service.add_contracts(review=review, payload=payload, current_user=current_user)


@router.get("/{review_id}")
def get_review(
    review_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
    service: TabularReviewService = Depends(get_tabular_review_service),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    if _reconcile_review_status(db, review=review):
        db.commit()
        db.refresh(review)
    return service.review_payload(review=review, org_id=current_user.org_id)


@router.post("/{review_id}/cells/{cell_id}/rerun")
def rerun_cell(
    review_id: str,
    cell_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use_ai_tools")),
    service: TabularReviewService = Depends(get_tabular_review_service),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    return service.rerun_cell(review=review, cell_id=cell_id, current_user=current_user)


@router.get("/{review_id}/chat")
def list_chat(
    review_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
    service: TabularReviewService = Depends(get_tabular_review_service),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    return service.list_chat(review=review, current_user=current_user)


@router.post("/{review_id}/chat")
async def chat_over_table(
    review_id: str,
    payload: TabularChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
    service: TabularReviewService = Depends(get_tabular_review_service),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    return await service.chat_over_table(
        review=review,
        message=payload.message,
        current_user=current_user,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/{review_id}/export")
def export_review_xlsx(
    review_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
    service: TabularReviewService = Depends(get_tabular_review_service),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    content = service.build_xlsx(review=review, org_id=current_user.org_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="tabular-review-{review.id}.xlsx"'},
    )
