from datetime import datetime

from pydantic import BaseModel, Field

# ---------- Org units ----------


class OrgUnitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None  # null => attempt to create the root


class OrgUnitUpdate(BaseModel):
    """Both fields optional; an *omitted* field is left unchanged. Because
    ``parent_id: null`` (explicit "make root") must be distinguishable from
    omitting ``parent_id`` entirely, the service layer must check
    ``"parent_id" in payload.model_fields_set`` (equivalently,
    ``payload.model_dump(exclude_unset=True)``) rather than testing
    ``payload.parent_id is None`` directly.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = None


class OrgUnitResponse(BaseModel):
    id: str
    org_id: str
    name: str
    parent_id: str | None
    is_root: bool
    depth: int
    path_names: list[str]
    child_count: int
    active_grant_count: int
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OrgUnitReparentEntry(BaseModel):
    org_unit_id: str
    name: str
    from_parent_id: str | None
    to_parent_id: str | None


class OrgUnitDeleteResponse(BaseModel):
    deleted_org_unit_id: str
    reparented: list[OrgUnitReparentEntry] = Field(default_factory=list)


# ---------- Role grants ----------


class RoleGrantCreate(BaseModel):
    user_id: str
    role_id: str
    org_unit_id: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class RoleGrantResponse(BaseModel):
    id: str
    org_id: str
    user_id: str
    user_label: str
    role_id: str
    role_name: str
    allows_hierarchy_rollup: bool
    org_unit_id: str
    org_unit_name: str
    valid_from: datetime | None
    valid_to: datetime | None
    is_active: bool
    revoked_at: datetime | None
    revoked_by_user_id: str | None
    created_at: datetime
    created_by_user_id: str | None


# ---------- Delegations ----------


class DelegationCreate(BaseModel):
    delegator_user_id: str | None = None  # null = the calling user
    delegate_user_id: str
    role_id: str | None = None
    org_unit_id: str | None = None
    start_date: datetime
    end_date: datetime


class DelegationResponse(BaseModel):
    id: str
    org_id: str
    delegator_user_id: str
    delegator_label: str
    delegate_user_id: str
    delegate_label: str
    role_id: str | None
    role_name: str | None
    org_unit_id: str | None
    org_unit_name: str | None
    start_date: datetime
    end_date: datetime
    status: str
    is_active: bool
    can_revoke: bool
    revoked_at: datetime | None
    revoked_by_user_id: str | None
    created_at: datetime
    created_by_user_id: str | None


class DelegationEligibilityEntry(BaseModel):
    role_id: str
    role_name: str
    allows_hierarchy_rollup: bool
    org_unit_id: str
    org_unit_name: str
    valid_to: datetime | None
