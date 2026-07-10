from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)


class EthicalWall(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """A conflict-of-interest screen: a HARD DENY that bars a set of principals
    from a scoped resource, overriding every ALLOW layer (ownership, grants, even
    org-admin). This is the deny-override the whole legal-RBAC pipeline honours
    first — see app/contracts/access.py.

    scope_type is 'contract' (bar people from one matter) or 'project' (bar them
    from every contract in a matter/workspace). Barred principals live in
    ``ethical_wall_principal`` and may be individual users or whole roles.
    """

    name = Column(String(200), nullable=False)
    reason = Column(Text, nullable=True)
    scope_type = Column(String(40), index=True, nullable=False)  # 'contract' | 'project'
    scope_id = Column(String(36), index=True, nullable=False)
    active = Column(Boolean, index=True, nullable=False, default=True)
    deactivated_at = Column(DateTime(timezone=True), nullable=True)

    principals = relationship(
        "EthicalWallPrincipal",
        back_populates="wall",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class EthicalWallPrincipal(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    wall_id = Column(
        String(36), ForeignKey("ethical_wall.id", ondelete="CASCADE"), index=True, nullable=False
    )
    principal_type = Column(String(40), nullable=False)  # 'user' | 'role'
    principal_id = Column(String(36), index=True, nullable=False)

    wall = relationship("EthicalWall", back_populates="principals")
