from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    DocumentExtractTemplate,
    SourceStatus,
    TrademarkRiskLevel,
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
    source_document_extract_id: str | None
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


class DashboardMetrics(BaseModel):
    total_trademarks: int
    active_prosecutions: int
    upcoming_renewals: int
    by_status: dict[str, int]


class RenewalCalendarEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str
    jurisdiction: str
    renewal_due_on: datetime


# ---------------------------------------------------------------------------
# Search-similar
# ---------------------------------------------------------------------------


class SearchSimilarRequest(BaseModel):
    trademark_name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    trademark_type: TrademarkType = TrademarkType.WORD_MARK
    jurisdictions: list[str] = Field(default_factory=lambda: ["IN"])
    nice_class_hint: list[str] = Field(default_factory=list)


class InternalPortfolioResult(BaseModel):
    trademark_id: str
    name: str
    similarity_score: float
    status: str
    jurisdiction: str
    nice_class: str | None = None
    filed_on: datetime | None = None
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
    trademark_ids: list[str]


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
