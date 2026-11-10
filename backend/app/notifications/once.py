"""An in-app notification that is written at most once for a given thing.

A sweep that runs every day must not tell the same person about the same problem every day. `notify_once` looks for an
earlier notification to the same person, of the same kind, carrying the same key, and writes one only when there is none.
Other code writes `Notification` rows directly; they are left as they are.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.notifications.models import Notification

_SUBJECT_MAX = 500  # the column's width: a long contract title would otherwise fail the insert, every day


def already_notified(db: Session, *, org_id: str, user_id: str, event_type: str, key: dict[str, str]) -> bool:
    """Whether this person was already sent this kind of notification for this key (every pair in `key` must match)."""
    found = select(Notification.id).where(
        Notification.org_id == org_id, Notification.user_id == user_id, Notification.event_type == event_type
    )
    for name, value in key.items():
        found = found.where(Notification.metadata_json[name].as_string() == value)
    return db.scalar(found.limit(1)) is not None


def notify_once(
    db: Session, *, org_id: str, user_id: str, event_type: str, subject: str, body: str, key: dict[str, str]
) -> bool:
    """Write the notification unless one with the same (user, event type, key) exists. True when it was written.

    `key` is stored as the notification's metadata and is what makes two notifications "the same thing": every pair in it
    must match. The caller holds whatever lock stops two runs checking at once (a per-contract advisory lock for
    per-contract keys; ponytail: a per-day key such as a digest relies on one sweep running at a time)."""
    if already_notified(db, org_id=org_id, user_id=user_id, event_type=event_type, key=key):
        return False
    db.add(Notification(
        org_id=org_id, user_id=user_id, channel="in_app", event_type=event_type, subject=subject[:_SUBJECT_MAX],
        body=body, status="sent", metadata_json=dict(key),
    ))
    db.flush()  # autoflush is off: a second call in the same session must see this row
    return True
