from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ProjectType


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    project_type: ProjectType = ProjectType.GENERAL
    metadata_json: dict = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    project_type: ProjectType | None = None
    metadata_json: dict | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    name: str
    description: str | None
    project_type: str
    owner_user_id: str
    metadata_json: dict


class ProjectFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_folder_id: str | None = None


class ProjectFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_folder_id: str | None = None


class ProjectFolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    parent_folder_id: str | None
    name: str


class ProjectMemberUpsert(BaseModel):
    user_id: str
    role: str = Field(default="member", min_length=1, max_length=120)


class ProjectMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    user_id: str
    role: str


class ProjectContractAdd(BaseModel):
    contract_id: str
    folder_id: str | None = None


class ProjectContractUpdate(BaseModel):
    folder_id: str | None = None


class ProjectContractResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    contract_id: str
    folder_id: str | None
