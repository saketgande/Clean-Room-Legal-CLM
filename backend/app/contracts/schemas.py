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


class ContractUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    contract_type: str | None = None
    counterparty_name: str | None = None
    jurisdiction: str | None = None
    risk_level: str | None = None
    metadata_json: dict | None = None


class LifecycleTransitionRequest(BaseModel):
    to_stage: ContractLifecycleStage
    reason: str | None = None
    override: bool = False
