"""The single, shared screen-access resolution capability (FR-6, FR-15 —
feature 003-menu-screen-security).

No other module may reimplement this resolution. Menu-tree generation, the
`require_screen_level` route dependency (``app/core/deps.py``), and any
"what can I do here" query all call into ``resolve_screen_access`` /
``resolve_all_screen_access`` / ``assert_screen_level`` below — never a
separate or looser rule (FR-7, FR-9, FR-10).

This module deliberately lives OUTSIDE ``app.core.org_access`` (spec.md's
"Out of scope" fences that module off from this feature) but REUSES its
``ancestor_unit_ids`` ancestor-walk and ``active_grants_for_user``
soft-delete/expiry filter verbatim, so FR-18's "identical ancestor-walk
resolution feature 002 already uses" is literally the same code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core import org_access
from app.menu_security.models import RoleScreenAccess, Screen
from app.org_structure.models import OrgUnit

ACTION_LEVELS: tuple[str, ...] = ("VIEW", "ADD", "EDIT", "DELETE")
LEVEL_RANK: dict[str, int] = {"VIEW": 1, "ADD": 2, "EDIT": 3, "DELETE": 4}

REASONS = ("granted", "no_grant", "screen_not_found", "org_unit_not_found")


@dataclass(frozen=True)
class ResolvedScreenAccess:
    screen_code: str
    screen_id: str | None  # None when the screen code is unknown
    org_unit_id: str | None  # the unit the check was scoped to (None = the caller's own grant units)
    level: str | None  # None == below VIEW (no access)
    rank: int  # 0 when level is None
    role_ids: tuple[str, ...]  # the grants that produced `level` ( () when denied )
    reason: str


def _denied(
    *, screen_code: str, screen_id: str | None, org_unit_id: str | None, reason: str
) -> ResolvedScreenAccess:
    return ResolvedScreenAccess(
        screen_code=screen_code,
        screen_id=screen_id,
        org_unit_id=org_unit_id,
        level=None,
        rank=0,
        role_ids=(),
        reason=reason,
    )


def _context_units(
    db: Session,
    *,
    user: User,
    org_unit_id: str | None,
    grants: list,
    at: datetime | None,
) -> tuple[set[str], set[str], str | None]:
    """Returns (candidate_units, exact_units, error_reason)."""
    if org_unit_id is not None:
        unit = db.scalar(
            select(OrgUnit).where(
                OrgUnit.id == org_unit_id,
                OrgUnit.org_id == user.org_id,
                OrgUnit.deleted_at.is_(None),
            )
        )
        if unit is None:
            return set(), set(), "org_unit_not_found"
        exact_units = {org_unit_id}
    else:
        exact_units = {g.org_unit_id for g in grants if g.org_unit_id is not None}

    candidate_units: set[str] = set()
    for unit_id in exact_units:
        candidate_units |= set(
            org_access.ancestor_unit_ids(
                db, org_id=user.org_id, org_unit_id=unit_id, include_self=True
            )
        )
    return candidate_units, exact_units, None


def _scope_predicate(candidate_units: set[str]):
    """The `org_unit_id IS NULL OR org_unit_id IN candidate_units` predicate,
    degrading to just `IS NULL` when there is no non-empty context (an empty
    `IN ()` is valid SQL but this reads clearer and avoids dialect quirks)."""
    if candidate_units:
        return (RoleScreenAccess.org_unit_id.is_(None)) | (
            RoleScreenAccess.org_unit_id.in_(candidate_units)
        )
    return RoleScreenAccess.org_unit_id.is_(None)


def resolve_screen_access(
    db: Session,
    *,
    user: User,
    screen_code: str,
    org_unit_id: str | None = None,
    at: datetime | None = None,
) -> ResolvedScreenAccess:
    """The core algorithm (exact steps, so every consumer behaves identically
    — FR-15). See plan.md's "The screen-access resolver" section."""
    screen = db.scalar(
        select(Screen).where(Screen.code == screen_code, Screen.deleted_at.is_(None))
    )
    if screen is None:
        return _denied(
            screen_code=screen_code, screen_id=None, org_unit_id=org_unit_id, reason="screen_not_found"
        )

    grants = org_access.active_grants_for_user(db, user_id=user.id, org_id=user.org_id, at=at)
    user_role_ids = {g.role_id for g in grants}
    if not user_role_ids:
        return _denied(
            screen_code=screen_code, screen_id=screen.id, org_unit_id=org_unit_id, reason="no_grant"
        )

    candidate_units, exact_units, error_reason = _context_units(
        db, user=user, org_unit_id=org_unit_id, grants=grants, at=at
    )
    if error_reason is not None:
        return _denied(
            screen_code=screen_code, screen_id=screen.id, org_unit_id=org_unit_id, reason=error_reason
        )

    rows = db.scalars(
        select(RoleScreenAccess).where(
            RoleScreenAccess.org_id == user.org_id,
            RoleScreenAccess.screen_id == screen.id,
            RoleScreenAccess.role_id.in_(user_role_ids),
            RoleScreenAccess.deleted_at.is_(None),
            _scope_predicate(candidate_units),
        )
    ).all()

    best_rank = 0
    best_level: str | None = None
    winning_role_ids: set[str] = set()
    for row in rows:
        kept = (
            row.org_unit_id is None
            or row.org_unit_id in exact_units
            or (row.role.allows_hierarchy_rollup and row.org_unit_id in candidate_units)
        )
        if not kept:
            continue
        rank = row.action_level.rank
        if rank > best_rank:
            best_rank = rank
            best_level = row.action_level.code
            winning_role_ids = {row.role_id}
        elif rank == best_rank and rank > 0:
            winning_role_ids.add(row.role_id)

    if best_level is None:
        return _denied(
            screen_code=screen_code, screen_id=screen.id, org_unit_id=org_unit_id, reason="no_grant"
        )

    return ResolvedScreenAccess(
        screen_code=screen_code,
        screen_id=screen.id,
        org_unit_id=org_unit_id,
        level=best_level,
        rank=best_rank,
        role_ids=tuple(sorted(winning_role_ids)),
        reason="granted",
    )


