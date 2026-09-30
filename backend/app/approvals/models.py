from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import ApprovalStatus


class ApprovalRequest(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    # A request hangs off EITHER a contract or an intake request (exactly one).
    # contract_id is nullable since the ladder generalised beyond contracts.
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=True)
    intake_request_id = Column(
        String(36), ForeignKey("intake_request.id", ondelete="CASCADE"), index=True, nullable=True
    )
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=True)
    status = Column(String(80), index=True, nullable=False, default=ApprovalStatus.PENDING)
    requested_by_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    approver_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=True)
    approver_role = Column(String(120), nullable=True)
    # Multi-step chain bookkeeping. A submission creates one request per step;
    # only step 1 starts PENDING, the rest start WAITING and are activated in
    # order as each preceding step is approved.
    # The team whose members may decide this rung (any one, or all — see mode).
    approver_team_id = Column(
        String(36), ForeignKey("intake_team.id", ondelete="SET NULL"), index=True, nullable=True
    )
    step_order = Column(Integer, nullable=False, default=1)
    # "any" = one member's approval clears the rung (default); "all" = every
    # member of the group must approve before it advances (quorum). The rung
    # stays PENDING, accumulating decisions, until the quorum is met.
    mode = Column(String(20), nullable=False, default="any")
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
    token_hash = Column(String(255), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
