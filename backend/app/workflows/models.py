from sqlalchemy import Column, ForeignKey, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import Visibility, WorkflowType


class Workflow(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    name = Column(String(255), nullable=False)
    workflow_type = Column(String(80), index=True, nullable=False, default=WorkflowType.ASSISTANT)
    visibility = Column(String(80), index=True, nullable=False, default=Visibility.PRIVATE)
    description = Column(Text, nullable=True)
    definition = Column(JSON, nullable=False, default=dict)


class WorkflowRun(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    workflow_id = Column(String(36), ForeignKey("workflow.id"), index=True, nullable=False)
    project_id = Column(String(36), ForeignKey("project.id"), nullable=True)
    status = Column(String(80), index=True, nullable=False, default="queued")
    input_contract_ids = Column(JSON, nullable=False, default=list)
    input_prompt = Column(Text, nullable=True)
    output = Column(JSON, nullable=True)
    tool_calls = Column(JSON, nullable=True)
    created_artifacts = Column(JSON, nullable=True)
    model_used = Column(String(160), nullable=True)
    error_message = Column(Text, nullable=True)
