from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)


class OrgUnit(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """A node in an organization's internal hierarchy (e.g. Global -> Region ->
    Entity -> Business Unit). Exactly one root (``parent_id IS NULL``) per
    ``org_id`` is enforced both by ``uq_org_unit_single_root`` (a partial
    unique index, the concurrency guarantee) and by the service layer (the
    readable 409). Role grants (``UserRoleGrant`` in app.auth.models) roll up
    through this tree — see app.core.org_access.
    """

    name = Column(String(200), nullable=False)
    parent_id = Column(String(36), ForeignKey("org_unit.id", ondelete="RESTRICT"), nullable=True)

    __table_args__ = (
        Index("ix_org_unit_parent_id", "parent_id"),
        Index("ix_org_unit_org_parent", "org_id", "parent_id"),
        Index(
            "uq_org_unit_single_root",
            "org_id",
            unique=True,
            postgresql_where=text("parent_id IS NULL AND deleted_at IS NULL"),
            sqlite_where=text("parent_id IS NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("(parent_id IS NULL OR parent_id <> id)", name="no_self_parent"),
    )


class Delegation(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """A bounded, revocable delegation of a delegator's own eligibility to a
    delegate (FR-13..FR-18). Revocation is modeled as a status transition
    (``status='revoked'``) AND the standard soft-delete columns set together
    (see app.core.org_access.resolve_access step 5) — one source of truth for
    "is this delegation currently excluded".
    """

    delegator_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    delegate_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    role_id = Column(String(36), ForeignKey("role.id"), index=True, nullable=True)
    org_unit_id = Column(String(36), ForeignKey("org_unit.id"), index=True, nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), index=True, nullable=False, default="active")

    __table_args__ = (
        CheckConstraint("delegator_user_id <> delegate_user_id", name="distinct_parties"),
        CheckConstraint("end_date >= start_date", name="date_order"),
        CheckConstraint("status IN ('active', 'revoked')", name="status"),
        Index("ix_delegation_delegate_lookup", "delegate_user_id", "status", "end_date"),
        Index("ix_delegation_delegator", "delegator_user_id", "status"),
    )
