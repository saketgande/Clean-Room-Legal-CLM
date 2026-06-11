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
    owner_user_id: str
    counterparty_name: str | None
    jurisdiction: str | None
    risk_level: str | None
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
