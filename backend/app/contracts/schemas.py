from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ContractLifecycleStage


class ContractResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    title: str
    contract_type: str | None
    lifecycle_stage: str
    renewal_due: bool = False
    archived: bool = False
    owner_user_id: str
    counterparty_name: str | None
    jurisdiction: str | None
    confidentiality: str = "internal"
    risk_level: str | None
    risk_score: int | None = None
    risk_band: str | None = None
    risk_summary: dict | None = None
    value_amount: float | None
    currency: str | None
    effective_date: date | None
    expiration_date: date | None
    current_contract_file_id: str | None
    current_authoritative_version_id: str | None
    metadata_json: dict


class ContractUploadResponse(BaseModel):
    contract: ContractResponse
    contract_file_id: str
    contract_version_id: str
    text_snapshot_id: str | None
    extraction_method: str
    extraction_quality_score: float
    queued_jobs: list[str] = Field(default_factory=list)
    # Per-job errors raised during best-effort dispatch after the upload was
    # persisted. Empty on a fully successful upload.
    dispatch_errors: list[dict] = Field(default_factory=list)


class ContractUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    contract_type: str | None = None
    counterparty_name: str | None = None
    jurisdiction: str | None = None
    confidentiality: str | None = Field(
        default=None, pattern="^(public|internal|confidential|restricted)$"
    )
    risk_level: str | None = None
    value_amount: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    effective_date: date | None = None
    expiration_date: date | None = None
    metadata_json: dict | None = None


class LifecycleTransitionRequest(BaseModel):
    to_stage: ContractLifecycleStage
    reason: str | None = None
    override: bool = False
    signed_confirmation: bool = False


class LifecycleOptionsResponse(BaseModel):
    current_stage: str
    allowed_transitions: list[str]
    days_in_stage: int = 0
    stage_sla_days: int | None = None
    sla_breached: bool = False


class ContractStageHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    from_stage: str | None
    to_stage: str
    reason: str | None
    changed_by_user_id: str | None
    changed_at: datetime
    override_used: bool


class ContractActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    title: str
    details: dict | None
    request_id: str | None
    job_id: str | None
    skill_run_id: str | None
    assistant_run_id: str | None
    ai_call_id: str | None
    created_at: datetime


class ReviewChecklistItem(BaseModel):
    key: str
    label: str
    status: str  # done | todo | blocked | in_progress
    count: int = 0
    detail: str | None = None


class ReviewStatusResponse(BaseModel):
    contract_id: str
    lifecycle_stage: str
    ai_reviewed: bool
    open_issues: int
    high_severity_issues: int
    pending_redlines: int
    open_comments: int
    counterparty_active: bool
    ready_for_approval: bool
    next_step: str
    next_action: str | None = None
    checklist: list[ReviewChecklistItem]


class ContractPartyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    name: str
    party_type: str | None
    contact_email: str | None


class ContractPartyCreate(BaseModel):
    name: str
    contact_email: str | None = None
    party_type: str | None = None


class SignerOption(BaseModel):
    name: str
    email: str
    kind: str  # party | user


class DiffLine(BaseModel):
    type: str  # context | add | remove
    text: str


class VersionDiffResponse(BaseModel):
    base_version_id: str
    base_version_number: int
    target_version_id: str
    target_version_number: int
    added: int
    removed: int
    truncated: bool
    lines: list[DiffLine]
