from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import AssistantRunStatus, AssistantSessionType, AssistantToolCallStatus, AssistantToolCategory


class AssistantSession(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    session_type = Column(String(80), index=True, nullable=False, default=AssistantSessionType.GENERAL)
    title = Column(String(255), nullable=True)
    project_id = Column(String(36), ForeignKey("project.id"), nullable=True)
    contract_id = Column(String(36), ForeignKey("contract.id"), nullable=True)
    tabular_review_id = Column(String(36), nullable=True)
    status = Column(String(80), index=True, nullable=False, default="active")
    metadata_json = Column(JSON, nullable=False, default=dict)


class AssistantMessage(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    session_id = Column(String(36), ForeignKey("assistant_session.id"), index=True, nullable=False)
    role = Column(String(40), nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class AssistantRun(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    session_id = Column(String(36), ForeignKey("assistant_session.id"), index=True, nullable=False)
    status = Column(String(80), index=True, nullable=False, default=AssistantRunStatus.RUNNING)
    user_message_id = Column(String(36), ForeignKey("assistant_message.id"), nullable=True)
    assistant_message_id = Column(String(36), ForeignKey("assistant_message.id"), nullable=True)
    provider_state = Column(JSON, nullable=False, default=dict)
    context_manifest = Column(JSON, nullable=False, default=dict)
    model = Column(String(160), nullable=True)
    model_config_hash = Column(String(64), nullable=True)
    prompt_hash = Column(String(64), nullable=True)
    current_tool_iteration = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class AssistantToolCall(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    session_id = Column(String(36), ForeignKey("assistant_session.id"), index=True, nullable=False)
    assistant_run_id = Column(String(36), ForeignKey("assistant_run.id"), index=True, nullable=True)
    message_id = Column(String(36), ForeignKey("assistant_message.id"), nullable=True)
    provider_tool_use_id = Column(String(255), nullable=True)
    confirmation_id = Column(String(36), nullable=True)
    tool_name = Column(String(160), index=True, nullable=False)
    category = Column(String(80), nullable=False, default=AssistantToolCategory.READ_ONLY)
    arguments = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    status = Column(String(80), index=True, nullable=False, default=AssistantToolCallStatus.PENDING)
    confirmation_required = Column(Boolean, nullable=False, default=False)
    confirmed_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    resource_type = Column(String(120), index=True, nullable=True)
    resource_id = Column(String(36), index=True, nullable=True)
    resource_version = Column(String(80), nullable=True)
    idempotency_key = Column(String(255), index=True, nullable=True)
    output_schema_name = Column(String(180), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)


class AssistantContractHandle(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    session_id = Column(String(36), ForeignKey("assistant_session.id"), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    handle = Column(String(80), nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