def resolve_all_screen_access(
    db: Session,
    *,
    user: User,
    org_unit_id: str | None = None,
    at: datetime | None = None,
) -> dict[str, ResolvedScreenAccess]:
    """Every screen the user resolves to >= VIEW, keyed by screen_code.

    One batched pass with the IDENTICAL predicate as ``resolve_screen_access``
    — the menu tree and /screen-access/me call this; per-screen and batched
    results are asserted equal in test_screen_access_resolver.py (FR-15)."""
    screens = db.scalars(select(Screen).where(Screen.deleted_at.is_(None))).all()

    grants = org_access.active_grants_for_user(db, user_id=user.id, org_id=user.org_id, at=at)
    user_role_ids = {g.role_id for g in grants}
    if not user_role_ids:
        return {}

    candidate_units, exact_units, error_reason = _context_units(
        db, user=user, org_unit_id=org_unit_id, grants=grants, at=at
    )
    if error_reason is not None:
        return {}

    screen_ids = [s.id for s in screens]
    if not screen_ids:
        return {}

    rows = db.scalars(
        select(RoleScreenAccess).where(
            RoleScreenAccess.org_id == user.org_id,
            RoleScreenAccess.screen_id.in_(screen_ids),
            RoleScreenAccess.role_id.in_(user_role_ids),
            RoleScreenAccess.deleted_at.is_(None),
            _scope_predicate(candidate_units),
        )
    ).all()

    best_rank_by_screen: dict[str, int] = {}
    best_level_by_screen: dict[str, str] = {}
    winning_roles_by_screen: dict[str, set[str]] = {}
    for row in rows:
        kept = (
            row.org_unit_id is None
            or row.org_unit_id in exact_units
            or (row.role.allows_hierarchy_rollup and row.org_unit_id in candidate_units)
        )
        if not kept:
            continue
        rank = row.action_level.rank
        current_best = best_rank_by_screen.get(row.screen_id, 0)
        if rank > current_best:
            best_rank_by_screen[row.screen_id] = rank
            best_level_by_screen[row.screen_id] = row.action_level.code
            winning_roles_by_screen[row.screen_id] = {row.role_id}
        elif rank == current_best and rank > 0:
            winning_roles_by_screen.setdefault(row.screen_id, set()).add(row.role_id)

    screens_by_id = {s.id: s for s in screens}
    results: dict[str, ResolvedScreenAccess] = {}
    for screen_id, rank in best_rank_by_screen.items():
        screen = screens_by_id[screen_id]
        results[screen.code] = ResolvedScreenAccess(
            screen_code=screen.code,
            screen_id=screen.id,
            org_unit_id=org_unit_id,
            level=best_level_by_screen[screen_id],
            rank=rank,
            role_ids=tuple(sorted(winning_roles_by_screen.get(screen_id, ()))),
            reason="granted",
        )
    return results


def assert_screen_level(
    db: Session,
    *,
    user: User,
    screen_code: str,
    min_level: str,
    org_unit_id: str | None = None,
    at: datetime | None = None,
) -> ResolvedScreenAccess:
    """``resolve_screen_access``, but records an FR-28 denial via
    ``app.core.authz.record_decision`` and raises ``HTTPException(403, ...)``
    when the resolved rank is below ``LEVEL_RANK[min_level]``."""
    resolved = resolve_screen_access(
        db, user=user, screen_code=screen_code, org_unit_id=org_unit_id, at=at
    )
    if resolved.rank < LEVEL_RANK[min_level]:
        from app.core.authz import record_decision

        record_decision(
            user=user,
            action=f"screen:{screen_code}:{min_level}",
            outcome="denied",
            resource_type="screen",
            resource_id=resolved.screen_id or screen_code,
            reason=resolved.reason,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Access denied: {min_level} access to '{screen_code}' is required",
        )
    return resolved
