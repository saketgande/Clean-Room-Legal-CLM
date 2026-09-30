"""Org-unit hierarchy, role-grant, and delegation CRUD (feature
002-org-hierarchy-rbac, T008).

Every mutation here is org-scoped (``actor.org_id``), writes an audit row in
the same transaction (FR-19), and — for soft-deletes — sets
``deleted_by_user_id`` (FR-20). Mutations that gate on a specific org-unit
scope call ``app.core.org_access.assert_access`` (the shared resolver, FR-11)
with the SAME permission string the router already required, per plan.md's
"Where THIS feature consumes the resolver" note; the resulting
``ResolvedAccess`` is folded into the audit row's metadata via
``delegation_audit_metadata`` so an action performed under an active
delegation is attributable to both the acting user and the delegator (FR-21).

Business-rule reuse, never reimplementation (FR-11/FR-22): ancestry walks
(``ancestor_unit_ids`` / ``descendant_unit_ids``) and the soft-delete/expiry
filter (``active_grants_for_user``) come from ``app.core.org_access``; the
grant-escalation guard reuses ``app.roles.service._assert_actor_can_grant``
rather than copying its logic.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import Role, User, UserRoleGrant
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.org_access import (
    active_grants_for_user,
    ancestor_unit_ids,
    assert_access,
    delegation_audit_metadata,
    descendant_unit_ids,
    effective_permission_values,
)
from app.org_structure.access import get_org_root, get_org_unit_or_404
from app.org_structure.models import Delegation, OrgUnit
from app.roles.service import RoleService

_DELEGATION_DIRECTIONS = {"mine", "received", "all"}


def _is_org_admin_now(db: Session, actor: User) -> bool:
    """Expiry-aware admin-equivalence check (FR-9): unlike
    ``app.core.access.is_org_admin`` (which resolves off ``User.roles`` /
    ``permission_values`` and, per this feature's design, does not
    independently filter ``valid_to`` expiry), this resolves off
    ``org_access.effective_permission_values`` so an admin whose
    ``admin_panel:access`` grant has expired but not been revoked cannot
    still pass admin-equivalence checks on this feature's surface (delegation
    revoke-on-behalf-of-another, view-all-delegations, create-delegation-on-
    behalf-of-another)."""
    return "admin_panel:access" in effective_permission_values(db, user=actor)


# ---------------------------------------------------------------------------
# Org units
# ---------------------------------------------------------------------------


def _unit_depth_and_path(db: Session, org_id: str, unit: OrgUnit) -> tuple[int, list[str]]:
    depth = 0
    path_names = [unit.name]
    seen = {unit.id}
    current = unit
    while current.parent_id and current.parent_id not in seen:
        parent = db.get(OrgUnit, current.parent_id)
        if parent is None or parent.org_id != org_id:
            break
        seen.add(parent.id)
        path_names.insert(0, parent.name)
        depth += 1
        current = parent
    return depth, path_names


def _unit_child_count(db: Session, org_id: str, unit_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(OrgUnit)
            .where(
                OrgUnit.org_id == org_id,
                OrgUnit.parent_id == unit_id,
                OrgUnit.deleted_at.is_(None),
            )
        )
        or 0
    )


def _unit_active_grant_count(db: Session, org_id: str, unit_id: str) -> int:
    now = utcnow()
    return (
        db.scalar(
            select(func.count())
            .select_from(UserRoleGrant)
            .where(
                UserRoleGrant.org_id == org_id,
                UserRoleGrant.org_unit_id == unit_id,
                UserRoleGrant.deleted_at.is_(None),
                (UserRoleGrant.valid_to.is_(None)) | (UserRoleGrant.valid_to > now),
                (UserRoleGrant.valid_from.is_(None)) | (UserRoleGrant.valid_from <= now),
            )
        )
        or 0
    )


def _serialize_org_unit(db: Session, org_id: str, unit: OrgUnit) -> dict:
    depth, path_names = _unit_depth_and_path(db, org_id, unit)
    return {
        "id": unit.id,
        "org_id": unit.org_id,
        "name": unit.name,
        "parent_id": unit.parent_id,
        "is_root": unit.parent_id is None,
        "depth": depth,
        "path_names": path_names,
        "child_count": _unit_child_count(db, org_id, unit.id),
        "active_grant_count": _unit_active_grant_count(db, org_id, unit.id),
        "deleted_at": unit.deleted_at,
        "created_at": unit.created_at,
        "updated_at": unit.updated_at,
    }


def list_org_units(db: Session, *, actor: User, include_deleted: bool = False) -> list[dict]:
    stmt = select(OrgUnit).where(OrgUnit.org_id == actor.org_id)
    if not include_deleted:
        stmt = stmt.where(OrgUnit.deleted_at.is_(None))
    units = db.scalars(stmt).all()
    rows = [_serialize_org_unit(db, actor.org_id, u) for u in units]
    rows.sort(key=lambda r: (r["depth"], r["path_names"]))
    return rows


def create_org_unit(db: Session, *, actor: User, payload) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Org unit name is required")

    resolved = None
    if payload.parent_id is None:
        existing_root = db.scalar(
            select(OrgUnit.id).where(
                OrgUnit.org_id == actor.org_id,
                OrgUnit.parent_id.is_(None),
                OrgUnit.deleted_at.is_(None),
            )
        )
        if existing_root:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Organization already has a root org unit"
            )
    else:
        parent = get_org_unit_or_404(db, actor.org_id, payload.parent_id)
        resolved = assert_access(
            db, user=actor, permission="admin_panel:access", org_unit_id=parent.id
        )

    unit = OrgUnit(
        org_id=actor.org_id,
        name=name,
        parent_id=payload.parent_id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(unit)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Organization already has a root org unit"
        ) from None

    metadata = delegation_audit_metadata(resolved, actor) if resolved else {"acting_user_id": actor.id}
    write_audit_log(
        db,
        action="org_unit.created",
        resource_type="org_unit",
        resource_id=unit.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={"name": unit.name, "parent_id": unit.parent_id},
        metadata=metadata,
    )
    db.commit()
    db.refresh(unit)
    return _serialize_org_unit(db, actor.org_id, unit)


def update_org_unit(db: Session, *, actor: User, org_unit_id: str, payload) -> dict:
    unit = get_org_unit_or_404(db, actor.org_id, org_unit_id)
    resolved = assert_access(db, user=actor, permission="admin_panel:access", org_unit_id=unit.id)
    metadata = delegation_audit_metadata(resolved, actor)

    fields_set = payload.model_fields_set
    changed = False

    rename_before: dict | None = None
    rename_after: dict | None = None
    if "name" in fields_set and payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Org unit name is required")
        if new_name != unit.name:
            rename_before = {"name": unit.name}
            unit.name = new_name
            rename_after = {"name": unit.name}
            changed = True

    reparent_before: dict | None = None
    reparent_after: dict | None = None
    if "parent_id" in fields_set:
        new_parent_id = payload.parent_id
        if new_parent_id != unit.parent_id:
            if new_parent_id is None:
                other_root = db.scalar(
                    select(OrgUnit.id).where(
                        OrgUnit.org_id == actor.org_id,
                        OrgUnit.parent_id.is_(None),
                        OrgUnit.deleted_at.is_(None),
                        OrgUnit.id != unit.id,
                    )
                )
                if other_root:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT, "Organization already has a root org unit"
                    )
            else:
                if new_parent_id == unit.id:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT, "An org unit cannot be its own parent"
                    )
                new_parent = get_org_unit_or_404(db, actor.org_id, new_parent_id)
                assert_access(
                    db, user=actor, permission="admin_panel:access", org_unit_id=new_parent.id
                )
                descendants = descendant_unit_ids(
                    db, org_id=actor.org_id, org_unit_id=unit.id, include_self=False
                )
                if new_parent_id in descendants:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "Cannot re-parent an org unit under its own descendant",
                    )
            reparent_before = {"parent_id": unit.parent_id}
            unit.parent_id = new_parent_id
            reparent_after = {"parent_id": unit.parent_id}
            changed = True

    if changed:
        unit.updated_by_user_id = actor.id
        db.flush()
        if rename_before is not None:
            write_audit_log(
                db,
                action="org_unit.updated",
                resource_type="org_unit",
                resource_id=unit.id,
                org_id=actor.org_id,
                actor_user_id=actor.id,
                before=rename_before,
                after=rename_after,
                metadata=metadata,
            )
        if reparent_before is not None:
            write_audit_log(
                db,
                action="org_unit.reparented",
                resource_type="org_unit",
                resource_id=unit.id,
                org_id=actor.org_id,
                actor_user_id=actor.id,
                before=reparent_before,
                after=reparent_after,
                metadata=metadata,
            )
        db.commit()
        db.refresh(unit)

    return _serialize_org_unit(db, actor.org_id, unit)


def delete_org_unit(db: Session, *, actor: User, org_unit_id: str) -> dict:
    unit = get_org_unit_or_404(db, actor.org_id, org_unit_id, include_deleted=True)
    if unit.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Org unit already deleted")
    resolved = assert_access(db, user=actor, permission="admin_panel:access", org_unit_id=unit.id)
    metadata = delegation_audit_metadata(resolved, actor)

    children = db.scalars(
        select(OrgUnit).where(
            OrgUnit.org_id == actor.org_id,
            OrgUnit.parent_id == unit.id,
            OrgUnit.deleted_at.is_(None),
        )
    ).all()

    if unit.parent_id is None and children:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The root org unit cannot be deleted while it has descendant org units",
        )

    reparented_entries: list[dict] = []
    for child in children:
        from_parent_id = child.parent_id
        child.parent_id = unit.parent_id
        child.updated_by_user_id = actor.id
        db.flush()
        write_audit_log(
            db,
            action="org_unit.reparented_on_delete",
            resource_type="org_unit",
            resource_id=child.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
            before={"parent_id": from_parent_id},
            after={"parent_id": child.parent_id},
            metadata={**metadata, "consequence_of_org_unit_id": unit.id},
        )
        reparented_entries.append(
            {
                "org_unit_id": child.id,
                "name": child.name,
                "from_parent_id": from_parent_id,
                "to_parent_id": child.parent_id,
            }
        )

    now = utcnow()
    unit.deleted_at = now
    unit.deleted_by_user_id = actor.id
    unit.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="org_unit.deleted",
        resource_type="org_unit",
        resource_id=unit.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before={"name": unit.name, "parent_id": unit.parent_id},
        metadata={**metadata, "reparented_child_ids": [c.id for c in children]},
    )
    db.commit()
    return {"deleted_org_unit_id": unit.id, "reparented": reparented_entries}


# ---------------------------------------------------------------------------
# Role grants
# ---------------------------------------------------------------------------


def _serialize_role_grant(db: Session, grant: UserRoleGrant) -> dict:
    user = db.get(User, grant.user_id)
    role = grant.role or db.get(Role, grant.role_id)
    unit = db.get(OrgUnit, grant.org_unit_id)
    now = utcnow()
    is_active = (
        grant.deleted_at is None
        and (grant.valid_to is None or grant.valid_to > now)
        and (grant.valid_from is None or grant.valid_from <= now)
    )
    return {
        "id": grant.id,
        "org_id": grant.org_id,
        "user_id": grant.user_id,
        "user_label": (user.full_name or user.email) if user else grant.user_id,
        "role_id": grant.role_id,
        "role_name": role.name if role else grant.role_id,
        "allows_hierarchy_rollup": role.allows_hierarchy_rollup if role else True,
        "org_unit_id": grant.org_unit_id,
        "org_unit_name": unit.name if unit else grant.org_unit_id,
        "valid_from": grant.valid_from,
        "valid_to": grant.valid_to,
        "is_active": is_active,
        "revoked_at": grant.deleted_at,
        "revoked_by_user_id": grant.deleted_by_user_id,
        "created_at": grant.created_at,
        "created_by_user_id": grant.created_by_user_id,
    }


def list_role_grants(
    db: Session,
    *,
    actor: User,
    user_id: str | None = None,
    org_unit_id: str | None = None,
    include_revoked: bool = False,
) -> list[dict]:
    stmt = select(UserRoleGrant).where(UserRoleGrant.org_id == actor.org_id)
    if user_id:
        stmt = stmt.where(UserRoleGrant.user_id == user_id)
    if org_unit_id:
        stmt = stmt.where(UserRoleGrant.org_unit_id == org_unit_id)
    if not include_revoked:
        stmt = stmt.where(UserRoleGrant.deleted_at.is_(None))
    grants = db.scalars(stmt.order_by(UserRoleGrant.created_at.desc())).all()
    return [_serialize_role_grant(db, g) for g in grants]


def create_role_grant(db: Session, *, actor: User, payload) -> dict:
    target = db.get(User, payload.user_id)
    if target is None or target.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    role = db.get(Role, payload.role_id)
    if role is None or role.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    unit = get_org_unit_or_404(db, actor.org_id, payload.org_unit_id, include_deleted=True)
    if unit.deleted_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Cannot grant a role scoped to a deleted org unit",
        )
    if (
        payload.valid_to is not None
        and payload.valid_from is not None
        and payload.valid_to < payload.valid_from
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "valid_to must not be before valid_from"
        )

    RoleService(db)._assert_actor_can_grant(actor, [role])
    resolved = assert_access(db, user=actor, permission="user:update_role", org_unit_id=unit.id)

    existing = db.scalar(
        select(UserRoleGrant.id).where(
            UserRoleGrant.user_id == target.id,
            UserRoleGrant.role_id == role.id,
            UserRoleGrant.org_unit_id == unit.id,
            UserRoleGrant.deleted_at.is_(None),
        )
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This user already holds this role at this org unit"
        )

    grant = UserRoleGrant(
        org_id=actor.org_id,
        user_id=target.id,
        role_id=role.id,
        org_unit_id=unit.id,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(grant)
    db.flush()
    write_audit_log(
        db,
        action="role_grant.created",
        resource_type="user_role",
        resource_id=grant.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={
            "user_id": target.id,
            "role_id": role.id,
            "org_unit_id": unit.id,
            "valid_from": grant.valid_from,
            "valid_to": grant.valid_to,
        },
        metadata=delegation_audit_metadata(resolved, actor),
    )
    db.commit()
    db.refresh(grant)
    return _serialize_role_grant(db, grant)


def revoke_role_grant(db: Session, *, actor: User, grant_id: str) -> None:
    grant = db.get(UserRoleGrant, grant_id)
    if grant is None or grant.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role grant not found")
    if grant.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Role grant already revoked")
    resolved = assert_access(
        db, user=actor, permission="user:update_role", org_unit_id=grant.org_unit_id
    )
    before = {
        "user_id": grant.user_id,
        "role_id": grant.role_id,
        "org_unit_id": grant.org_unit_id,
        "valid_to": grant.valid_to,
    }
    grant.deleted_at = utcnow()
    grant.deleted_by_user_id = actor.id
    grant.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="role_grant.revoked",
        resource_type="user_role",
        resource_id=grant.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
        metadata={**delegation_audit_metadata(resolved, actor), "revocation": True},
    )
    db.commit()


# ---------------------------------------------------------------------------
# Delegations
# ---------------------------------------------------------------------------


def _serialize_delegation(db: Session, delegation: Delegation, actor: User) -> dict:
    delegator = db.get(User, delegation.delegator_user_id)
    delegate = db.get(User, delegation.delegate_user_id)
    role = db.get(Role, delegation.role_id) if delegation.role_id else None
    unit = db.get(OrgUnit, delegation.org_unit_id) if delegation.org_unit_id else None
    now = utcnow()
    is_active = (
        delegation.deleted_at is None
        and delegation.status == "active"
        and delegation.start_date <= now <= delegation.end_date
    )
    can_revoke = delegation.deleted_at is None and (
        actor.id == delegation.delegator_user_id or _is_org_admin_now(db, actor)
    )
    return {
        "id": delegation.id,
        "org_id": delegation.org_id,
        "delegator_user_id": delegation.delegator_user_id,
        "delegator_label": (
            (delegator.full_name or delegator.email) if delegator else delegation.delegator_user_id
        ),
        "delegate_user_id": delegation.delegate_user_id,
        "delegate_label": (
            (delegate.full_name or delegate.email) if delegate else delegation.delegate_user_id
        ),
        "role_id": delegation.role_id,
        "role_name": role.name if role else None,
        "org_unit_id": delegation.org_unit_id,
        "org_unit_name": unit.name if unit else None,
        "start_date": delegation.start_date,
        "end_date": delegation.end_date,
        "status": delegation.status,
        "is_active": is_active,
        "can_revoke": can_revoke,
        "revoked_at": delegation.deleted_at,
        "revoked_by_user_id": delegation.deleted_by_user_id,
        "created_at": delegation.created_at,
        "created_by_user_id": delegation.created_by_user_id,
    }


def list_delegations(db: Session, *, actor: User, direction: str = "mine") -> list[dict]:
    if direction not in _DELEGATION_DIRECTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid direction")
    if direction == "all" and not _is_org_admin_now(db, actor):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "admin_panel:access required to view all delegations"
        )
    stmt = select(Delegation).where(Delegation.org_id == actor.org_id)
    if direction == "mine":
        stmt = stmt.where(Delegation.delegator_user_id == actor.id)
    elif direction == "received":
        stmt = stmt.where(Delegation.delegate_user_id == actor.id)
    delegations = db.scalars(stmt.order_by(Delegation.created_at.desc())).all()
    return [_serialize_delegation(db, d, actor) for d in delegations]


def delegation_eligibility(
    db: Session, *, actor: User, delegator_user_id: str | None = None
) -> list[dict]:
    target_id = delegator_user_id or actor.id
    if target_id != actor.id and not _is_org_admin_now(db, actor):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "admin_panel:access required to view another user's eligibility",
        )
    target = db.get(User, target_id)
    if target is None or target.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    grants = active_grants_for_user(db, user_id=target.id, org_id=actor.org_id)
    entries: list[dict] = []
    for grant in grants:
        role = grant.role
        unit = db.get(OrgUnit, grant.org_unit_id)
        if role is None or unit is None:
            continue
        entries.append(
            {
                "role_id": role.id,
                "role_name": role.name,
                "allows_hierarchy_rollup": role.allows_hierarchy_rollup,
                "org_unit_id": unit.id,
                "org_unit_name": unit.name,
                "valid_to": grant.valid_to,
            }
        )
    return entries


def _delegator_holds_natively(
    db: Session, *, delegator: User, org_unit_id: str | None, role_id: str | None
) -> bool:
    """FR-14/FR-17: the delegator must currently, natively hold whatever the
    delegation narrows to — never merely via their own received delegation
    (no chains)."""
    grants = active_grants_for_user(db, user_id=delegator.id, org_id=delegator.org_id)
    if role_id is not None:
        grants = [g for g in grants if g.role_id == role_id]
    if org_unit_id is not None:
        chain = ancestor_unit_ids(
            db, org_id=delegator.org_id, org_unit_id=org_unit_id, include_self=True
        )
        ancestor_only = set(chain[1:])
        grants = [
            g
            for g in grants
            if g.org_unit_id == org_unit_id
            or (g.role is not None and g.role.allows_hierarchy_rollup and g.org_unit_id in ancestor_only)
        ]
    return len(grants) > 0


def create_delegation(db: Session, *, actor: User, payload) -> dict:
    delegator_id = payload.delegator_user_id or actor.id
    if delegator_id != actor.id and not _is_org_admin_now(db, actor):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "admin_panel:access required to create a delegation on another user's behalf",
        )
    delegator = db.get(User, delegator_id)
    if delegator is None or delegator.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delegator not found")
    delegate = db.get(User, payload.delegate_user_id)
    if delegate is None or delegate.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delegate not found")
    if delegator.id == delegate.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Cannot delegate to yourself")
    if payload.end_date < payload.start_date:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "end_date must not be before start_date"
        )

    if payload.role_id is not None:
        role = db.get(Role, payload.role_id)
        if role is None or role.org_id != actor.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")

    narrowed_unit = None
    if payload.org_unit_id is not None:
        narrowed_unit = get_org_unit_or_404(db, actor.org_id, payload.org_unit_id)

    if not _delegator_holds_natively(
        db, delegator=delegator, org_unit_id=payload.org_unit_id, role_id=payload.role_id
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Delegator does not hold the narrowed role/org-unit natively",
        )

    scope_unit = narrowed_unit or get_org_root(db, actor.org_id)
    resolved = assert_access(
        db, user=actor, permission="delegation:manage", org_unit_id=scope_unit.id
    )

    delegation = Delegation(
        org_id=actor.org_id,
        delegator_user_id=delegator.id,
        delegate_user_id=delegate.id,
        role_id=payload.role_id,
        org_unit_id=payload.org_unit_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        status="active",
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(delegation)
    db.flush()
    write_audit_log(
        db,
        action="delegation.created",
        resource_type="delegation",
        resource_id=delegation.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={
            "delegator_user_id": delegator.id,
            "delegate_user_id": delegate.id,
            "role_id": payload.role_id,
            "org_unit_id": payload.org_unit_id,
            "start_date": delegation.start_date,
            "end_date": delegation.end_date,
        },
        metadata=delegation_audit_metadata(resolved, actor),
    )
    db.commit()
    db.refresh(delegation)
    return _serialize_delegation(db, delegation, actor)


def revoke_delegation(db: Session, *, actor: User, delegation_id: str) -> dict:
    delegation = db.get(Delegation, delegation_id)
    if delegation is None or delegation.org_id != actor.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delegation not found")
    if delegation.deleted_at is not None or delegation.status == "revoked":
        raise HTTPException(status.HTTP_409_CONFLICT, "Delegation already revoked")
    if actor.id != delegation.delegator_user_id and not _is_org_admin_now(db, actor):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the delegator or an org administrator may revoke this delegation",
        )

    scope_unit_id = delegation.org_unit_id or get_org_root(db, actor.org_id).id
    resolved = assert_access(
        db, user=actor, permission="delegation:manage", org_unit_id=scope_unit_id
    )

    before = {"status": delegation.status}
    now = utcnow()
    delegation.status = "revoked"
    delegation.deleted_at = now
    delegation.deleted_by_user_id = actor.id
    delegation.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="delegation.revoked",
        resource_type="delegation",
        resource_id=delegation.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
        after={"status": "revoked"},
        metadata=delegation_audit_metadata(resolved, actor),
    )
    db.commit()
    db.refresh(delegation)
    return _serialize_delegation(db, delegation, actor)
