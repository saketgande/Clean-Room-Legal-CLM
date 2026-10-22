from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    DocumentExtractTemplate,
    SourceStatus,
    TrademarkDecision,
    TrademarkRiskLevel,
    TrademarkSearchPurpose,
    TrademarkStatus,
    TrademarkType,
)

# ---------------------------------------------------------------------------
# Trademark CRUD
# ---------------------------------------------------------------------------


class TrademarkCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    trademark_type: TrademarkType = TrademarkType.WORD_MARK
    jurisdiction: str = Field(min_length=1, max_length=120)
    jurisdictions: list[str] = Field(default_factory=list)
    nice_class: str | None = None
    goods_services: str | None = None
    filing_context: dict | None = None
    filed_on: datetime | None = None
    renewal_due_on: datetime | None = None


class TrademarkUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: TrademarkStatus | None = None
    trademark_type: TrademarkType | None = None
    jurisdiction: str | None = None
    jurisdictions: list[str] | None = None
    nice_class: str | None = None
    goods_services: str | None = None
    filing_context: dict | None = None
    filed_on: datetime | None = None
    renewal_due_on: datetime | None = None


class TrademarkCase(BaseModel):
    """Where a trademark's clearance case stands, read from the Legal Intake
    request that carries it and that request's workflow run."""

    request_id: str
    request_ref: str | None = None
    request_status: str
    flow_name: str | None = None
    run_status: str | None = None
    step_index: int | None = None
    step_total: int | None = None
    step_name: str | None = None
    step_status: str | None = None
    assignee_user_id: str | None = None
    assignee_name: str | None = None


class TrademarkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    name: str
    description: str | None
    status: str
    trademark_type: str
    jurisdiction: str
    jurisdictions: list[str] | None
    nice_class: str | None
    goods_services: str | None
    filing_context: dict | None
    filed_on: datetime | None
    renewal_due_on: datetime | None
    workflow_state: str
    source: str
    source_intake_request_id: str | None = None
    decision: str | None = None
    decision_conditions: str | None = None
    decided_by_user_id: str | None = None
    decided_at: datetime | None = None
    application_number: str | None = None
    filing_form: str | None = None
    portal_reference: str | None = None
    docket_number: str | None = None
    case: TrademarkCase | None = None
    created_at: datetime
    updated_at: datetime


