import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.citations import validate_citation
from app.ai.controller import ai_controller
from app.ai.schemas import CitationInput, TabularChatOutput
from app.contracts.service import get_contract_for_user
from app.core.access import is_org_admin
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import TabularCellStatus
from app.projects.access import get_project_for_user
from app.projects.models import ProjectContract
from app.tabular_review.models import (
    TabularReview,
    TabularReviewCell,
    TabularReviewChat,
    TabularReviewColumn,
)
from app.tabular_review.service import build_table_context, build_xlsx, dispatch_cells

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
    project_id: str | None = None
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


def _review_is_accessible(db: Session, *, review: TabularReview, current_user) -> bool:
    if is_org_admin(current_user) or review.created_by_user_id == current_user.id:
        return True
    if review.project_id:
        try:
            get_project_for_user(db, project_id=review.project_id, user=current_user)
        except HTTPException:
            return False
        return True
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
    visible = [
        review
        for review in reviews
        if _review_is_accessible(db, review=review, current_user=current_user)
    ]
    mutated = [_reconcile_review_status(db, review=review) for review in visible]
    if any(mutated):
        db.commit()
    return visible


@router.post("", status_code=status.HTTP_201_CREATED)
def create_review(
    payload: TabularReviewCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use_ai_tools")),
):
    contract_ids = list(dict.fromkeys(payload.contract_ids))
    if payload.project_id:
        get_project_for_user(db, project_id=payload.project_id, user=current_user)
        if not contract_ids:
            contract_ids = list(
                db.scalars(
                    select(ProjectContract.contract_id).where(
                        ProjectContract.org_id == current_user.org_id,
                        ProjectContract.project_id == payload.project_id,
                    )
                ).all()
            )
    if not contract_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No contracts selected")
    # Access-check every row contract.
    for cid in contract_ids:
        get_contract_for_user(db, contract_id=cid, user=current_user)

    review = TabularReview(
        org_id=current_user.org_id,
        name=payload.name,
        project_id=payload.project_id,
        source_contract_ids=contract_ids,
        status="running",
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(review)
    db.flush()
    columns = []
    for position, column_payload in enumerate(payload.columns):
        column = TabularReviewColumn(
            org_id=current_user.org_id,
            tabular_review_id=review.id,
            name=column_payload.name,
            prompt=column_payload.prompt,
            position=position,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(column)
        db.flush()
        columns.append(column)
    cells = []
    for contract_id in contract_ids:
        for column in columns:
            cell = TabularReviewCell(
                org_id=current_user.org_id,
                tabular_review_id=review.id,
                column_id=column.id,
                contract_id=contract_id,
                status=TabularCellStatus.PENDING,
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
            db.add(cell)
            db.flush()
            cells.append(cell)
    db.commit()
    dispatch_cells(db, user=current_user, review=review, cells=cells)
    db.refresh(review)
    return review


def _review_payload(db: Session, *, review: TabularReview, org_id: str) -> dict:
    columns = db.scalars(
        select(TabularReviewColumn)
        .where(TabularReviewColumn.tabular_review_id == review.id)
        .order_by(TabularReviewColumn.position.asc())
    ).all()
    cells = db.scalars(
        select(TabularReviewCell).where(
            TabularReviewCell.org_id == org_id,
            TabularReviewCell.tabular_review_id == review.id,
        )
    ).all()
    return {"review": review, "columns": columns, "cells": cells}


@router.post("/{review_id}/columns", status_code=status.HTTP_201_CREATED)
def add_columns(
    review_id: str,
    payload: TabularColumnsAdd,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use_ai_tools")),
):
    """Add columns to an existing review and back-fill only the new cells.

    Existing answers are never re-run — only the new (contract x column)
    cells are created and dispatched, mirroring the reference behaviour.
    """
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    contract_ids: list[str] = list(review.source_contract_ids or [])
    next_pos = db.scalar(
        select(func.max(TabularReviewColumn.position)).where(
            TabularReviewColumn.tabular_review_id == review.id
        )
    )
    next_pos = (next_pos + 1) if next_pos is not None else 0

    new_columns = []
    for offset, col in enumerate(payload.columns):
        column = TabularReviewColumn(
            org_id=current_user.org_id,
            tabular_review_id=review.id,
            name=col.name,
            prompt=col.prompt,
            position=next_pos + offset,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(column)
        db.flush()
        new_columns.append(column)

    cells = []
    for contract_id in contract_ids:
        for column in new_columns:
            cell = TabularReviewCell(
                org_id=current_user.org_id,
                tabular_review_id=review.id,
                column_id=column.id,
                contract_id=contract_id,
                status=TabularCellStatus.PENDING,
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
            db.add(cell)
            db.flush()
            cells.append(cell)
    if cells:
        review.status = "running"
    review.updated_by_user_id = current_user.id
    db.commit()
    if cells:
        dispatch_cells(db, user=current_user, review=review, cells=cells)
    db.refresh(review)
    return _review_payload(db, review=review, org_id=current_user.org_id)


@router.post("/{review_id}/contracts", status_code=status.HTTP_201_CREATED)
def add_contracts(
    review_id: str,
    payload: TabularContractsAdd,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use_ai_tools")),
):
    """Add contracts (files) to an existing review and back-fill new cells."""
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    existing = list(review.source_contract_ids or [])
    existing_set = set(existing)
    new_contract_ids = [
        cid
        for cid in dict.fromkeys(payload.contract_ids)
        if cid not in existing_set
    ]
    if not new_contract_ids:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Those contracts are already in this review",
        )
    for cid in new_contract_ids:
        get_contract_for_user(db, contract_id=cid, user=current_user)

    columns = db.scalars(
        select(TabularReviewColumn)
        .where(TabularReviewColumn.tabular_review_id == review.id)
        .order_by(TabularReviewColumn.position.asc())
    ).all()

    cells = []
    for contract_id in new_contract_ids:
        for column in columns:
            cell = TabularReviewCell(
                org_id=current_user.org_id,
                tabular_review_id=review.id,
                column_id=column.id,
                contract_id=contract_id,
                status=TabularCellStatus.PENDING,
                created_by_user_id=current_user.id,
                updated_by_user_id=current_user.id,
            )
            db.add(cell)
            db.flush()
            cells.append(cell)

    review.source_contract_ids = existing + new_contract_ids
    if cells:
        review.status = "running"
    review.updated_by_user_id = current_user.id
    db.commit()
    if cells:
        dispatch_cells(db, user=current_user, review=review, cells=cells)
    db.refresh(review)
    return _review_payload(db, review=review, org_id=current_user.org_id)


@router.get("/{review_id}")
def get_review(
    review_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    if _reconcile_review_status(db, review=review):
        db.commit()
        db.refresh(review)
    columns = db.scalars(
        select(TabularReviewColumn)
        .where(TabularReviewColumn.tabular_review_id == review.id)
        .order_by(TabularReviewColumn.position.asc())
    ).all()
    cells = db.scalars(
        select(TabularReviewCell).where(
            TabularReviewCell.org_id == current_user.org_id,
            TabularReviewCell.tabular_review_id == review.id,
        )
    ).all()
    return {"review": review, "columns": columns, "cells": cells}


@router.post("/{review_id}/cells/{cell_id}/rerun")
def rerun_cell(
    review_id: str,
    cell_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use_ai_tools")),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    cell = db.get(TabularReviewCell, cell_id)
    if (
        cell is None
        or cell.org_id != current_user.org_id
        or cell.tabular_review_id != review.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tabular cell not found")
    get_contract_for_user(db, contract_id=cell.contract_id, user=current_user)
    cell.status = TabularCellStatus.PENDING
    cell.answer = None
    cell.reasoning = None
    cell.citations = None
    cell.error_message = None
    cell.updated_by_user_id = current_user.id
    # Re-open the parent run so it tracks the re-running cell again
    # (committing bumps updated_at, restarting the stuck-run TTL window).
    review.status = "running"
    review.updated_by_user_id = current_user.id
    db.commit()
    suffix = f":rerun:{int(time.time() * 1000)}"
    dispatch_cells(db, user=current_user, review=review, cells=[cell], suffix=suffix)
    db.refresh(cell)
    return cell


@router.get("/{review_id}/chat")
def list_chat(
    review_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    return db.scalars(
        select(TabularReviewChat)
        .where(
            TabularReviewChat.org_id == current_user.org_id,
            TabularReviewChat.tabular_review_id == review.id,
        )
        .order_by(TabularReviewChat.created_at.asc())
    ).all()


@router.post("/{review_id}/chat")
async def chat_over_table(
    review_id: str,
    payload: TabularChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    context_text = build_table_context(db, review=review, org_id=current_user.org_id)
    db.add(
        TabularReviewChat(
            org_id=current_user.org_id,
            tabular_review_id=review.id,
            role="user",
            content=payload.message,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
    )
    db.commit()
    output = await ai_controller.run_structured_skill(
        db,
        skill_name="tabular_review_chat",
        org_id=current_user.org_id,
        created_by_user_id=current_user.id,
        input_payload={"question": payload.message, "table_context": context_text},
        request_id=getattr(request.state, "request_id", None),
    )
    answer = output if isinstance(output, TabularChatOutput) else TabularChatOutput.model_validate(output)
    validated = []
    for c in answer.citations:
        result = validate_citation(CitationInput(quote=c.quote), context_text or "")
        validated.append(
            {"quote": c.quote, "validation_status": result.validation_status,
             "similarity_score": result.similarity_score}
        )
    message = TabularReviewChat(
        org_id=current_user.org_id,
        tabular_review_id=review.id,
        role="assistant",
        content=answer.answer,
        citations=validated,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.get("/{review_id}/export")
def export_review_xlsx(
    review_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    review = _get_review_for_user(db, review_id=review_id, current_user=current_user)
    content = build_xlsx(db, review=review, org_id=current_user.org_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="tabular-review-{review.id}.xlsx"'},
    )
