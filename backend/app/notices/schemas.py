from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.notices.extraction import PERIOD_UNITS, RUNS_FROM
from app.notices.models import DIRECTIONS, NOTICE_TYPES, PRIORITIES, STATUSES

_DIRECTION_RE = "^(" + "|".join(DIRECTIONS) + ")$"
_TYPE_RE = "^(" + "|".join(NOTICE_TYPES) + ")$"
_STATUS_RE = "^(" + "|".join(STATUSES) + ")$"
_PRIORITY_RE = "^(" + "|".join(PRIORITIES) + ")$"


class NoticeDeadlineFacts(BaseModel):
    """The notice's deadline wording as /notices/extract returned it. The server
    counts the date from these itself; a client-computed date is never trusted."""

    stated: bool = False
    explicit_date: date | None = None
    period_count: int | None = Field(default=None, ge=0, le=3650)
    period_unit: str | None = Field(default=None, pattern="^(" + "|".join(PERIOD_UNITS) + ")$")
    # "clear days": one day earlier than the plain count (app.core.deadlines.shift).
    clear_days: bool = False
    runs_from: str | None = Field(default=None, pattern="^(" + "|".join(RUNS_FROM) + ")$")
    runs_from_text: str | None = Field(default=None, max_length=300)
    quote: str | None = Field(default=None, max_length=1000)


class NoticeCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    counterparty_name: str = Field(min_length=1, max_length=200)
    direction: str = Field(default="received", pattern=_DIRECTION_RE)
    notice_type: str = Field(default="other", pattern=_TYPE_RE)
    description: str = ""
    counterparty_ref: str | None = Field(default=None, max_length=120)
    contract_id: str | None = None
    notice_date: date | None = None
    received_at: date | None = None
    response_due_date: date | None = None
    # Outbound notices normally start as 'draft'; inbound ones as 'open'.
    status: str | None = Field(default=None, pattern=_STATUS_RE)
    priority: str = Field(default="Medium", pattern=_PRIORITY_RE)
    # None on a received notice means "the filer", so the reminder sweep always
    # has someone to chase.
    owner_user_id: str | None = None
    # Deadline wording from /notices/extract. With it, response_due_date is the
    # code-computed date; a different response_due_date is a person's override.
    deadline_basis: NoticeDeadlineFacts | None = None
    # The filer ticked "I've checked the received date and deadline".
    deadline_confirmed: bool = False


class NoticeUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    counterparty_name: str | None = Field(default=None, min_length=1, max_length=200)
    direction: str | None = Field(default=None, pattern=_DIRECTION_RE)
    notice_type: str | None = Field(default=None, pattern=_TYPE_RE)
    description: str | None = None
    counterparty_ref: str | None = Field(default=None, max_length=120)
    contract_id: str | None = None
    notice_date: date | None = None
    received_at: date | None = None
    # Sent (even as null) = a person sets the deadline; null means "no deadline
    # applies". Omitted = untouched. Same for the other nullable dates/refs.
    response_due_date: date | None = None
    priority: str | None = Field(default=None, pattern=_PRIORITY_RE)
    owner_user_id: str | None = None
    # A person confirms the code-computed deadline as it stands.
    confirm_deadline: bool = False


class NoticeStatusUpdate(BaseModel):
    status: str = Field(pattern=_STATUS_RE)
    note: str | None = None
    # Only meaningful when moving to 'responded'.
    response_summary: str | None = None


class NoticeNoteCreate(BaseModel):
    body: str = Field(min_length=1)


class NoticeEscalate(BaseModel):
    """Escalation opens a linked intake ticket, which then carries routing, SLA
    and the approval ladder. The reason is mandatory — it becomes the ticket's
    description and the timeline entry, and 'why' is the part a reviewer needs."""

    reason: str = Field(min_length=1, max_length=2000)
    type_label: str | None = Field(default=None, max_length=120)
    priority: str | None = Field(default=None, pattern=_PRIORITY_RE)


class NoticeEventResponse(BaseModel):
    id: str
    kind: str
    body: str | None = None
    actor_user_id: str | None = None
    actor_name: str | None = None
    created_at: datetime


class NoticeResponse(BaseModel):
    id: str
    ref: str
    direction: str
    notice_type: str
    subject: str
    description: str
    counterparty_name: str
    counterparty_ref: str | None = None
    contract_id: str | None = None
    contract_title: str | None = None
    notice_date: date | None = None
    received_at: date | None = None
    response_due_date: date | None = None
    status: str
    priority: str
    owner_user_id: str | None = None
    owner_name: str | None = None
    responded_at: datetime | None = None
    response_summary: str | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Derived on read, never stored — see service.deadline_posture.
    deadline_posture: str  # none|needs_date|on_track|at_risk|overdue|met
    # How response_due_date was worked out: {facts, due, formula, reason,
    # needs_person, set_by: document|person, confirmed_by, confirmed_at,
    # needs_reconfirm, recomputed}. `recomputed` is code's recount after a
    # notice/received date moved under a confirmed date, which it never
    # overwrites; confirming accepts it. None when no deadline was ever recorded.
    deadline_basis: dict | None = None
    days_to_due: int | None = None
    # Most urgent reminder already sent to the owner (t7|t3|due|overdue).
    last_reminder_stage: str | None = None
    last_reminder_at: datetime | None = None
    # AI-drafted reply awaiting a lawyer's edit — a proposal, not what was sent.
    draft_response: str | None = None
    draft_response_at: datetime | None = None
    # Set when the notice has been escalated into the intake queue.
    escalated_intake_request_id: str | None = None
    escalated_intake_ref: str | None = None


class NoticeDocumentResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    extraction_quality: float | None = None
    has_text: bool = False
    created_at: datetime


class NoticeDetailResponse(NoticeResponse):
    events: list[NoticeEventResponse] = Field(default_factory=list)
    documents: list[NoticeDocumentResponse] = Field(default_factory=list)


class NoticeDraftResponse(NoticeDetailResponse):
    # False when the model was unavailable and a template skeleton was used —
    # the UI says so rather than passing boilerplate off as a drafted reply.
    draft_generated: bool = True


class NoticeExtractionResponse(BaseModel):
    """Proposed field values from an uploaded notice. Deliberately *suggestions*
    — the filer reviews them in the form before anything is saved, so a
    mis-read statutory deadline can never enter the register unreviewed."""

    suggestions: dict = Field(default_factory=dict)
    # The deadline wording read from the notice (send it back on create), and the
    # date code counts from it with today as the received date — a preview; the
    # saved date is recounted from the form's own dates.
    deadline_basis: dict | None = None
    deadline_preview: dict | None = None
    confidence: float = 0.0
    source: str = "llm"  # llm | heuristic | empty
    extraction_quality: float | None = None
    message: str | None = None


class NoticeSummary(BaseModel):
    """Counts for the register's filter tiles."""

    total: int = 0
    open: int = 0
    overdue: int = 0
    at_risk: int = 0
    responded: int = 0
    escalated: int = 0
    closed: int = 0
    draft: int = 0
    # Live notices whose deadline a person must set (no period stated, or one
    # code can't count).
    needs_date: int = 0
