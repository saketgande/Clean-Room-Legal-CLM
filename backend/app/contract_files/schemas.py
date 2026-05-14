from pydantic import BaseModel, ConfigDict


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
