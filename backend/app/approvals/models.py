from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Text

from app.core.database import ActorTrackedMixin, Base, IdMixin, OrgScopedMixin, TableNameMixin, TimestampMixin
from app.core.enums import ApprovalStatus


class ApprovalRoutingRule(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    name = Column(String(255), nullable=False)
    priority = Column(String(40), nullable=False, default="100")
    criteria = Column(JSON, nullable=False, default=dict)
    approver_role = Column(String(120), nullable=True)
    approver_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)


class ApprovalRequest(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=True)
    status = Column(String(80), index=True, nullable=False, default=ApprovalStatus.PENDING)
    requested_by_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    approver_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=True)
    approver_role = Column(String(120), nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ApprovalDecision(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    approval_request_id = Column(
        String(36), ForeignKey("approval_request.id"), index=True, nullable=False
    )
    approver_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=True)
    decision = Column(String(80), index=True, nullable=False)
    comment = Column(Text, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=False)


class ApprovalToken(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    approval_request_id = Column(
        String(36), ForeignKey("approval_request.id"), index=True, nullable=False
    )
    intended_approver_email = Column(String(320), index=True, nullable=False)
    token_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
