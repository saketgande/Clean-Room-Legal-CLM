from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)


class AuthorityGrant(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """A Delegation-of-Authority policy — the ABAC action gate (Method 7).

    RBAC answers "may this person approve/sign at all?"; this answers "does their
    *authority* cover THIS contract?" A grant authorises a principal (user / role)
    to perform one high-stakes ``action`` (``contract:approve`` / ``contract:sign``)
    on contracts whose attributes fall within the stated limits:

        value <= max_value (in matching currency),
        contract_type in allowed_contract_types (empty = any),
        jurisdiction in allowed_jurisdictions (empty = any),
        risk_band <= max_risk_band (null = any).

    A grant with ``delegated_by_user_id`` set is a *temporary delegation* — one
    person handing their authority to another for a window (cover during leave).
    The gate is progressive: an action is only enforced once at least one policy
    exists for it, so turning Phase 4 on doesn't retroactively block anyone.
    """

    principal_type = Column(String(40), index=True, nullable=False)  # 'user' | 'role'
    principal_id = Column(String(36), index=True, nullable=False)
    action = Column(String(60), index=True, nullable=False)  # 'contract:approve' | 'contract:sign'
    max_value = Column(Float, nullable=True)  # NULL = unlimited
    currency = Column(String(3), nullable=True)
    allowed_contract_types = Column(JSON, nullable=True)  # list[str]; NULL/[] = any
    allowed_jurisdictions = Column(JSON, nullable=True)  # list[str]; NULL/[] = any
    max_risk_band = Column(String(40), nullable=True)  # 'low'|'medium'|'high'|'critical'; NULL = any
    # Temporary delegation: the person who handed over their authority (NULL for a
    # standing, org-defined authority).
    delegated_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    note = Column(Text, nullable=True)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)  # NULL = no expiry
    revoked_at = Column(DateTime(timezone=True), nullable=True)
