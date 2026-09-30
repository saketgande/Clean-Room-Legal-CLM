from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import UserStatus

role_permission_table = Table(
    "role_permission",
    Base.metadata,
    Column("role_id", ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permission.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(TableNameMixin, IdMixin, TimestampMixin, Base):
    value = Column(String(160), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)


class Role(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    name = Column(String(120), index=True, nullable=False)
    description = Column(Text, nullable=True)
    # FR-7: default rolls up the org-unit hierarchy; when False a grant of this
    # role satisfies a check only at the exact org unit it was granted at.
    allows_hierarchy_rollup = Column(Boolean, nullable=False, default=True)
    permissions = relationship("Permission", secondary=role_permission_table, lazy="selectin")

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_role_org_name"),)


class UserRoleGrant(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """A user's role grant, scoped to a specific org unit with an optional
    validity window (FR-6). Replaces the old flat ``user_role`` association
    table: a user may now hold the same role at several org units (so the
    composite ``(user_id, role_id)`` primary key is gone in favor of its own
    ``id``), and revocation/expiry need actor-tracked soft-delete columns an
    association Table cannot carry (FR-9, FR-10, FR-20).

    ``org_unit_id`` is declared by string (``"org_unit.id"``) rather than
    importing ``app.org_structure.models`` to avoid an
    auth -> org_structure -> auth import cycle (org_structure's ``Delegation``
    FKs to ``user``/``role``, both defined here).
    """

    __tablename__ = "user_role"

    user_id = Column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False)
    role_id = Column(String(36), ForeignKey("role.id", ondelete="CASCADE"), index=True, nullable=False)
    org_unit_id = Column(
        String(36), ForeignKey("org_unit.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)

    role = relationship("Role", lazy="joined")

    __table_args__ = (
        Index(
            "uq_user_role_scope",
            "user_id",
            "role_id",
            "org_unit_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_user_role_lookup", "user_id", "org_id", "deleted_at"),
    )


# Compatibility alias: existing readers (app/roles/service.py raw counts,
# User.roles below) keep working against the underlying table of the new
# mapped model above.
user_role_table = UserRoleGrant.__table__


class User(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    email = Column(String(320), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(500), nullable=False)
    status = Column(String(40), index=True, nullable=False, default=UserStatus.PENDING_APPROVAL)
    active_role_id = Column(String(36), ForeignKey("role.id"), nullable=True)
    # Phase 3 (MAC): the highest confidentiality level this user may read.
    #   public < internal < confidential < restricted
    # Defaults to 'confidential' so existing users keep access to every
    # public/internal/confidential contract; only 'restricted' matters require a
    # deliberate clearance bump. Admins bypass clearance (they administer it).
    clearance = Column(String(40), nullable=False, default="confidential")
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    preferences = Column(JSON, nullable=False, default=dict)
    # viewonly: grants now carry org_unit_id/validity-window/actor data an
    # implicit association-table insert cannot populate, so all writes go
    # through UserRoleGrant rows in the service layer (see app/roles/service.py
    # and app/org_structure/service.py). Filtered on deleted_at IS NULL only —
    # expiry is deliberately NOT expressed here (dialect-fragile, cached per
    # load); it is applied fresh on every call by
    # app.core.org_access.effective_permission_values / resolve_access.
    roles = relationship(
        "Role",
        secondary=user_role_table,
        viewonly=True,
        lazy="selectin",
        primaryjoin="and_(User.id == user_role.c.user_id, user_role.c.deleted_at.is_(None))",
        secondaryjoin="Role.id == user_role.c.role_id",
    )

    @property
    def permission_values(self) -> set[str]:
        if self.active_role_id:
            active_role = next((role for role in self.roles if role.id == self.active_role_id), None)
            if active_role is not None:
                return {permission.value for permission in active_role.permissions}
        values: set[str] = set()
        for role in self.roles:
            for permission in role.permissions:
                values.add(permission.value)
        return values


class RefreshToken(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class RevokedAccessToken(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    """Per-jti revocation row for an access token.

    Access tokens are short-lived (default 60 min) but a stolen one is valid
    for that whole window. Persisting the jti on revocation lets logout and
    password reset cut off in-flight tokens immediately. The table is pruned
    once ``expires_at`` passes — there's no point keeping rows that the JWT
    decoder will already reject on `exp` alone.
    """

    user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=True)
    jti = Column(String(80), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), index=True, nullable=False)
    reason = Column(String(120), nullable=True)


class PasswordResetToken(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)


class ApiKey(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    name = Column(String(255), nullable=False)
    key_hash = Column(String(255), nullable=False, index=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class UserInvitation(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    email = Column(String(320), index=True, nullable=False)
    role_name = Column(String(120), nullable=False, default="member")
    token_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class UserApprovalDecision(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    target_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    decision = Column(String(40), index=True, nullable=False)
    reason = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
