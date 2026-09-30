"""Legal Intake — the single front door for all legal work.

capture -> triage -> route -> SLA -> audit. AI agents draft; a named human
decides (via app/ai/confirmations.py + app/authority); every action is a
chain-sealed audit row surfaced as the request timeline.

Conventions match app/authority + app/grants: all models compose the standard
mixins, enum-ish fields are validated VARCHARs (never DB enums), list/dict
payloads are JSON. No SoftDeleteMixin — a closed request is terminal, never
deleted. ``org_id`` (via OrgScopedMixin) is dormant single-tenant scaffolding,
kept for consistency with every other table.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
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


class IntakeTeam(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """A team: the one kind of group of people who do the work (see
    intake/teams.py). Owns new requests (the default intake team, or triage's
    expertise match), does workflow steps, approves at approval steps. The
    balancer (least_loaded | round_robin) picks a member, chaining to overflow
    when everyone is full."""

    key = Column(String(60), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    strategy = Column(String(20), nullable=False, default="least_loaded")  # least_loaded|round_robin
    overflow_team_id = Column(
        String(36), ForeignKey("intake_team.id", ondelete="SET NULL"), nullable=True
    )
    # Takes new requests no expertise match claims. Exactly one per org.
    is_default_intake = Column(Boolean, nullable=False, default=False, server_default="false")
    sort_order = Column(Integer, nullable=False, default=100)
    # Context-aware routing: the matter categories this team owns (expertise /
    # skills) and the business-unit departments it serves. The triage assigns a
    # request's owner to the team whose expertise covers its category, narrowed
    # to the team that serves the request's department when there's a match.
    expertise = Column(JSON, nullable=True)
    departments = Column(JSON, nullable=True)

    members = relationship(
        "IntakeTeamMember",
        back_populates="team",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class IntakeTeamMember(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    team_id = Column(
        String(36), ForeignKey("intake_team.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(String(36), ForeignKey("user.id"), nullable=False)
    capacity = Column(Integer, nullable=False, default=0)  # 0 = unbounded
    active = Column(Boolean, nullable=False, default=True)
    last_assigned_at = Column(DateTime(timezone=True), nullable=True)  # round-robin cursor

    team = relationship("IntakeTeam", back_populates="members")


class IntakeRequest(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """The hub. One row per legal request. status and sla_status are two
    independent axes; closed_at stamps once on entering a terminal status so SLA
    evidence can't be rewritten by updated_at."""

    ref = Column(String(20), nullable=False)  # 'REQ-4123' from intake_ref_seq
    source = Column(String(20), nullable=False, default="form")  # form|copilot|email|api|seed
    requester_user_id = Column(String(36), ForeignKey("user.id"), nullable=False)
    requester_name = Column(String(200), nullable=True)
    department = Column(String(60), nullable=True)
    type_label = Column(String(120), nullable=False)
    subject = Column(String(200), nullable=True)  # short human title; falls back to description line 1
    description = Column(Text, nullable=False, default="")
    field_values = Column(JSON, nullable=True)  # {field.key: value}
    priority = Column(String(20), nullable=False, default="Medium")  # Critical|High|Medium|Low
    # status ∈ open|escalated|approved|closed  (triage removed — a filed request is
    # 'open' and flows straight to its workflow; 'approved'/'closed' are terminal)
    status = Column(String(40), nullable=False, default="open")
    stage = Column(String(60), nullable=False, default="new")
    work_status = Column(String(40), nullable=True)  # not_started|in_progress|blocked|delivered
    assigned_to_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    approval_gate_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    sla_hours = Column(Integer, nullable=False, default=24)
    sla_status = Column(String(20), nullable=False, default="on_track")  # on_track|at_risk|overdue
    paused_at = Column(DateTime(timezone=True), nullable=True)
    paused_ms_total = Column(BigInteger, nullable=False, default=0)
    submitted_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)  # stamped once on terminal (Part 0.10)
    # Last request-management action (reassign|manual_close|snoozed|escalate) + who did it.
    triaged_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    triaged_at = Column(DateTime(timezone=True), nullable=True)
    triage_action = Column(String(30), nullable=True)  # reassigned|manual_close|snoozed|escalate
    snoozed_until = Column(DateTime(timezone=True), nullable=True)
    # ai_triage holds the triage read + flow suggestion (feeds owner assignment
    # and workflow selection). Not the removed recommendation.
    ai_triage = Column(JSON, nullable=True)
    stage_timestamps = Column(JSON, nullable=True)  # [{stage, at}]
    conversation = Column(JSON, nullable=True)  # copilot transcript
    handoff_holder = Column(String(10), nullable=True)  # human|queue
    handoff_user_id = Column(String(36), nullable=True)
    external_message_id = Column(String(200), nullable=True)
    screening = Column(JSON, nullable=True)  # {counterparty, parties, relationship} — see intake/screening.py
    parties = Column(JSON, nullable=True)  # [{name, role, is_person}] — counterparty + adverse/related
    contract_id = Column(
        String(36), ForeignKey("contract.id", ondelete="SET NULL"), nullable=True
    )
    # The records picked in the wizard's lookups (the names are also kept in
    # field_values for display). Checked to exist in this org when filed.
    counterparty_id = Column(
        String(36), ForeignKey("counterparty.id", ondelete="SET NULL"), nullable=True, index=True
    )
    legal_entity_id = Column(
        String(36), ForeignKey("legal_entity.id", ondelete="SET NULL"), nullable=True, index=True
    )



class IntakeHandoff(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """Append-only custody ledger. Every baton pass starts a new SLA leg. Never
    updated, never deleted (except request cascade). created_at = event time."""

    request_id = Column(
        String(36), ForeignKey("intake_request.id", ondelete="CASCADE"), nullable=False
    )
    from_holder = Column(String(10), nullable=True)  # agent|human|queue; NULL on first
    to_holder = Column(String(10), nullable=False)  # agent|human|queue
    to_user_id = Column(String(36), nullable=True)  # required when to_holder=human
    reason = Column(String(300), nullable=True)
    actor_type = Column(String(10), nullable=False, default="user")  # user|system


class IntakeTask(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    request_id = Column(
        String(36), ForeignKey("intake_request.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    assignee_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    status = Column(String(20), nullable=False, default="open")  # open|in_progress|blocked|done
    sort_order = Column(Integer, nullable=False, default=100)
    effort_minutes = Column(Integer, nullable=False, default=0)


class IntakeDocument(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """An attachment on a request. Text is extracted on upload and folded into
    the ticket so the triage agents read what the requester attached."""

    request_id = Column(
        String(36), ForeignKey("intake_request.id", ondelete="CASCADE"), nullable=False
    )
    filename = Column(String(300), nullable=False)
    mime_type = Column(String(120), nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    extracted_text = Column(Text, nullable=True)
    extraction_quality = Column(Float, nullable=True)


class IntakeDraft(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    """A half-filled agreement-wizard form, saved by "Save as Draft".

    Deliberately not an IntakeRequest: a filed request starts triage, the SLA
    clock, board counts and approvals, and a draft must do none of that until
    it is submitted. Private to the person who saved it.
    """

    user_id = Column(String(36), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    form_key = Column(String(60), nullable=False)  # agreement_forms.json key
    title = Column(String(200), nullable=True)  # shown in the drafts list
    values = Column(JSON, nullable=False, default=dict)  # the wizard's answers
    parent_contract_id = Column(String(36), nullable=True)  # picker selection, resolved on resume
    page_index = Column(Integer, nullable=False, default=0)  # step to reopen on
    visited = Column(Integer, nullable=False, default=1)  # furthest step reached
