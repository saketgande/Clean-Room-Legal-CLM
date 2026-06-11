from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import UserStatus


user_role_table = Table(
    "user_role",
    Base.metadata,
    Column("user_id", ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
)

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
    permissions = relationship("Permission", secondary=role_permission_table, lazy="selectin")

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_role_org_name"),)


class User(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    email = Column(String(320), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(500), nullable=False)
    status = Column(String(40), index=True, nullable=False, default=UserStatus.PENDING_APPROVAL)
    active_role_id = Column(String(36), ForeignKey("role.id"), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    preferences = Column(JSON, nullable=False, default=dict)
    roles = relationship("Role", secondary=user_role_table, lazy="selectin")

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
    token_hash = Column(String(255), nullable=False)
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
    key_hash = Column(String(255), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class UserInvitation(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    email = Column(String(320), index=True, nullable=False)
    role_name = Column(String(120), nullable=False, default="member")
    token_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class OrgJoinRequest(TableNameMixin, IdMixin, TimestampMixin, Base):
    org_id = Column(String(36), ForeignKey("organization.id"), index=True, nullable=True)
    email = Column(String(320), index=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    requested_domain = Column(String(255), index=True, nullable=True)
    message = Column(Text, nullable=True)
    status = Column(String(40), index=True, nullable=False, default="pending")
    decided_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    decision_reason = Column(Text, nullable=True)


class UserApprovalDecision(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    target_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    decision = Column(String(40), index=True, nullable=False)
    reason = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
