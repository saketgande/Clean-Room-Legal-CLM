from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---- requests -------------------------------------------------------------

class AttachmentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    mime_type: str = "application/octet-stream"
    content_b64: str


class RequestCreate(BaseModel):
    type_label: str
    subject: str | None = None
    department: str | None = None
    priority: str = Field(default="Medium", pattern="^(Critical|High|Medium|Low)$")
    description: str = ""
    field_values: dict | None = None
    requester_name: str | None = None
    source: str = Field(default="form", pattern="^(form|copilot|email|api|seed)$")
    # Files sent with the filing itself: every one is checked before anything is
    # saved, so a bad file files nothing instead of leaving a half-made request.
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=5)


class DraftSave(BaseModel):
    form_key: str
    title: str | None = None
    values: dict = Field(default_factory=dict)
    parent_contract_id: str | None = None
    page_index: int = Field(default=0, ge=0, le=7)
    visited: int = Field(default=1, ge=1, le=8)


class DraftResponse(BaseModel):
    id: str
    form_key: str
    title: str | None = None
    values: dict
    parent_contract_id: str | None = None
    page_index: int
    visited: int
    created_at: datetime
    updated_at: datetime


class RequestUpdate(BaseModel):
    stage: str | None = None
    priority: str | None = Field(default=None, pattern="^(Critical|High|Medium|Low)$")
    department: str | None = None
    description: str | None = None
    field_values: dict | None = None
    work_status: str | None = Field(
        default=None, pattern="^(not_started|in_progress|blocked|delivered)$"
    )


class WorkflowStep(BaseModel):
    label: str
    stage: str
    done: bool
    active: bool


class HandoffCreate(BaseModel):
    to_holder: str = Field(pattern="^(agent|human|queue)$")
    to_user_id: str | None = None
    reason: str | None = None
    sync_assignee: bool = True


class HandoffResponse(BaseModel):
    id: str
    from_holder: str | None = None
    to_holder: str
    to_user_id: str | None = None
    to_label: str | None = None
    reason: str | None = None
    actor_type: str
    created_at: str | None = None


class TriageActionRequest(BaseModel):
    # Request-management actions (triage verdicts removed).
    action: str = Field(pattern="^(reassigned|manual_close|snoozed|escalate)$")
    assignee_user_id: str | None = None  # reassigned
    snoozed_until: str | None = None      # snoozed (ISO)
    comment: str | None = None


class TaskCreateReq(BaseModel):
    title: str
    description: str | None = None
    assignee_user_id: str | None = None
    sort_order: int = 100


class TaskUpdateReq(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee_user_id: str | None = None
    status: str | None = Field(default=None, pattern="^(open|in_progress|blocked|done)$")
    sort_order: int | None = None


class TaskResponse(BaseModel):
    id: str
    request_id: str
    title: str
    description: str | None = None
    assignee_user_id: str | None = None
    assignee_label: str | None = None
    status: str
    sort_order: int
    effort_minutes: int


class AssigneeResponse(BaseModel):
    id: str
    name: str
    email: str


# ---- teams / pools --------------------------------------------------------

class TeamMemberSpec(BaseModel):
    user_id: str
    capacity: int = 0
    active: bool = True


class TeamMemberResponse(BaseModel):
    id: str
    user_id: str
    name: str
    capacity: int
    active: bool
    open_count: int = 0


class TeamCreate(BaseModel):
    key: str
    name: str
    description: str | None = None
    strategy: str = Field(default="least_loaded", pattern="^(least_loaded|round_robin)$")
    overflow_team_id: str | None = None
    sort_order: int = 100
    expertise: list[str] = Field(default_factory=list)  # matter categories this team owns
    departments: list[str] = Field(default_factory=list)  # business units this team serves
    is_default_intake: bool = False
    members: list[TeamMemberSpec] = Field(default_factory=list)


class TeamUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    active: bool | None = None
    strategy: str | None = Field(default=None, pattern="^(least_loaded|round_robin)$")
    overflow_team_id: str | None = None
    sort_order: int | None = None
    expertise: list[str] | None = None
    departments: list[str] | None = None
    is_default_intake: bool | None = None
    members: list[TeamMemberSpec] | None = None


class TeamResponse(BaseModel):
    id: str
    key: str
    name: str
    description: str | None = None
    active: bool
    strategy: str
    overflow_team_id: str | None = None
    overflow_team_name: str | None = None
    sort_order: int
    is_default_intake: bool = False
    used_in: list[dict] = Field(default_factory=list)
    expertise: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    members: list[TeamMemberResponse] = Field(default_factory=list)


# ---- promote --------------------------------------------------------------

class PromoteRequest(BaseModel):
    target: str = Field(pattern="^contract$")
    target_id: str


class PartyIn(BaseModel):
    name: str
    role: str = "counterparty"  # counterparty|adverse|related|our_side
    is_person: bool = False


class PartiesUpdate(BaseModel):
    parties: list[PartyIn] = Field(default_factory=list)


class RequestResponse(BaseModel):
    id: str
    ref: str
    source: str
    requester_user_id: str
    requester_name: str | None = None
    department: str | None = None
    counterparty_id: str | None = None
    legal_entity_id: str | None = None
    type_label: str
    subject: str | None = None
    description: str
    field_values: dict | None = None
    priority: str
    status: str
    stage: str
    work_status: str | None = None
    assigned_to_user_id: str | None = None
    assigned_to_label: str | None = None
    approval_gate_user_id: str | None = None
    sla_hours: int
    sla_status: str
    sla_pct: int
    submitted_at: str | None = None
    closed_at: str | None = None
    triaged_by_user_id: str | None = None
    triage_action: str | None = None
    ai_triage: dict | None = None
    gates: dict | None = None
    screening: dict | None = None
    parties: list[dict] = Field(default_factory=list)
    handoff_holder: str | None = None
    handoff_user_id: str | None = None
    contract_id: str | None = None
    contract_title: str | None = None
    workflow: list[WorkflowStep] = Field(default_factory=list)
    created_at: str | None = None
    draftable_doc_type: str | None = None  # nda|msa|dpa|vendor|… — what "Approve & draft" makes
    draftable_doc_label: str | None = None
