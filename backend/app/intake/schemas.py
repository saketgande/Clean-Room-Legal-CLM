from __future__ import annotations

from pydantic import BaseModel, Field

# ---- request types --------------------------------------------------------

class FieldSpec(BaseModel):
    key: str
    label: str
    kind: str = Field(default="text", pattern="^(text|textarea|select|date|number|boolean)$")
    required: bool = False
    sort_order: int = 100
    options: list[dict] | None = None


class RequestTypeCreate(BaseModel):
    key: str
    name: str
    workstream: str | None = None
    description: str | None = None
    stages: list[str] | None = None
    sort_order: int = 100
    fields: list[FieldSpec] = Field(default_factory=list)


class RequestTypeUpdate(BaseModel):
    name: str | None = None
    workstream: str | None = None
    description: str | None = None
    active: bool | None = None
    stages: list[str] | None = None
    sort_order: int | None = None
    fields: list[FieldSpec] | None = None  # replaced wholesale when provided


class RequestTypeResponse(BaseModel):
    id: str
    key: str
    name: str
    workstream: str | None = None
    description: str | None = None
    active: bool
    stages: list[str] | None = None
    sort_order: int
    fields: list[FieldSpec] = Field(default_factory=list)


# ---- requests -------------------------------------------------------------

class RequestCreate(BaseModel):
    type_label: str
    request_type_id: str | None = None
    department: str | None = None
    priority: str = Field(default="Medium", pattern="^(Critical|High|Medium|Low)$")
    description: str = ""
    field_values: dict | None = None
    requester_name: str | None = None
    source: str = Field(default="form", pattern="^(form|copilot|email|api|seed)$")


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
    action: str = Field(
        pattern="^(approved|edited_approved|rejected|reassigned|manual_close|snoozed|escalate)$"
    )
    assignee_user_id: str | None = None  # reassigned
    snoozed_until: str | None = None      # snoozed (ISO)
    comment: str | None = None
    edited_response: str | None = None    # edited_approved


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
    members: list[TeamMemberSpec] = Field(default_factory=list)


class TeamUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    active: bool | None = None
    strategy: str | None = Field(default=None, pattern="^(least_loaded|round_robin)$")
    overflow_team_id: str | None = None
    sort_order: int | None = None
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
    members: list[TeamMemberResponse] = Field(default_factory=list)


# ---- routing rules --------------------------------------------------------

class RuleCreate(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True
    eval_order: int = 100
    match_type: str | None = None
    match_priority: str | None = None
    match_department: str | None = None
    match_keyword: str | None = None
    match_complexity: str | None = None
    set_assignee_user_id: str | None = None
    set_priority: str | None = None
    set_sla_hours: int | None = None
    set_team_id: str | None = None
    escalate_to_user_id: str | None = None
    require_approval_from_user_id: str | None = None


class RuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    eval_order: int | None = None
    match_type: str | None = None
    match_priority: str | None = None
    match_department: str | None = None
    match_keyword: str | None = None
    match_complexity: str | None = None
    set_assignee_user_id: str | None = None
    set_priority: str | None = None
    set_sla_hours: int | None = None
    set_team_id: str | None = None
    escalate_to_user_id: str | None = None
    require_approval_from_user_id: str | None = None


class RuleResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    enabled: bool
    eval_order: int
    match_type: str | None = None
    match_priority: str | None = None
    match_department: str | None = None
    match_keyword: str | None = None
    match_complexity: str | None = None
    set_assignee_user_id: str | None = None
    set_assignee_name: str | None = None
    set_priority: str | None = None
    set_sla_hours: int | None = None
    set_team_id: str | None = None
    set_team_name: str | None = None
    escalate_to_user_id: str | None = None
    escalate_to_name: str | None = None
    require_approval_from_user_id: str | None = None
    require_approval_from_name: str | None = None
    times_fired: int
    last_fired_at: str | None = None


# ---- recommendation / verdicts / promote ----------------------------------

class RecommendationResponse(BaseModel):
    id: str
    request_id: str
    agent_id: str
    confidence: float
    suggested_action: str
    drafted_response: str
    reasoning: str
    concerns: list[str] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    degraded: bool
    status: str
    reviewed_by_user_id: str | None = None
    can_auto_send: bool


class BulkTriageRequest(BaseModel):
    ids: list[str]
    action: str = Field(pattern="^(approved|manual_close)$")


class PromoteRequest(BaseModel):
    target: str = Field(pattern="^(project|contract)$")
    target_id: str


# ---- knowledge base -------------------------------------------------------

class KbCreate(BaseModel):
    source_ref: str
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)


class KbUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    active: bool | None = None


class KbResponse(BaseModel):
    id: str
    source_ref: str
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    active: bool


# ---- copilot (conversational filing) --------------------------------------

class CopilotMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class CopilotTurnRequest(BaseModel):
    messages: list[CopilotMessage] = Field(default_factory=list)
    user_message: str


class CopilotTurnResponse(BaseModel):
    reply: str
    extracted: dict = Field(default_factory=dict)
    ready: bool = False
    suggested_type_label: str | None = None


class CopilotFileRequest(BaseModel):
    messages: list[CopilotMessage] = Field(default_factory=list)
    type_label: str
    description: str
    field_values: dict | None = None


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
    request_type_id: str | None = None
    type_label: str
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
    agent_outcome: str | None = None
    ai_triage: dict | None = None
    fired_rules: dict | None = None
    screening: dict | None = None
    parties: list[dict] = Field(default_factory=list)
    handoff_holder: str | None = None
    handoff_user_id: str | None = None
    project_id: str | None = None
    contract_id: str | None = None
    contract_title: str | None = None
    workflow: list[WorkflowStep] = Field(default_factory=list)
    created_at: str | None = None
