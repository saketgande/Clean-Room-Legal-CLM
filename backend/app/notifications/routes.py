from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.notifications.models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _mine(current_user):
    return (
        Notification.org_id == current_user.org_id,
        Notification.user_id == current_user.id,
    )


@router.get("")
def list_notifications(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return db.scalars(
        select(Notification)
        .where(*_mine(current_user))
        .order_by(Notification.created_at.desc())
        .limit(100)
    ).all()


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    count = db.scalar(
        select(func.count(Notification.id)).where(
            *_mine(current_user), Notification.read_at.is_(None)
        )
    )
    return {"count": count or 0}


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = db.execute(
        update(Notification)
        .where(*_mine(current_user), Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
    return {"marked": result.rowcount or 0}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id, *_mine(current_user)
        )
    )
    if notification is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        db.commit()
    return notification
