from sqlalchemy import Boolean, Column, ForeignKey, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import AssistantSessionType, AssistantToolCategory


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


class AssistantToolCall(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    session_id = Column(String(36), ForeignKey("assistant_session.id"), index=True, nullable=False)
    message_id = Column(String(36), ForeignKey("assistant_message.id"), nullable=True)
    tool_name = Column(String(160), index=True, nullable=False)
    category = Column(String(80), nullable=False, default=AssistantToolCategory.READ_ONLY)
    arguments = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    status = Column(String(80), index=True, nullable=False, default="pending")
    confirmation_required = Column(Boolean, nullable=False, default=False)
    confirmed_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    error_message = Column(Text, nullable=True)


class AssistantContractHandle(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    session_id = Column(String(36), ForeignKey("assistant_session.id"), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    handle = Column(String(80), nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
