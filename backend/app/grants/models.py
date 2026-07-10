from sqlalchemy import Column, DateTime, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)


class ResourceGrant(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """A single, time-bound grant of access to ONE object, for a user / role /
    group. This unifies the three fragmented sharing mechanisms (ProjectShare,
    ContractShare, Workflow.shared_user_ids) into one relationship model — the
    object#level@principal tuple the whole legal-RBAC pipeline reads from.

    ``org_id`` (via OrgScopedMixin) is kept for consistency with every other
    table; it is dormant scaffolding in this single-tenant deployment.
    """

    principal_type = Column(String(40), index=True, nullable=False)  # 'user' | 'role' | 'group'
    principal_id = Column(String(36), index=True, nullable=False)
    resource_type = Column(String(60), index=True, nullable=False)  # 'contract' | 'project' | 'playbook'
    resource_id = Column(String(36), index=True, nullable=False)
    # read < comment < update < share < owner
    access_level = Column(String(40), nullable=False, default="read")
    note = Column(Text, nullable=True)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)  # NULL = no expiry
    revoked_at = Column(DateTime(timezone=True), nullable=True)
