from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import MatterStatus, MatterType


class MatterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    matter_type: MatterType = MatterType.GENERAL
    client_name: str | None = Field(default=None, max_length=255)
    status: MatterStatus = MatterStatus.ACTIVE
    metadata_json: dict = Field(default_factory=dict)


class MatterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    matter_type: MatterType | None = None
    client_name: str | None = None
    status: MatterStatus | None = None
    closed_at: datetime | None = None
    metadata_json: dict | None = None


class MatterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    name: str
    description: str | None
    matter_type: str
    owner_user_id: str
    matter_number: str | None
    client_name: str | None
    status: str
    opened_at: datetime | None
    closed_at: datetime | None
    metadata_json: dict


class MatterFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_folder_id: str | None = None


class MatterFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_folder_id: str | None = None


class MatterFolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    matter_id: str
    parent_folder_id: str | None
    name: str


class MatterMemberUpsert(BaseModel):
    user_id: str
    role: str = Field(default="member", min_length=1, max_length=120)


class MatterMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    matter_id: str
    user_id: str
    role: str


class MatterContractAdd(BaseModel):
    contract_id: str
    folder_id: str | None = None


class MatterContractUpdate(BaseModel):
    folder_id: str | None = None


class MatterContractResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    matter_id: str
    contract_id: str
    folder_id: str | None


class MatterShareCreate(BaseModel):
    user_id: str
    access_level: str = Field(default="read", pattern="^(read|update|share)$")
    expires_at: datetime | None = None


class MatterShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    matter_id: str
    shared_with_user_id: str
    access_level: str
    expires_at: datetime | None
    revoked_at: datetime | None


# --- Matter hub: overview roll-up, unfiled items, assignment -----------------
class MatterRollupItem(BaseModel):
    id: str
    title: str
    status: str | None = None
    meta: str | None = None


class MatterOverviewCounts(BaseModel):
    contracts: int = 0
    contracts_active: int = 0
    obligations_open: int = 0
    obligations_overdue: int = 0
    approvals_pending: int = 0
    notices_open: int = 0
    intake_open: int = 0


class MatterOverview(BaseModel):
    matter: MatterResponse
    counts: MatterOverviewCounts
    contracts: list[MatterRollupItem]
    obligations: list[MatterRollupItem]
    notices: list[MatterRollupItem]
    approvals: list[MatterRollupItem]
    intake: list[MatterRollupItem]


class UnfiledItem(BaseModel):
    id: str
    kind: str  # contract | intake
    title: str
    subtitle: str | None = None
    # Heuristic suggestion (counterparty <-> client match); not an LLM guess.
    suggested_matter_id: str | None = None
    suggested_matter_label: str | None = None


class AssignItemRequest(BaseModel):
    item_type: str  # contract | intake
    item_id: str
