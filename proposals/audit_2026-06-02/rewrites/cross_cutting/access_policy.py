"""Cross-cutting decision CC-1 — typed Contract access policy.

Replaces the bare ``accessible_contract_filter`` helper at
``backend/app/contracts/access.py:11-13`` whose admin branch returned
``true()`` and could leak cross-org rows when a join did not also
constrain ``Contract.org_id`` (Agent 2 finding F-03).

This module is intended to live at ``backend/app/core/access_policy.py``
once merged. Every caller that joins ``Contract`` must run its query
through ``ContractAccessPolicy.scope_query`` so the
``Contract.org_id == user.org_id`` predicate is always present, even
for admins, before the membership/share filter is applied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, and_, or_
from sqlalchemy.sql import ColumnElement

from app.auth.models import User
from app.contracts.models import Contract
from app.core.access import is_org_admin
from app.core.database import utcnow
from app.projects.models import Project, ProjectContract, ProjectMember, ProjectShare

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from app.assistant.models import AssistantSession


class ContractAccessPolicy:
    """Centralized contract-access predicates with mandatory org scoping."""

    @staticmethod
    def scope_query(
        query: Select,
        user: User,
        *,
        require_org_match: bool = True,
    ) -> Select:
        """Apply org-scope and access predicates to a Select that joins Contract."""
        clauses: list[ColumnElement[bool]] = []
        if require_org_match:
            clauses.append(Contract.org_id == user.org_id)
        clauses.append(ContractAccessPolicy._access_predicate(user))
        return query.where(*clauses)

    @staticmethod
    def access_predicate(user: User) -> ColumnElement[bool]:
        """Return the AND of org-scope and membership/share predicates."""
        return and_(
            Contract.org_id == user.org_id,
            ContractAccessPolicy._access_predicate(user),
        )

    @staticmethod
    def _access_predicate(user: User) -> ColumnElement[bool]:
        """Return only the row-level membership/share/admin predicate."""
        if is_org_admin(user):
            # Admins still need an org filter (applied by the caller) — but
            # within their org they see every contract. Returning a tautology
            # here is safe because scope_query always pairs it with the org
            # filter above.
            return Contract.org_id == user.org_id
        project_membership = (
            ProjectContract.__table__.select()
            .with_only_columns(ProjectContract.id)
            .select_from(
                ProjectContract.__table__.join(
                    Project.__table__,
                    Project.id == ProjectContract.project_id,
                ).join(
                    ProjectMember.__table__,
                    and_(
                        ProjectMember.project_id == ProjectContract.project_id,
                        ProjectMember.org_id == ProjectContract.org_id,
                    ),
                )
            )
            .where(
                ProjectContract.contract_id == Contract.id,
                ProjectContract.org_id == user.org_id,
                Project.deleted_at.is_(None),
                ProjectMember.user_id == user.id,
            )
            .exists()
        )
        project_share = (
            ProjectContract.__table__.select()
            .with_only_columns(ProjectContract.id)
            .select_from(
                ProjectContract.__table__.join(
                    Project.__table__,
                    Project.id == ProjectContract.project_id,
                ).join(
                    ProjectShare.__table__,
                    and_(
                        ProjectShare.project_id == ProjectContract.project_id,
                        ProjectShare.org_id == ProjectContract.org_id,
                    ),
                )
            )
            .where(
                ProjectContract.contract_id == Contract.id,
                ProjectContract.org_id == user.org_id,
                Project.deleted_at.is_(None),
                ProjectShare.shared_with_user_id == user.id,
                ProjectShare.revoked_at.is_(None),
                or_(
                    ProjectShare.expires_at.is_(None),
                    ProjectShare.expires_at > utcnow(),
                ),
            )
            .exists()
        )
        return or_(
            Contract.owner_user_id == user.id,
            Contract.created_by_user_id == user.id,
            project_membership,
            project_share,
        )

    @staticmethod
    def can_admin_read_session(
        session: "AssistantSession",
        user: User,
    ) -> bool:
        """Return True if user is an org admin reading a same-org session."""
        return (
            session is not None
            and session.org_id == user.org_id
            and is_org_admin(user)
        )

    @staticmethod
    def can_read_session(
        session: "AssistantSession",
        user: User,
    ) -> bool:
        """Return True if user is the session creator or an org admin in same org."""
        if session is None or session.org_id != user.org_id:
            return False
        if session.created_by_user_id == user.id:
            return True
        return ContractAccessPolicy.can_admin_read_session(session, user)

    @staticmethod
    def can_write_session(
        session: "AssistantSession",
        user: User,
    ) -> bool:
        """Return True only for the session creator within the same org."""
        if session is None or session.org_id != user.org_id:
            return False
        return session.created_by_user_id == user.id
