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
