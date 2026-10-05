"""The single, shared access-resolution capability for the org-unit hierarchy
+ delegation feature (FR-11, FR-22 — feature 002-org-hierarchy-rbac).

No other module may reimplement the ancestry walk, the grant-validity
(soft-delete + expiry) filter, or the delegation-intersection logic. This
module sits ALONGSIDE ``app.core.rbac.has_permission`` (the plain
permission-string check, unchanged — FR-23) rather than replacing it: a
resolved access decision requires both the permission-string check AND this
module's org-unit-scope/expiry/delegation resolution to succeed.

Explicitly out of scope and never called into from here: ``app.authority``
(the contract-approve/-sign authority-limits ABAC mechanism, FR-24) and
``app.walls`` (the ethical-wall row-level override, which continues to run
after and override any ALLOW this resolver produces — FR-25).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Role, User, UserRoleGrant
from app.core.database import utcnow
from app.core.rbac import has_permission
from app.org_structure.models import Delegation, OrgUnit

# Cycle-prevention guard: a pre-existing corrupt cycle in org_unit.parent_id
# (or a self-referencing delegation chain) must not hang a request — cap any
# ancestor/descendant walk at this many levels.
_MAX_DEPTH = 64

REASONS = (
    "native_grant",
    "delegated_grant",
    "no_grant",
    "role_lacks_permission",
    "org_unit_not_found",
)


@dataclass(frozen=True)
class ResolvedAccess:
    allowed: bool
    reason: str
    role_ids: tuple[str, ...]
    org_unit_id: str | None
    via_delegation_id: str | None = None
    on_behalf_of_user_id: str | None = None


@dataclass(frozen=True)
class RoleHolder:
    """One user eligible to fulfil ``role_id`` at a given org-unit scope
    (feature 004-approval-chain-reconciliation, FR-18) — the INVERSE
    direction of ``ResolvedAccess``: "who holds this role" rather than "does
    this specific user qualify"."""

    user_id: str
    role_id: str
    org_unit_id: str  # the unit the satisfying grant sits at
    via_delegation_id: str | None = None
    on_behalf_of_user_id: str | None = None  # the delegator, when via_delegation_id is set


def ancestor_unit_ids(
    db: Session, *, org_id: str, org_unit_id: str, include_self: bool = True
) -> list[str]:
    """Walk ``parent_id`` upward from ``org_unit_id`` to the root.

    Non-deleted units only, strictly within ``org_id``. Returns ``[]`` if the
    starting unit is missing, soft-deleted, or belongs to another
    organization. Depth-capped at ``_MAX_DEPTH`` to survive a corrupt cycle
    without hanging.
    """
    chain: list[str] = []
    current_id: str | None = org_unit_id
    seen: set[str] = set()
    depth = 0
    while current_id and depth < _MAX_DEPTH:
        if current_id in seen:
            break
        seen.add(current_id)
        unit = db.scalar(
            select(OrgUnit).where(
                OrgUnit.id == current_id,
                OrgUnit.org_id == org_id,
                OrgUnit.deleted_at.is_(None),
            )
        )
        if unit is None:
            break
        if depth > 0 or include_self:
            chain.append(unit.id)
        current_id = unit.parent_id
        depth += 1
    return chain


def descendant_unit_ids(
    db: Session, *, org_id: str, org_unit_id: str, include_self: bool = True
) -> list[str]:
    """Breadth-first walk downward from ``org_unit_id``.

    Non-deleted units only, strictly within ``org_id``. Used for cycle
    detection on re-parent (a different task, T008) and for delegation
    org-unit narrowing here. Depth-capped at ``_MAX_DEPTH``.
    """
    root = db.scalar(
        select(OrgUnit).where(
            OrgUnit.id == org_unit_id,
            OrgUnit.org_id == org_id,
            OrgUnit.deleted_at.is_(None),
        )
    )
    if root is None:
        return []
    result: list[str] = [root.id] if include_self else []
    frontier = [root.id]
    depth = 0
    while frontier and depth < _MAX_DEPTH:
        children = db.scalars(
            select(OrgUnit).where(
                OrgUnit.parent_id.in_(frontier),
                OrgUnit.org_id == org_id,
                OrgUnit.deleted_at.is_(None),
            )
        ).all()
        if not children:
            break
        frontier = [child.id for child in children]
        result.extend(frontier)
        depth += 1
    return result


def _grant_validity_clause(at: datetime | None = None):
    """The soft-delete + validity-window predicate, extracted VERBATIM from the
    body of ``active_grants_for_user`` (which now calls it). Behaviour-
    identical; this exists so ``users_holding_role`` (feature
    004-approval-chain-reconciliation, FR-18) cannot drift from it.

    ``deleted_at`` (revocation) and ``valid_to``/``valid_from`` (expiry) are
    two INDEPENDENT exclusion conditions — neither substitutes for the other.
    """
    now = at or utcnow()
    return (
        UserRoleGrant.deleted_at.is_(None),
        (UserRoleGrant.valid_to.is_(None)) | (UserRoleGrant.valid_to > now),
        (UserRoleGrant.valid_from.is_(None)) | (UserRoleGrant.valid_from <= now),
    )


