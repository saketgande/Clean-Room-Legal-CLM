"""Resource-grant service — the unified object-level access layer.

Grants are ADDITIVE and time-bound: a grant only ever *adds* access to one
object for one principal (user / role / group), within an optional validity
window and until revoked. It never subtracts (the deny-override layers —
ethical walls, clearance — come in Phase 3). Access-level ordering:

    read < comment < update < share < owner
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.models import Role, User
from app.core.access import is_org_admin
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.grants.models import ResourceGrant

LEVELS = ["read", "comment", "update", "share", "owner"]
PRINCIPAL_TYPES = {"user", "role", "group"}
RESOURCE_TYPES = {"contract", "project", "playbook"}


def _levels_at_least(min_level: str) -> list[str]:
    try:
        return LEVELS[LEVELS.index(min_level):]
    except ValueError:
        return LEVELS[:]


def _active_clause():
    """SQL predicate: grant is currently in force."""
    now = utcnow()
    return and_(
        ResourceGrant.revoked_at.is_(None),
        or_(ResourceGrant.valid_from.is_(None), ResourceGrant.valid_from <= now),
        or_(ResourceGrant.valid_until.is_(None), ResourceGrant.valid_until > now),
    )


def _principal_clause(user: User):
    """SQL predicate matching grants whose principal is this user, or any role
    the user holds. (Group principals resolve via ApproverGroup membership below,
    handled in user_has_grant for the row check; the list filter covers user+role
    which is the common case.)"""
    conds = [
        and_(ResourceGrant.principal_type == "user", ResourceGrant.principal_id == user.id)
    ]
    role_ids = [r.id for r in getattr(user, "roles", [])]
    if role_ids:
        conds.append(
            and_(ResourceGrant.principal_type == "role", ResourceGrant.principal_id.in_(role_ids))
        )
    return or_(*conds)


def granted_resource_ids(user: User, resource_type: str, min_level: str = "read"):
    """A SELECT of resource_ids the user has an active grant on at >= min_level.
    Use inside `.where(Model.id.in_(granted_resource_ids(...)))` filters."""
    return (
        select(ResourceGrant.resource_id)
        .where(
            ResourceGrant.resource_type == resource_type,
            ResourceGrant.access_level.in_(_levels_at_least(min_level)),
            _principal_clause(user),
            _active_clause(),
        )
    )


def user_has_grant(
    db: Session, *, user: User, resource_type: str, resource_id: str, min_level: str = "read"
) -> bool:
    """Row check: does the user have an active grant (direct, by role, or by
    approver-group membership) on this specific resource at >= min_level?"""
    direct = db.scalar(
        granted_resource_ids(user, resource_type, min_level)
        .where(ResourceGrant.resource_id == resource_id)
        .limit(1)
    )
    if direct is not None:
        return True
    # Group principals: any approver group the user belongs to.
    from app.approvals.models import ApproverGroup

    group_ids = [
        g.id
        for g in db.scalars(select(ApproverGroup).where(ApproverGroup.org_id == user.org_id)).all()
        if any(m.id == user.id for m in g.members)
    ]
    if not group_ids:
        return False
    return (
        db.scalar(
            select(ResourceGrant.id).where(
                ResourceGrant.resource_type == resource_type,
                ResourceGrant.resource_id == resource_id,
                ResourceGrant.principal_type == "group",
                ResourceGrant.principal_id.in_(group_ids),
                ResourceGrant.access_level.in_(_levels_at_least(min_level)),
                _active_clause(),
            ).limit(1)
        )
        is not None
    )


# --- resource ownership / management gate ---------------------------------

def _resource_owner_id(db: Session, resource_type: str, resource_id: str) -> str | None:
    if resource_type == "contract":
        from app.contracts.models import Contract

        c = db.get(Contract, resource_id)
        return c.owner_user_id if c else None
    if resource_type == "project":
        from app.matters.models import Matter

        p = db.get(Matter, resource_id)
        return p.owner_user_id if p else None
    if resource_type == "playbook":
        from app.playbooks.models import Playbook

        pb = db.get(Playbook, resource_id)
        return pb.created_by_user_id if pb else None
    return None


def can_manage_grants(db: Session, *, user: User, resource_type: str, resource_id: str) -> bool:
    """Who may grant/revoke access to a resource: an org admin, the resource's
    owner, or someone the resource is shared TO at 'share'/'owner' level."""
    if is_org_admin(user):
        return True
    if _resource_owner_id(db, resource_type, resource_id) == user.id:
        return True
    return user_has_grant(
        db, user=user, resource_type=resource_type, resource_id=resource_id, min_level="share"
    )


# --- serialization --------------------------------------------------------

def _principal_label(db: Session, principal_type: str, principal_id: str) -> str:
    if principal_type == "user":
        u = db.get(User, principal_id)
        return (u.full_name or u.email) if u else principal_id
    if principal_type == "role":
        r = db.get(Role, principal_id)
        return r.name if r else principal_id
    if principal_type == "group":
        from app.approvals.models import ApproverGroup

        g = db.get(ApproverGroup, principal_id)
        return g.name if g else principal_id
    return principal_id


def serialize_grant(db: Session, grant: ResourceGrant) -> dict:
    now = utcnow()
    active = grant.revoked_at is None and (
        grant.valid_until is None or grant.valid_until > now
    )
    return {
        "id": grant.id,
        "principal_type": grant.principal_type,
        "principal_id": grant.principal_id,
        "principal_label": _principal_label(db, grant.principal_type, grant.principal_id),
        "resource_type": grant.resource_type,
        "resource_id": grant.resource_id,
        "access_level": grant.access_level,
        "note": grant.note,
        "valid_until": grant.valid_until.isoformat() if grant.valid_until else None,
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
        "active": active,
        "created_at": grant.created_at.isoformat() if grant.created_at else None,
    }


# --- mutations ------------------------------------------------------------

def list_grants_for_resource(db: Session, *, org_id: str, resource_type: str, resource_id: str, include_revoked: bool = False) -> list[dict]:
    q = select(ResourceGrant).where(
        ResourceGrant.org_id == org_id,
        ResourceGrant.resource_type == resource_type,
        ResourceGrant.resource_id == resource_id,
    )
    if not include_revoked:
        q = q.where(ResourceGrant.revoked_at.is_(None))
    rows = db.scalars(q.order_by(ResourceGrant.created_at.desc())).all()
    return [serialize_grant(db, g) for g in rows]


def _validate_principal(db: Session, *, org_id: str, principal_type: str, principal_id: str) -> None:
    if principal_type not in PRINCIPAL_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid principal_type")
    if principal_type == "user":
        u = db.get(User, principal_id)
        ok = u is not None and u.org_id == org_id
    elif principal_type == "role":
        r = db.get(Role, principal_id)
        ok = r is not None and r.org_id == org_id
    else:
        from app.approvals.models import ApproverGroup

        g = db.get(ApproverGroup, principal_id)
        ok = g is not None and g.org_id == org_id
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Principal not found in this organization")


def grant_access(db: Session, *, actor: User, payload) -> dict:
    if payload.resource_type not in RESOURCE_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported resource_type")
    if payload.access_level not in LEVELS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid access_level")
    if _resource_owner_id(db, payload.resource_type, payload.resource_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resource not found")
    if not can_manage_grants(
        db, user=actor, resource_type=payload.resource_type, resource_id=payload.resource_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed to share this resource")
    _validate_principal(
        db, org_id=actor.org_id, principal_type=payload.principal_type, principal_id=payload.principal_id
    )

    # Upsert: if an active grant already exists for this principal+resource,
    # update its level/expiry rather than stacking duplicates.
    existing = db.scalar(
        select(ResourceGrant).where(
            ResourceGrant.org_id == actor.org_id,
            ResourceGrant.principal_type == payload.principal_type,
            ResourceGrant.principal_id == payload.principal_id,
            ResourceGrant.resource_type == payload.resource_type,
            ResourceGrant.resource_id == payload.resource_id,
            ResourceGrant.revoked_at.is_(None),
        )
    )
    if existing is not None:
        existing.access_level = payload.access_level
        existing.valid_until = payload.valid_until
        existing.note = payload.note
        existing.updated_by_user_id = actor.id
        grant = existing
    else:
        grant = ResourceGrant(
            org_id=actor.org_id,
            principal_type=payload.principal_type,
            principal_id=payload.principal_id,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            access_level=payload.access_level,
            note=payload.note,
            valid_from=utcnow(),
            valid_until=payload.valid_until,
            created_by_user_id=actor.id,
            updated_by_user_id=actor.id,
        )
        db.add(grant)
    db.flush()
    write_audit_log(
        db,
        action="grant.created",
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        metadata={
            "grant_id": grant.id,
            "principal_type": grant.principal_type,
            "principal_id": grant.principal_id,
            "access_level": grant.access_level,
        },
    )
    db.commit()
    db.refresh(grant)
    return serialize_grant(db, grant)


def revoke_grant(db: Session, *, actor: User, grant_id: str) -> None:
    grant = db.get(ResourceGrant, grant_id)
    if grant is None or grant.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Grant not found")
    if not can_manage_grants(
        db, user=actor, resource_type=grant.resource_type, resource_id=grant.resource_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed to manage this resource")
    if grant.revoked_at is None:
        grant.revoked_at = utcnow()
        grant.updated_by_user_id = actor.id
        write_audit_log(
            db,
            action="grant.revoked",
            resource_type=grant.resource_type,
            resource_id=grant.resource_id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            metadata={"grant_id": grant.id},
        )
        db.commit()
