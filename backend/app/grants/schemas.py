from datetime import datetime

from pydantic import BaseModel


class GrantCreate(BaseModel):
    principal_type: str  # 'user' | 'role' | 'group'
    principal_id: str
    resource_type: str  # 'contract' | 'project' | 'playbook'
    resource_id: str
    access_level: str = "read"
    valid_until: datetime | None = None
    note: str | None = None


class GrantResponse(BaseModel):
    id: str
    principal_type: str
    principal_id: str
    principal_label: str
    resource_type: str
    resource_id: str
    access_level: str
    note: str | None = None
    valid_until: str | None = None
    revoked_at: str | None = None
    active: bool
    created_at: str | None = None
