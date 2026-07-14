from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.comments_models import ContractComment
from app.contracts.models import Contract
from app.core.audit import write_audit_log
from app.core.rbac import has_permission
from app.notifications.models import Notification

_VISIBILITIES = {"internal", "shared"}


def _name_map(db: Session, org_id: str) -> dict[str, str]:
    return {
        u.id: (u.full_name or u.email)
        for u in db.scalars(select(User).where(User.org_id == org_id)).all()
    }


def _serialize(comment: ContractComment, *, names: dict[str, str]) -> dict:
    return {
        "id": comment.id,
        "contract_id": comment.contract_id,
        "contract_version_id": comment.contract_version_id,
        "parent_comment_id": comment.parent_comment_id,
        "visibility": comment.visibility,
        "author_kind": comment.author_kind,
        "author_user_id": comment.author_user_id,
        "author_name": comment.author_label
        or names.get(comment.author_user_id or "")
        or "Unknown",
        "body": comment.body,
        "anchor": comment.anchor,
        "mentioned_user_ids": comment.mentioned_user_ids or [],
        "resolved": comment.resolved_at is not None,
        "resolved_at": comment.resolved_at,
        "created_at": comment.created_at,
    }


def list_comments(db: Session, *, contract: Contract, include_resolved: bool = True) -> list[dict]:
    rows = db.scalars(
        select(ContractComment)
        .where(
            ContractComment.org_id == contract.org_id,
            ContractComment.contract_id == contract.id,
            ContractComment.deleted_at.is_(None),
        )
        .order_by(ContractComment.created_at.asc())
    ).all()
    if not include_resolved:
        rows = [r for r in rows if r.resolved_at is None]
    names = _name_map(db, contract.org_id)
    return [_serialize(r, names=names) for r in rows]


def create_comment(
    db: Session,
    *,
    contract: Contract,
    user: User,
    body: str,
    visibility: str = "internal",
    contract_version_id: str | None = None,
    parent_comment_id: str | None = None,
    anchor: dict | None = None,
    mentioned_user_ids: list[str] | None = None,
    request_id: str | None = None,
) -> dict:
    if not body or not body.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Comment body is required")
    if visibility not in _VISIBILITIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "visibility must be 'internal' or 'shared'"
        )
    # Keep only mentions that belong to this org.
    valid_mentions: list[str] = []
    for uid in dict.fromkeys(mentioned_user_ids or []):
        member = db.get(User, uid)
        if member is not None and member.org_id == contract.org_id:
            valid_mentions.append(uid)

    comment = ContractComment(
        org_id=contract.org_id,
        contract_id=contract.id,
        contract_version_id=contract_version_id,
        parent_comment_id=parent_comment_id,
        visibility=visibility,
        author_kind="user",
        author_user_id=user.id,
        body=body.strip(),
        anchor=anchor,
        mentioned_user_ids=valid_mentions,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    db.add(comment)
    db.flush()

    # @mention → in-app notification (no email, to avoid negotiation noise).
    for uid in valid_mentions:
        if uid == user.id:
            continue
        db.add(
            Notification(
                org_id=contract.org_id,
                user_id=uid,
                channel="in_app",
                event_type="contract.comment.mention",
                subject=f"{user.full_name or user.email} mentioned you",
                body=f"on “{contract.title}”: {comment.body[:140]}",
                status="sent",  # in-app is delivered on write; nothing drains a queue
                metadata_json={"contract_id": contract.id, "comment_id": comment.id},
                created_by_user_id=user.id,
                updated_by_user_id=user.id,
            )
        )

    write_audit_log(
        db,
        action="contract.comment.created",
        resource_type="contract_comment",
        resource_id=comment.id,
        org_id=contract.org_id,
        actor_user_id=user.id,
        request_id=request_id,
        after={"contract_id": contract.id, "visibility": visibility},
    )
    db.commit()
    db.refresh(comment)
    return _serialize(comment, names=_name_map(db, contract.org_id))


# --- Counterparty (external share) side ----------------------------------
def list_shared_comments(db: Session, *, contract: Contract) -> list[dict]:
    """Only the comments marked visibility='shared' — what the counterparty may
    see through a share link. Internal comments are never returned here."""
    rows = db.scalars(
        select(ContractComment)
        .where(
            ContractComment.org_id == contract.org_id,
            ContractComment.contract_id == contract.id,
            ContractComment.visibility == "shared",
            ContractComment.deleted_at.is_(None),
        )
        .order_by(ContractComment.created_at.asc())
    ).all()
    names = _name_map(db, contract.org_id)
    return [_serialize(r, names=names) for r in rows]


def add_counterparty_comment(
    db: Session,
    *,
    contract: Contract,
    author_name: str | None,
    body: str,
    request_id: str | None = None,
) -> dict:
    """A comment left by the counterparty via a share link (no account). Always
    'shared'; notifies the contract owner in-app."""
    if not body or not body.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Comment body is required")
    comment = ContractComment(
        org_id=contract.org_id,
        contract_id=contract.id,
        visibility="shared",
        author_kind="counterparty",
        author_user_id=None,
        author_label=(author_name or "Counterparty").strip()[:255] or "Counterparty",
        body=body.strip(),
        mentioned_user_ids=[],
    )
    db.add(comment)
    db.flush()
    if contract.owner_user_id:
        db.add(
            Notification(
                org_id=contract.org_id,
                user_id=contract.owner_user_id,
                channel="in_app",
                event_type="contract.comment.counterparty",
                subject=f"Counterparty commented on “{contract.title}”",
                body=comment.body[:140],
                status="sent",  # in-app is delivered on write; nothing drains a queue
                metadata_json={"contract_id": contract.id, "comment_id": comment.id},
            )
        )
    write_audit_log(
        db,
        action="contract.comment.counterparty_added",
        resource_type="contract_comment",
        resource_id=comment.id,
        org_id=contract.org_id,
        actor_user_id=None,
        request_id=request_id,
        after={"contract_id": contract.id},
    )
    db.commit()
    db.refresh(comment)
    return _serialize(comment, names=_name_map(db, contract.org_id))


def _get_owned(db: Session, *, contract: Contract, comment_id: str) -> ContractComment:
    comment = db.get(ContractComment, comment_id)
    if (
        comment is None
        or comment.org_id != contract.org_id
        or comment.contract_id != contract.id
        or comment.deleted_at is not None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    return comment


def set_resolved(
    db: Session, *, contract: Contract, user: User, comment_id: str, resolved: bool
) -> dict:
    comment = _get_owned(db, contract=contract, comment_id=comment_id)
    comment.resolved_at = datetime.now(UTC) if resolved else None
    comment.resolved_by_user_id = user.id if resolved else None
    comment.updated_by_user_id = user.id
    db.commit()
    db.refresh(comment)
    return _serialize(comment, names=_name_map(db, contract.org_id))


def delete_comment(db: Session, *, contract: Contract, user: User, comment_id: str) -> dict:
    comment = _get_owned(db, contract=contract, comment_id=comment_id)
    # Author or anyone who can edit the contract may delete.
    if comment.author_user_id != user.id and not has_permission(
        user.permission_values, "contract:update"
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the author or an editor can delete this comment"
        )
    comment.deleted_at = datetime.now(UTC)
    comment.deleted_by_user_id = user.id
    comment.updated_by_user_id = user.id
    db.commit()
    return {"status": "deleted", "id": comment_id}
