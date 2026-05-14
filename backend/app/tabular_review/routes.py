from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.tabular_review.models import TabularReview, TabularReviewCell, TabularReviewColumn

router = APIRouter(prefix="/tabular-reviews", tags=["tabular-reviews"])


class TabularColumnCreate(BaseModel):
    name: str
    prompt: str


class TabularReviewCreate(BaseModel):
    name: str
    project_id: str | None = None
    contract_ids: list[str]
    columns: list[TabularColumnCreate]


@router.get("")
def list_reviews(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    return db.scalars(
        select(TabularReview).where(
            TabularReview.org_id == current_user.org_id,
            TabularReview.deleted_at.is_(None),
        )
    ).all()


@router.post("")
def create_review(
    payload: TabularReviewCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use_ai_tools")),
):
    review = TabularReview(
        org_id=current_user.org_id,
        name=payload.name,
        project_id=payload.project_id,
        source_contract_ids=payload.contract_ids,
        status="pending",
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
    for contract_id in payload.contract_ids:
        for column in columns:
            db.add(
                TabularReviewCell(
                    org_id=current_user.org_id,
                    tabular_review_id=review.id,
                    column_id=column.id,
                    contract_id=contract_id,
                    created_by_user_id=current_user.id,
                    updated_by_user_id=current_user.id,
                )
            )
    db.commit()
    db.refresh(review)
    return review