def active_grants_for_user(
    db: Session, *, user_id: str, org_id: str, at: datetime | None = None
) -> list[UserRoleGrant]:
    """The single soft-delete + validity-window filter (FR-9, FR-22).

    ``deleted_at`` (revocation) and ``valid_to``/``valid_from`` (expiry) are
    two INDEPENDENT exclusion conditions — neither substitutes for the other.
    """
    return list(
        db.scalars(
            select(UserRoleGrant).where(
                UserRoleGrant.user_id == user_id,
                UserRoleGrant.org_id == org_id,
                *_grant_validity_clause(at),
            )
        ).all()
    )


def resolve_access(
    db: Session,
    *,
    user: User,
    permission: str,
    org_unit_id: str,
    at: datetime | None = None,
    allow_delegated: bool = True,
    restrict_role_ids: Sequence[str] | None = None,
) -> ResolvedAccess:
    """The core algorithm (exact steps, so every consumer behaves identically).

    1. Load the target org unit, org-scoped + non-deleted.
    2. Native pass: the user's own active grants, upward-only rollup (FR-8).
    3. Permission-string check via the EXISTING ``core.rbac.has_permission``
       (FR-23 — reused verbatim, never reimplemented).
    4. If still denied and ``allow_delegated``: re-evaluate fresh (FR-16) every
       active delegation naming this user as delegate, recursing into the
       DELEGATOR's own resolution with ``allow_delegated=False`` (FR-17 — no
       chains) and the delegation's role/org-unit narrowing intersected in
       (FR-14 — narrow, never broaden).
    """
    now = at or utcnow()

    unit = db.scalar(
        select(OrgUnit).where(
            OrgUnit.id == org_unit_id,
            OrgUnit.org_id == user.org_id,
            OrgUnit.deleted_at.is_(None),
        )
    )
    if unit is None:
        return ResolvedAccess(
            allowed=False, reason="org_unit_not_found", role_ids=(), org_unit_id=org_unit_id
        )

    chain = ancestor_unit_ids(db, org_id=user.org_id, org_unit_id=org_unit_id, include_self=True)
    ancestor_only = set(chain[1:])  # strictly above org_unit_id — FR-8 upward-only

    grants = active_grants_for_user(db, user_id=user.id, org_id=user.org_id, at=now)
    if restrict_role_ids is not None:
        restrict_set = set(restrict_role_ids)
        grants = [g for g in grants if g.role_id in restrict_set]

    candidate_grants: list[UserRoleGrant] = []
    for grant in grants:
        role = grant.role
        if role is None:
            continue
        exact_match = grant.org_unit_id == org_unit_id
        rollup_match = role.allows_hierarchy_rollup and grant.org_unit_id in ancestor_only
        if exact_match or rollup_match:
            candidate_grants.append(grant)

    matched_role_ids: list[str] = []
    for grant in candidate_grants:
        permission_values = {p.value for p in grant.role.permissions}
        if has_permission(permission_values, permission):
            matched_role_ids.append(grant.role_id)

    if matched_role_ids:
        return ResolvedAccess(
            allowed=True,
            reason="native_grant",
            role_ids=tuple(matched_role_ids),
            org_unit_id=org_unit_id,
        )

    denial_reason = "role_lacks_permission" if candidate_grants else "no_grant"

    if allow_delegated:
        delegations = db.scalars(
            select(Delegation).where(
                Delegation.delegate_user_id == user.id,
                Delegation.org_id == user.org_id,
                Delegation.deleted_at.is_(None),
                Delegation.status == "active",
                Delegation.start_date <= now,
                Delegation.end_date >= now,
            )
        ).all()
        for delegation in delegations:
            if delegation.org_unit_id is not None:
                allowed_units = descendant_unit_ids(
                    db, org_id=user.org_id, org_unit_id=delegation.org_unit_id, include_self=True
                )
                if org_unit_id not in allowed_units:
                    continue
            delegator = db.get(User, delegation.delegator_user_id)
            if delegator is None or delegator.org_id != user.org_id:
                continue
            narrowed_role_ids = (delegation.role_id,) if delegation.role_id else None
            sub = resolve_access(
                db,
                user=delegator,
                permission=permission,
                org_unit_id=org_unit_id,
                at=now,
                allow_delegated=False,
                restrict_role_ids=narrowed_role_ids,
            )
            if sub.allowed:
                return ResolvedAccess(
                    allowed=True,
                    reason="delegated_grant",
                    role_ids=sub.role_ids,
                    org_unit_id=org_unit_id,
                    via_delegation_id=delegation.id,
                    on_behalf_of_user_id=delegation.delegator_user_id,
                )

    return ResolvedAccess(allowed=False, reason=denial_reason, role_ids=(), org_unit_id=org_unit_id)


