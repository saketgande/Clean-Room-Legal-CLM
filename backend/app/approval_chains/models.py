"""Dynamic, condition-driven approval chains (feature 004).

Six new tables, all prefixed ``approval_chain_``. Every FK to ``role.id``,
``user.id``, ``org_unit.id`` is declared BY STRING so this module imports
nothing from ``app.auth``, ``app.org_structure``, ``app.contracts`` or
``app.approvals`` (no import cycle) — see plan.md "Database schema".

Five of the six tables use the standard mixin set
(``TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin,
SoftDeleteMixin, TimestampMixin``). ``ApprovalChainHistory`` deliberately
departs from that set — see its docstring — because FR-10/FR-11 require it to
be genuinely append-only: no ``updated_at``, no ``deleted_at``, no
``updated_by_user_id`` exist on it at all, and both an ORM event-listener
guard (below) and a Postgres trigger (in migration ``0044_approval_chains``)
independently reject any UPDATE/DELETE against it.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
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
    new_uuid,
    utcnow,
)


class ApprovalChainDefinition(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """One named, versioned approval chain for one subject type per org.

    ``module`` IS the subject-type discriminator (``"contract"`` |
    ``"intake_request"``) — see plan.md "Risks & decisions". At most one
    definition per ``(org_id, module)`` may be ``is_active`` at a time
    (enforced by the partial unique index below), which is what makes
    ``dispatch.active_definition_for`` deterministic.
    """

    name = Column(String(200), nullable=False)
    module = Column(String(40), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    is_default_seeded = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(
            "module IN ('contract', 'intake_request')",
            name="ck_approval_chain_definition_module",
        ),
        Index(
            "uq_approval_chain_definition_scope",
            "org_id",
            "name",
            "version",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_approval_chain_definition_active_module",
            "org_id",
            "module",
            unique=True,
            postgresql_where=text("is_active AND deleted_at IS NULL"),
            sqlite_where=text("is_active AND deleted_at IS NULL"),
        ),
        Index("ix_approval_chain_definition_lookup", "org_id", "module", "is_active"),
    )


class ApprovalChainStep(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """One ordered step of a chain definition. ``approval_mode`` is declared
    PER STEP (FR-12) so a single chain may mix sequential and parallel steps.
    """

    definition_id = Column(
        String(36), ForeignKey("approval_chain_definition.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_key = Column(String(80), nullable=False)
    name = Column(String(200), nullable=False)
    sequence_order = Column(Integer, nullable=False)
    step_type = Column(String(20), nullable=False, default="approval")
    approval_mode = Column(String(20), nullable=False, default="sequential")

    __table_args__ = (
        CheckConstraint("step_type IN ('action', 'approval')", name="ck_approval_chain_step_step_type"),
        CheckConstraint(
            "approval_mode IN ('sequential', 'parallel')", name="ck_approval_chain_step_approval_mode"
        ),
        Index(
            "uq_approval_chain_step_key",
            "definition_id",
            "step_key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_approval_chain_step_order",
            "definition_id",
            "sequence_order",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )


class ApprovalChainStepRule(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """The single config table for BOTH a step's unconditional base
    requirement(s) and its condition rules (FR-1). ``required_role`` is
    eagerly loaded (``lazy="joined"``) because every read renders the role's
    name (plan.md "Component design > Backend > Models").
    """

    step_id = Column(
        String(36), ForeignKey("approval_chain_step.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_base_requirement = Column(Boolean, nullable=False, default=False)
    condition_expression = Column(JSON(none_as_null=True), nullable=True)
    required_role_id = Column(String(36), ForeignKey("role.id", ondelete="RESTRICT"), nullable=False, index=True)
    sequence_order = Column(Integer, nullable=False, default=1)
    description = Column(String(300), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    required_role = relationship("Role", lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "(is_base_requirement AND condition_expression IS NULL) "
            "OR (NOT is_base_requirement AND condition_expression IS NOT NULL)",
            name="ck_approval_chain_step_rule_base_xor_condition",
        ),
        Index(
            "uq_approval_chain_step_rule_base",
            "step_id",
            "required_role_id",
            unique=True,
            postgresql_where=text("is_base_requirement AND deleted_at IS NULL"),
            sqlite_where=text("is_base_requirement AND deleted_at IS NULL"),
        ),
        Index("ix_approval_chain_step_rule_step", "step_id", "is_active", "deleted_at"),
    )


class ApprovalChainInstance(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """One running (or completed) chain against one business record.

    ``module_record_id`` has no FK because the target table varies with
    ``module`` (``contract.id`` or ``intake_request.id``) — the same pattern
    ``intake_request.matter_id`` already uses.
    """

    definition_id = Column(
        String(36),
        ForeignKey(
            "approval_chain_definition.id",
            ondelete="RESTRICT",
            name="fk_approval_chain_instance_definition_id_ac_definition",
        ),
        nullable=False,
        index=True,
    )
    module = Column(String(40), nullable=False)
    module_record_id = Column(String(36), nullable=False)
    org_unit_id = Column(String(36), ForeignKey("org_unit.id", ondelete="RESTRICT"), nullable=False, index=True)
    current_step_id = Column(
        String(36), ForeignKey("approval_chain_step.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    status = Column(String(20), nullable=False, default="pending", index=True)
    started_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint(
            "module IN ('contract', 'intake_request')", name="ck_approval_chain_instance_module"
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approval_chain_instance_status",
        ),
        Index("ix_approval_chain_instance_record", "org_id", "module", "module_record_id"),
        Index(
            "uq_approval_chain_instance_live",
            "definition_id",
            "module",
            "module_record_id",
            unique=True,
            postgresql_where=text("status = 'pending' AND deleted_at IS NULL"),
            sqlite_where=text("status = 'pending' AND deleted_at IS NULL"),
        ),
    )


class ApprovalChainRequirement(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """The FR-5 materialized snapshot — one row per required role per step per
    instance. This table is the frozen record; nothing writes to it except
    materialization, a decision, or an explicit recalculation. ``required_role``
    is eagerly loaded for the same reason as on ``ApprovalChainStepRule``.
    """

    instance_id = Column(
        String(36),
        ForeignKey(
            "approval_chain_instance.id",
            ondelete="CASCADE",
            name="fk_approval_chain_requirement_instance_id_ac_instance",
        ),
        nullable=False,
        index=True,
    )
    step_id = Column(
        String(36), ForeignKey("approval_chain_step.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    required_role_id = Column(String(36), ForeignKey("role.id", ondelete="RESTRICT"), nullable=False, index=True)
    sequence_order = Column(Integer, nullable=False)
    is_base_requirement = Column(Boolean, nullable=False, default=False)
    triggered_by_rule_ids = Column(JSON, nullable=False, default=list)
    condition_explanations = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="pending", index=True)
    counts_toward_completion = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    acted_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    acted_as_role_id = Column(String(36), ForeignKey("role.id"), nullable=True)
    delegated_from_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    acted_at = Column(DateTime(timezone=True), nullable=True)
    comment = Column(Text, nullable=True)
    is_unfulfillable = Column(Boolean, nullable=False, default=False)
    eligible_user_count = Column(Integer, nullable=False, default=0)
    materialized_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # foreign_keys is explicit because this table has a SECOND FK to role.id
    # (acted_as_role_id) — without it SQLAlchemy cannot determine which
    # column this relationship should join on.
    required_role = relationship("Role", lazy="joined", foreign_keys=[required_role_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approval_chain_requirement_status",
        ),
        Index(
            "uq_approval_chain_requirement_role",
            "instance_id",
            "step_id",
            "required_role_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND superseded_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL AND superseded_at IS NULL"),
        ),
        Index("ix_approval_chain_requirement_step", "instance_id", "step_id", "status"),
    )


class ApprovalChainHistory(TableNameMixin, IdMixin, OrgScopedMixin, Base):
    """The FR-10/FR-11 append-only action/decision history.

    Deliberately uses ONLY ``TableNameMixin, IdMixin, OrgScopedMixin`` — no
    ``TimestampMixin`` (its ``updated_at`` carries ``onupdate=utcnow``, a
    column whose whole purpose is recording a mutation that must never
    happen here), no ``SoftDeleteMixin`` (``deleted_at`` is a deletion
    channel), no ``ActorTrackedMixin`` (``updated_by_user_id`` is likewise an
    update channel). ``acted_at`` is the one timestamp on this table. This is
    a deliberate departure from the codebase's standard mixin set and the
    only table in this feature that departs from it.

    Append-only is enforced TWICE, independently:
    1. Here, via the ``before_update``/``before_delete`` ORM event listeners
       below, which reject any application-code mutation attempt.
    2. In migration ``0044_approval_chains``, via a Postgres
       ``BEFORE UPDATE OR DELETE`` trigger, which additionally rejects any
       raw SQL issued outside the ORM — this is what makes AC-10's "any code
       path, including an administrative one" literally true.
    """

    id = Column(String(36), primary_key=True, default=new_uuid)
    instance_id = Column(
        String(36), ForeignKey("approval_chain_instance.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    step_id = Column(String(36), ForeignKey("approval_chain_step.id", ondelete="RESTRICT"), nullable=True)
    requirement_id = Column(
        String(36),
        ForeignKey(
            "approval_chain_requirement.id",
            ondelete="RESTRICT",
            name="fk_approval_chain_history_requirement_id_ac_requirement",
        ),
        nullable=True,
    )
    action = Column(String(40), nullable=False, index=True)
    acted_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    acted_as_role_id = Column(String(36), ForeignKey("role.id"), nullable=True)
    delegated_from_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    comments = Column(Text, nullable=True)
    before_json = Column(JSON, nullable=True)
    after_json = Column(JSON, nullable=True)
    acted_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (
        CheckConstraint(
            "action IN ("
            "'instance_created', 'materialized', 'approved', 'rejected', "
            "'recalculated', 'blocked_no_eligible_approver', "
            "'instance_completed', 'instance_rejected'"
            ")",
            name="ck_approval_chain_history_action",
        ),
    )


@event.listens_for(ApprovalChainHistory, "before_update", propagate=True)
def _reject_history_update(mapper, connection, target):
    raise RuntimeError("approval_chain_history is append-only: append a new entry instead")


@event.listens_for(ApprovalChainHistory, "before_delete", propagate=True)
def _reject_history_delete(mapper, connection, target):
    raise RuntimeError("approval_chain_history is append-only: append a new entry instead")
