from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import ApprovalStatus

# Membership of users in an approver group (e.g. who counts as "Legal Counsel").
# A user may belong to many groups, and a group has many members — this is the
# pool a routing step draws its approver from.
approver_group_member = Table(
    "approver_group_member",
    Base.metadata,
    Column("group_id", ForeignKey("approver_group.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
)


class ApproverGroup(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """A named pool of approvers for a business function (Legal Counsel, Finance,
    Procurement, …). The *function* lives here as data — not as a duplicated RBAC
    role — so the same single ``approval:decide`` permission gates every member."""

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    members = relationship("User", secondary=approver_group_member, lazy="selectin")

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_approver_group_org_name"),)


class ApprovalRoutingRule(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    name = Column(String(255), nullable=False)
    priority = Column(String(40), nullable=False, default="100")
    criteria = Column(JSON, nullable=False, default=dict)
    # Legacy single-approver fields, kept as a fallback when a rule has no steps.
    approver_role = Column(String(120), nullable=True)
    approver_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    # Ordered approval chain. When present, this is authoritative over the legacy
    # single-approver columns above.
    steps = relationship(
        "ApprovalRoutingStep",
        order_by="ApprovalRoutingStep.step_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ApprovalRoutingStep(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """One ordered stage of a routing rule's chain. Its approver is a group
    (any/all members), a specific user, or a role — resolved at submit time."""

    rule_id = Column(
        String(36), ForeignKey("approval_routing_rule.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    step_order = Column(Integer, nullable=False, default=1)
    approver_group_id = Column(String(36), ForeignKey("approver_group.id"), nullable=True)
    approver_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    approver_role = Column(String(120), nullable=True)
    # "any" = any one member of the group may approve (default); "all" = every
    # member must approve. Ignored for specific-user / role targets.
    mode = Column(String(20), nullable=False, default="any")


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
    approver_group_id = Column(String(36), ForeignKey("approver_group.id"), index=True, nullable=True)
    routing_rule_id = Column(String(36), ForeignKey("approval_routing_rule.id"), nullable=True)
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
