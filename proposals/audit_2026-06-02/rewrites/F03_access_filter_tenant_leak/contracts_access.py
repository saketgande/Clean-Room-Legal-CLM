"""F-03 rewrite — ``contracts/access.py`` (no more bare ``true()`` for admins).

Replaces ``backend/app/contracts/access.py:11-13``. The previous body
returned ``true()`` when ``is_org_admin(user)``; combined with any
caller that joined ``Contract`` but did NOT also constrain
``Contract.org_id == user.org_id``, this could surface cross-org rows
(Agent 2 finding F-03 — Critical).

The new shape always emits ``Contract.org_id == user.org_id`` first,
then the per-role membership/share predicate. Admins remain "see
everything in their org" — they just stop seeing other orgs.
Delegates to ``ContractAccessPolicy`` (CC-1) so future tweaks happen
in one place.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.auth.models import User
from app.contracts.models import Contract
from app.core.access import is_org_admin
from app.core.access_policy import ContractAccessPolicy
from app.core.database import utcnow
from app.projects.models import Project, ProjectContract, ProjectMember, ProjectShare


def accessible_contract_filter(user: User) -> ColumnElement[bool]:
    """Back-compat shim: returns the AND of org-scope and access predicates."""
    return ContractAccessPolicy.access_predicate(user)


def user_can_access_contract(db: Session, *, contract: Contract, user: User) -> bool:
    """Return True only when contract is in user's org AND user can read it."""
    if contract is None or contract.org_id != user.org_id:
        return False
    if (
        is_org_admin(user)
        or contract.owner_user_id == user.id
        or contract.created_by_user_id == user.id
    ):
        return True
    membership = (
        select(ProjectContract.id)
        .join(Project, Project.id == ProjectContract.project_id)
        .join(
            ProjectMember,
            and_(
                ProjectMember.project_id == ProjectContract.project_id,
                ProjectMember.org_id == ProjectContract.org_id,
            ),
        )
        .where(
            ProjectContract.org_id == user.org_id,
            ProjectContract.contract_id == contract.id,
            Project.deleted_at.is_(None),
            ProjectMember.user_id == user.id,
        )
        .limit(1)
    )
    if db.scalar(membership) is not None:
        return True
    share = (
        select(ProjectContract.id)
        .join(Project, Project.id == ProjectContract.project_id)
        .join(
            ProjectShare,
            and_(
                ProjectShare.project_id == ProjectContract.project_id,
                ProjectShare.org_id == ProjectContract.org_id,
            ),
        )
        .where(
            ProjectContract.org_id == user.org_id,
            ProjectContract.contract_id == contract.id,
            Project.deleted_at.is_(None),
            ProjectShare.shared_with_user_id == user.id,
            ProjectShare.revoked_at.is_(None),
            or_(
                ProjectShare.expires_at.is_(None),
                ProjectShare.expires_at > utcnow(),
            ),
        )
        .limit(1)
    )
    return db.scalar(share) is not None
