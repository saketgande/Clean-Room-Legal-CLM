from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ShareAccessMode


class StorageObjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256_hash: str
    storage_backend: str


class ContractVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    contract_file_id: str
    version_number: int
    storage_object_id: str
    text_snapshot_id: str | None
    source: str
    change_summary: str | None
    is_authoritative: bool


class ContractFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    current_version_id: str | None
    file_label: str


class ContractTextSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    contract_version_id: str
    extraction_method: str
    extraction_quality_score: float
    ocr_provider: str | None
    validation_status: str | None


class ContractEditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    contract_version_id: str
    edit_type: str
    status: str
    original_text: str | None
    replacement_text: str | None
    rationale: str | None
    citation: list | None


class ContractEditDecisionRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class ContractShareCreate(BaseModel):
    contract_version_id: str | None = None
    access_mode: ShareAccessMode = ShareAccessMode.VIEW_ONLY
    expires_at: datetime | None = None
    passcode: str | None = Field(default=None, min_length=4, max_length=128)
    download_allowed: bool = False


class ContractShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    contract_version_id: str | None
    access_mode: str
    expires_at: datetime | None
    revoked_at: datetime | None
    download_allowed: bool


class ContractShareCreateResponse(BaseModel):
    share: ContractShareResponse
    token: str


class ExternalShareResponse(BaseModel):
    contract_id: str
    contract_version_id: str | None
    title: str
    filename: str | None = None
    access_mode: str
    download_allowed: bool
    text_excerpt: str | None = None
    text_truncated: bool = False