def assert_access(
    db: Session, *, user: User, permission: str, org_unit_id: str, at: datetime | None = None
) -> ResolvedAccess:
    """``resolve_access``, but raises ``HTTPException(403, ...)`` (and records
    the denial on the Method-8 audit chain) when not allowed."""
    resolved = resolve_access(db, user=user, permission=permission, org_unit_id=org_unit_id, at=at)
    if not resolved.allowed:
        from app.core.authz import record_decision

        record_decision(
            user=user,
            action=permission,
            outcome="denied",
            resource_type="org_unit",
            resource_id=org_unit_id,
            reason=resolved.reason,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Access denied: {resolved.reason}")
    return resolved


def effective_permission_values(db: Session, *, user: User, at: datetime | None = None) -> set[str]:
    """The union of permission strings from the user's currently-active
    (non-expired, non-deleted) grants' roles, ignoring org-unit scoping.

    Used by ``core.deps.require_permission`` (T009) as the "is the permission
    string valid at all, anywhere" check, so an expired/revoked grant confers
    nothing anywhere — org-unit-specific enforcement remains ``resolve_access``'s
    job. Deliberately does NOT use ``user.permission_values`` (which no longer
    respects expiry now that ``User.roles`` is viewonly-but-unfiltered-on-expiry)
    — it independently queries active grants and their roles' permissions.
    """
    now = at or utcnow()
    grants = active_grants_for_user(db, user_id=user.id, org_id=user.org_id, at=now)
    values: set[str] = set()
    for grant in grants:
        role = grant.role
        if role is None:
            continue
        values.update(p.value for p in role.permissions)
    return values


def users_holding_role(
    db: Session,
    *,
    org_id: str,
    role_id: str,
    org_unit_id: str,
    at: datetime | None = None,
    include_delegates: bool = True,
) -> list[RoleHolder]:
    """The INVERSE of ``resolve_access``: every user who holds ``role_id`` at
    ``org_unit_id`` (or an ancestor, when the role allows rollup) right now
    (feature 004-approval-chain-reconciliation, FR-18). Deduplicated by
    ``user_id`` — a native grant always wins over a delegated one.

    Evaluated fresh on every call; nothing cached.
    """
    now = at or utcnow()

    role = db.scalar(
        select(Role).where(Role.id == role_id, Role.org_id == org_id)
    )
    if role is None:
        return []

    chain = ancestor_unit_ids(db, org_id=org_id, org_unit_id=org_unit_id, include_self=True)
    if not chain:
        return []
    ancestor_only = set(chain[1:])  # strictly above org_unit_id — upward-only, matches resolve_access

    grants = db.scalars(
        select(UserRoleGrant).where(
            UserRoleGrant.org_id == org_id,
            UserRoleGrant.role_id == role_id,
            *_grant_validity_clause(now),
        )
    ).all()

    holders: dict[str, RoleHolder] = {}
    native_user_ids: set[str] = set()
    for grant in grants:
        exact_match = grant.org_unit_id == org_unit_id
        rollup_match = role.allows_hierarchy_rollup and grant.org_unit_id in ancestor_only
        if not (exact_match or rollup_match):
            continue
        native_user_ids.add(grant.user_id)
        holders[grant.user_id] = RoleHolder(
            user_id=grant.user_id, role_id=role_id, org_unit_id=grant.org_unit_id
        )

    if include_delegates:
        delegations = db.scalars(
            select(Delegation).where(
                Delegation.org_id == org_id,
                Delegation.deleted_at.is_(None),
                Delegation.status == "active",
                Delegation.start_date <= now,
                Delegation.end_date >= now,
                (Delegation.role_id.is_(None)) | (Delegation.role_id == role_id),
            )
        ).all()
        for delegation in delegations:
            if delegation.delegator_user_id not in native_user_ids:
                continue
            if delegation.org_unit_id is not None:
                allowed_units = descendant_unit_ids(
                    db, org_id=org_id, org_unit_id=delegation.org_unit_id, include_self=True
                )
                if org_unit_id not in allowed_units:
                    continue
            if delegation.delegate_user_id in holders:
                continue  # native precedence — never overwrite a native holder
            holders[delegation.delegate_user_id] = RoleHolder(
                user_id=delegation.delegate_user_id,
                role_id=role_id,
                org_unit_id=org_unit_id,
                via_delegation_id=delegation.id,
                on_behalf_of_user_id=delegation.delegator_user_id,
            )

    return list(holders.values())


def delegation_audit_metadata(resolved: ResolvedAccess, actor: User) -> dict:
    """The FR-21 two-field attribution block: the acting user and (when the
    action was performed under delegation) the delegator, as two DISTINCT
    fields — never collapsed into one "acted as" identity. Callers merge this
    into their mutation's ``write_audit_log(metadata=...)``."""
    metadata: dict = {"acting_user_id": actor.id}
    if resolved.via_delegation_id:
        metadata["on_behalf_of_user_id"] = resolved.on_behalf_of_user_id
        metadata["delegation_id"] = resolved.via_delegation_id
    return metadata
