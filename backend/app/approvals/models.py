from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
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
    # Soft-delete: a retired rule is skipped by the resolver but its row survives
    # so historical ApprovalRequest.routing_rule_id references still resolve.
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)

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
    # Steps sharing a stage run in parallel — every one must clear before the
    # chain advances. Defaults to step_order (each step its own sequential stage).
    stage = Column(Integer, nullable=True)
    # Optional list of {field, op, value} conditions; the step only joins the
    # chain when they all match the contract. Null / empty = always included.
    condition = Column(JSON, nullable=True)
    # Per-step SLA (hours) and where it escalates when overdue. Null sla_hours
    # falls back to the org-wide approval_default_due_days.
    sla_hours = Column(Integer, nullable=True)
    escalation_group_id = Column(String(36), ForeignKey("approver_group.id"), nullable=True)
    escalation_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    approver_group_id = Column(String(36), ForeignKey("approver_group.id"), nullable=True)
    approver_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    approver_role = Column(String(120), nullable=True)
    # "any" = any one member of the group may approve (default); "all" = every
    # member must approve. Ignored for specific-user / role targets.
    mode = Column(String(20), nullable=False, default="any")


class ApprovalRequest(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
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
    # Parallel-stage grouping (mirrors the resolved step's stage). All non-skipped
    # requests in a stage must be APPROVED before the next stage activates.
    stage = Column(Integer, nullable=True)
    # Escalation target (denormalized from the step at submit) + when the overdue
    # sweep escalated this request (set once, so it fires a single time).
    escalated_at = Column(DateTime(timezone=True), nullable=True)
    escalation_group_id = Column(String(36), ForeignKey("approver_group.id"), nullable=True)
    escalation_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
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
