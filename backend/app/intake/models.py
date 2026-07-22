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
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
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


class IntakeRequestType(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """A no-code request type: typed capture fields + an ordered stage workflow.
    Mirrors the Matter/type-config pattern; surfaces on the New Request form the
    instant it's saved."""

    key = Column(String(60), nullable=False)  # ^[a-z0-9][a-z0-9_-]*$
    name = Column(String(120), nullable=False)
    workstream = Column(String(120), nullable=True)
    description = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    stages = Column(JSON, nullable=True)  # ordered custom mid-stage names; NULL = default spine
    sort_order = Column(Integer, nullable=False, default=100)

    fields = relationship(
        "IntakeRequestField",
        back_populates="request_type",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="IntakeRequestField.sort_order",
    )


class IntakeRequestField(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    request_type_id = Column(
        String(36), ForeignKey("intake_request_type.id", ondelete="CASCADE"), nullable=False
    )
    key = Column(String(60), nullable=False)
    label = Column(String(120), nullable=False)
    kind = Column(String(20), nullable=False, default="text")  # text|textarea|select|date|number|boolean
    required = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=100)
    options = Column(JSON, nullable=True)  # [{value,label}] for select

    request_type = relationship("IntakeRequestType", back_populates="fields")


class IntakeTeam(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """A claimable pool / tier. Routing rules point at these; the balancer
    (least_loaded | round_robin) picks a member, chaining to overflow when full."""

    key = Column(String(60), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    strategy = Column(String(20), nullable=False, default="least_loaded")  # least_loaded|round_robin
    overflow_team_id = Column(
        String(36), ForeignKey("intake_team.id", ondelete="SET NULL"), nullable=True
    )
    sort_order = Column(Integer, nullable=False, default=100)

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


class IntakeRoutingRule(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """No-code when->then. Conditions AND together; actions are cumulative.
    Evaluated in eval_order inside the save chokepoint. Never overrides a human."""

    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    eval_order = Column(Integer, nullable=False, default=100)
    # conditions (all non-null AND)
    match_type = Column(String(120), nullable=True)
    match_priority = Column(String(20), nullable=True)
    match_department = Column(String(60), nullable=True)
    match_keyword = Column(String(200), nullable=True)  # ci substring of description
    match_complexity = Column(String(20), nullable=True)  # simple|standard|complex
    # actions (cumulative)
    set_assignee_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    set_priority = Column(String(20), nullable=True)
    set_sla_hours = Column(Integer, nullable=True)
    set_team_id = Column(
        String(36), ForeignKey("intake_team.id", ondelete="SET NULL"), nullable=True
    )
    escalate_to_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    require_approval_from_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    times_fired = Column(Integer, nullable=False, default=0)
    last_fired_at = Column(DateTime(timezone=True), nullable=True)


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
    request_type_id = Column(
        String(36), ForeignKey("intake_request_type.id", ondelete="SET NULL"), nullable=True
    )
    type_label = Column(String(120), nullable=False)
    description = Column(Text, nullable=False, default="")
    field_values = Column(JSON, nullable=True)  # {field.key: value}
    priority = Column(String(20), nullable=False, default="Medium")  # Critical|High|Medium|Low
    # status ∈ awaiting_triage|in_review|escalated|approved|closed  (NO 'rejected' — Part 0.8)
    status = Column(String(40), nullable=False, default="awaiting_triage")
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
    triaged_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    triaged_at = Column(DateTime(timezone=True), nullable=True)
    triage_action = Column(String(30), nullable=True)  # approved|edited_approved|rejected|reassigned|manual_close|snoozed
    snoozed_until = Column(DateTime(timezone=True), nullable=True)
    agent_processed_at = Column(DateTime(timezone=True), nullable=True)
    agent_outcome = Column(String(20), nullable=True)  # matched|no_match
    ai_triage = Column(JSON, nullable=True)
    fired_rules = Column(JSON, nullable=True)
    stage_timestamps = Column(JSON, nullable=True)  # [{stage, at}]
    conversation = Column(JSON, nullable=True)  # copilot transcript
    handoff_holder = Column(String(10), nullable=True)  # agent|human|queue
    handoff_user_id = Column(String(36), nullable=True)
    external_message_id = Column(String(200), nullable=True)
    screening = Column(JSON, nullable=True)  # {counterparty, sanctions, conflicts, relationship}
    parties = Column(JSON, nullable=True)  # [{name, role, is_person}] — counterparty + adverse/related
    project_id = Column(String(36), nullable=True)  # selective promotion (matter); no FK
    contract_id = Column(
        String(36), ForeignKey("contract.id", ondelete="SET NULL"), nullable=True
    )


class IntakeAgentRecommendation(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """The AI review artifact — the thing a human approves/edits/rejects. Model /
    prompt / telemetry evidence lives on the linked AISkillRun; the human-gate
    verdict lives on status + the linked AIConfirmation."""

    request_id = Column(
        String(36), ForeignKey("intake_request.id", ondelete="CASCADE"), nullable=False
    )
    agent_id = Column(String(60), nullable=False)  # nda_agent, faq_agent, ...
    confidence = Column(Float, nullable=False, default=0)  # 0..1
    suggested_action = Column(String(40), nullable=False, default="flag_for_review")
    drafted_response = Column(Text, nullable=False, default="")
    reasoning = Column(Text, nullable=False, default="")
    concerns = Column(JSON, nullable=True)  # string[]
    citations = Column(JSON, nullable=True)  # [{id,title}]
    short_form_reply = Column(Text, nullable=True)
    degraded = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default="pending")  # pending|approved|edited|rejected
    reviewed_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    override_reason = Column(String(300), nullable=True)
    edited_at = Column(DateTime(timezone=True), nullable=True)
    skill_run_id = Column(String(36), nullable=True)
    confirmation_id = Column(String(36), nullable=True)


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
    actor_type = Column(String(10), nullable=False, default="user")  # user|agent|system
    recommendation_id = Column(
        String(36), ForeignKey("intake_agent_recommendation.id", ondelete="SET NULL"), nullable=True
    )


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


class SanctionsListEntry(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    """A denied-party list row (e.g. OFAC SDN). Screening treats an empty or
    stale (>30d) list as 'unavailable' — never 'clear'."""

    source = Column(String(40), nullable=False)  # OFAC_SDN|DEMO
    source_ref = Column(String(60), nullable=False)
    name = Column(String(400), nullable=False)
    name_normalized = Column(String(400), nullable=False)
    programs = Column(String(400), nullable=True)
    refreshed_at = Column(DateTime(timezone=True), nullable=False)


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


class IntakeKbArticle(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """Admin-editable, citable knowledge for the FAQ/Policy-QA agents and the
    Self-Service portal — one source of truth for both."""

    source_ref = Column(String(120), nullable=False)  # citation label
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    tags = Column(JSON, nullable=True)  # string[] retrieval keywords
    active = Column(Boolean, nullable=False, default=True)
