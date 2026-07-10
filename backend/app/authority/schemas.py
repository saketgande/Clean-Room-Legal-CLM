from pydantic import BaseModel, Field


class AuthorityCreate(BaseModel):
    principal_type: str = Field(pattern="^(user|role)$")
    principal_id: str
    action: str = Field(pattern="^(contract:approve|contract:sign)$")
    max_value: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    allowed_contract_types: list[str] = Field(default_factory=list)
    allowed_jurisdictions: list[str] = Field(default_factory=list)
    max_risk_band: str | None = None
    delegated_by_user_id: str | None = None
    note: str | None = None
    valid_until: str | None = None  # ISO datetime


class AuthorityUpdate(BaseModel):
    max_value: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    allowed_contract_types: list[str] | None = None
    allowed_jurisdictions: list[str] | None = None
    max_risk_band: str | None = None
    note: str | None = None
    valid_until: str | None = None


class AuthorityResponse(BaseModel):
    id: str
    principal_type: str
    principal_id: str
    principal_label: str
    action: str
    max_value: float | None = None
    currency: str | None = None
    allowed_contract_types: list[str] = Field(default_factory=list)
    allowed_jurisdictions: list[str] = Field(default_factory=list)
    max_risk_band: str | None = None
    delegated_by_user_id: str | None = None
    delegated_by_label: str | None = None
    note: str | None = None
    valid_until: str | None = None
    revoked_at: str | None = None
    active: bool
    created_at: str | None = None
