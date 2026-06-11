from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_current_user
from app.notifications.models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return db.scalars(
        select(Notification)
        .where(Notification.org_id == current_user.org_id, Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
    ).all()
