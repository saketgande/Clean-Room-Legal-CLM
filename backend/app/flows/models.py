"""Workflow-engine tables.

A **Flow** is a reusable, versioned definition: selection *criteria* + an ordered
list of typed *steps* (stored inline as JSON — the builder just reorders the
list, no separate step table). A **FlowRun** is one ticket walking one flow; a
**FlowStepRun** is the per-step audit row that powers both the ticket stepper
and the timeline.
"""

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Integer, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)

# Step types the executor understands. Kept as a plain tuple (validated in the
# service) — no DB enum, matching house style.
STEP_TYPES = ("ai_task", "human_task", "clm_draft", "approval", "signature", "counterparty", "notify")


class Flow(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """A prebuilt, selectable workflow. ``steps`` is an ordered JSON list of
    ``{id, type, name, config}``; ``criteria`` is the when-to-pick-this matcher
    (same condition shape as IntakeRoutingRule)."""

    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    is_builtin = Column(Boolean, nullable=False, default=False)
    eval_order = Column(Integer, nullable=False, default=100)  # lower wins during selection
    version = Column(Integer, nullable=False, default=1)
    # selection criteria (all non-null conditions AND together)
    criteria = Column(JSON, nullable=False, default=dict)
    # ordered list of step dicts
    steps = Column(JSON, nullable=False, default=list)


class FlowRun(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """One ticket riding one flow. ``current_index`` points at the step in flight.
    ``contract_id`` is set once a clm_draft/counterparty step produces a contract."""

    request_id = Column(
        String(36), ForeignKey("intake_request.id", ondelete="CASCADE"), index=True, nullable=False
    )
    flow_id = Column(String(36), ForeignKey("flow.id", ondelete="SET NULL"), nullable=True)
    flow_name = Column(String(160), nullable=False)
    flow_version = Column(Integer, nullable=False, default=1)
    steps = Column(JSON, nullable=False, default=list)  # pinned snapshot of the flow's steps
    status = Column(String(24), index=True, nullable=False, default="running")  # running|waiting|complete|failed|cancelled
    current_index = Column(Integer, nullable=False, default=0)
    contract_id = Column(
        String(36), ForeignKey("contract.id", ondelete="SET NULL"), index=True, nullable=True
    )
    context = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=True)


class FlowStepRun(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    """Per-step audit + the thing waiting-resume keys off. ``waiting_job_id``
    links a step to the async AI job it enqueued, so the generic hook in
    _run_ai_job can find and advance it on completion."""

    flow_run_id = Column(
        String(36), ForeignKey("flow_run.id", ondelete="CASCADE"), index=True, nullable=False
    )
    idx = Column(Integer, nullable=False)
    step_type = Column(String(40), nullable=False)
    step_name = Column(String(160), nullable=False)
    status = Column(String(24), nullable=False, default="pending")  # pending|running|waiting_human|waiting_job|done|skipped|failed
    assignee_user_id = Column(String(36), ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    team_id = Column(String(36), nullable=True)
    waiting_job_id = Column(String(36), index=True, nullable=True)
    result = Column(JSON, nullable=True)
    note = Column(Text, nullable=True)