class IntakeSubmitRequest(BaseModel):
    """Accumulated payload from the 4-step intake wizard."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    trademark_type: TrademarkType = TrademarkType.WORD_MARK
    nice_class: str | None = None
    goods_services: str | None = None
    jurisdictions: list[str] = Field(default_factory=lambda: ["IN"])
    filing_context: dict | None = None
    renewal_due_on: datetime | None = None
    search_query_id: str | None = None
    business_unit: str | None = Field(default=None, max_length=120)
    business_unit_spoc: str | None = Field(default=None, max_length=200)


class AwaitingAction(BaseModel):
    """A clearance case whose current workflow step is waiting on the caller."""

    trademark_id: str
    name: str
    step_name: str | None = None
    request_id: str


class DashboardMetrics(BaseModel):
    total_trademarks: int
    active_prosecutions: int
    upcoming_renewals: int
    by_status: dict[str, int]
    cases_by_step: dict[str, int] = Field(default_factory=dict)
    awaiting_my_action: list[AwaitingAction] = Field(default_factory=list)


class RenewalCalendarEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str
    jurisdiction: str
    nice_class: str | None = None
    filed_on: datetime | None = None
    description: str | None = None
    source: str
    renewal_due_on: datetime
    # True when renewal_due_on has already passed. Ported from the source
    # module's RenewalRecord.overdue — the Calendar/Dashboard both group on
    # this instead of recomputing "is this in the past" in three places.
    overdue: bool


class PortfolioRenewalsResponse(BaseModel):
    """Every trademark with a computable renewal date (stored
    renewal_due_on, or filed_on + statutory term as a fallback — see
    TrademarksService._effective_renewal_due), sorted soonest-due first,
    plus portfolio-level totals. Matches the source module's
    GET /api/portfolio/renewals shape."""

    records: list[RenewalCalendarEntry]
    total_with_dates: int
    skipped_unparseable: int
    overdue_count: int
    due_next_90d: int


class TrademarkSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str
    jurisdiction: str


# ---------------------------------------------------------------------------
# Search-similar
# ---------------------------------------------------------------------------


class SearchSimilarRequest(BaseModel):
    trademark_name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    trademark_type: TrademarkType = TrademarkType.WORD_MARK
    jurisdictions: list[str] = Field(default_factory=lambda: ["IN"])
    nice_class_hint: list[str] = Field(default_factory=list)
    # The intake form's "Minimum match to show" slider, ported from the
    # source module (its SearchSimilarModal forwards this straight through).
    # Only drops results that actually carry a similarity_score — web search
    # results have none, so they're never filtered by this.
    min_match_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # Searching from inside a clearance case saves the run straight onto it.
    trademark_id: str | None = None
    purpose: TrademarkSearchPurpose | None = None


class InternalPortfolioResult(BaseModel):
    trademark_id: str
    name: str
    similarity_score: float
    status: str
    jurisdiction: str
    nice_class: str | None = None
    filed_on: datetime | None = None
    risk_level: TrademarkRiskLevel


class DocumentExtractResult(BaseModel):
    extract_id: str
    name: str | None = None
    similarity_score: float
    jurisdiction: str | None = None
    tm_number: str | None = None
    filed_on: str | None = None
    goods_services: str | None = None
    source_filename: str
    page_start: int
    page_end: int
    risk_level: TrademarkRiskLevel


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    relevance: TrademarkRiskLevel = TrademarkRiskLevel.CONTEXT_ONLY


class SignaResult(BaseModel):
    signa_id: str
    mark_text: str
    similarity_score: float
    status_primary: str | None = None
    office_code: str | None = None
    filing_date: datetime | None = None
    owner_name: str | None = None
    nice_classes: list[int] = Field(default_factory=list)
    risk_level: TrademarkRiskLevel


class TmSearchResult(BaseModel):
    tmsearch_id: str
    mark_text: str
    similarity_score: float
    status: str | None = None
    office_code: str | None = None
    application_number: str | None = None
    registration_number: str | None = None
    filed_date: datetime | None = None
    protection_countries: list[str] = Field(default_factory=list)
    image_url: str | None = None
    risk_level: TrademarkRiskLevel


class SourceResult(BaseModel):
    status: SourceStatus
    provider: str | None = None
    results: list[dict] = Field(default_factory=list)
    error_message: str | None = None


class SearchSummary(BaseModel):
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    recommendation: str


class SearchSimilarResponse(BaseModel):
    query_id: str
    sources: dict[str, SourceResult]
    summary: SearchSummary


# ---------------------------------------------------------------------------
# Document extraction
# ---------------------------------------------------------------------------


class FieldDefinition(BaseModel):
    name: str
    data_type: Literal["string", "number", "date", "boolean"] = "string"
    order: int = 0
    anchor: str
    regex_override: str | None = None


class UploadDocumentResponse(BaseModel):
    doc_id: str
    filename: str
    total_pages: int


class ExtractRequest(BaseModel):
    doc_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    template: DocumentExtractTemplate = DocumentExtractTemplate.GENERIC
    field_schema: list[FieldDefinition] = Field(default_factory=list)


class ExtractedRecord(BaseModel):
    page_number: int
    entry_index: int = 0
    fields: dict = Field(default_factory=dict)
    used_ocr: bool = False
    warnings: list[str] = Field(default_factory=list)


class ExtractResponse(BaseModel):
    doc_id: str
    records: list[ExtractedRecord]
    warnings: list[str] = Field(default_factory=list)


class IngestRequest(BaseModel):
    doc_id: str
    source_filename: str
    page_start: int
    page_end: int
    template: DocumentExtractTemplate = DocumentExtractTemplate.GENERIC
    field_schema: list[FieldDefinition] = Field(default_factory=list)
    records: list[ExtractedRecord]


class IngestResponse(BaseModel):
    ingested_count: int
    record_ids: list[str]


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------


class IntegrationStatusEntry(BaseModel):
    configured: bool
    status: str
    detail: str | None = None


class IntegrationStatusResponse(BaseModel):
    signa: IntegrationStatusEntry
    serper: IntegrationStatusEntry
    postgres: IntegrationStatusEntry


class IntegrationTestResponse(BaseModel):
    status: str
    detail: str | None = None
    latency_ms: float | None = None


# ---------------------------------------------------------------------------
# NICE-class suggestion, conflict explanation, portfolio digest/stats
# ---------------------------------------------------------------------------


class ClassSuggestionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)


class ClassSuggestionResponse(BaseModel):
    nice_class: str
    heading: str
    reasoning: str
    cached: bool


class ExplainConflictRequest(BaseModel):
    trademark_name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    source: str
    conflict_name: str
    jurisdiction: str | None = None
    status: str | None = None
    match_score: float | None = None
    risk_level: TrademarkRiskLevel | None = None


class ExplainConflictResponse(BaseModel):
    explanation: str
    cached: bool


class PortfolioDigestResponse(BaseModel):
    digest_date: str
    summary: str
    stats: dict
    generated: bool


class PortfolioStatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_jurisdiction: dict[str, int]
    by_nice_class: dict[str, int]
    filed_last_30d: int
    filed_last_90d: int
    upcoming_renewals_90d: int
    renewal_pending: int
    lapsed: int
    by_step: dict[str, int] = Field(default_factory=dict)
    by_decision: dict[str, int] = Field(default_factory=dict)


class TrademarkFromIntakeRequest(BaseModel):
    """Whatever the intake request itself doesn't already capture — asked
    for once, at the point of "Continue as Trademark", not as a whole
    separate wizard."""

    trademark_type: TrademarkType = TrademarkType.WORD_MARK
    nice_class: str | None = None
    goods_services: str | None = None
    jurisdictions: list[str] = Field(default_factory=lambda: ["IN"])
    filing_context: dict | None = None
    renewal_due_on: datetime | None = None


# ---------------------------------------------------------------------------
# Clearance case: saved searches, decision, filing, activity
# ---------------------------------------------------------------------------


class TrademarkSearchRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    query_id: str
    trademark_id: str | None
    purpose: str | None
    risk_level: str | None
    notes: str | None
    query: dict
    summary: dict
    created_by_user_id: str | None
    created_at: datetime


class TrademarkSearchRunDetail(TrademarkSearchRunSummary):
    sources: dict


class SearchRunUpdate(BaseModel):
    risk_level: TrademarkRiskLevel | None = None
    notes: str | None = Field(default=None, max_length=4000)


class DecisionRequest(BaseModel):
    decision: TrademarkDecision
    conditions: str | None = Field(default=None, max_length=4000)
    # Sends the case to the escalation step (TM Head / GC) after this decision.
    escalate: bool = False


class FilingRequest(BaseModel):
    application_number: str | None = Field(default=None, max_length=120)
    filing_form: str | None = Field(default=None, max_length=60)
    portal_reference: str | None = Field(default=None, max_length=120)
    docket_number: str | None = Field(default=None, max_length=120)
    filed_on: datetime | None = None


class ActivityEntry(BaseModel):
    id: str
    event_type: str
    title: str
    actor_user_id: str | None
    actor_name: str | None
    created_at: datetime
    source: Literal["trademark", "request"]
