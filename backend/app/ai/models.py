from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import (
    AIConfirmationStatus,
    AIPromptStatus,
    AISkillRunStatus,
    AIValidationStatus,
)


class AISkillRun(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    skill_name = Column(String(180), index=True, nullable=False)
    skill_version = Column(String(80), nullable=False)
    execution_mode = Column(String(80), index=True, nullable=False)
    status = Column(String(80), index=True, nullable=False, default=AISkillRunStatus.QUEUED)
    resource_type = Column(String(120), index=True, nullable=True)
    resource_id = Column(String(36), index=True, nullable=True)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=True)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=True)
    text_snapshot_id = Column(String(36), ForeignKey("contract_text_snapshot.id"), index=True, nullable=True)
    job_id = Column(String(36), ForeignKey("job_run.id"), index=True, nullable=True)
    session_id = Column(String(36), ForeignKey("assistant_session.id"), index=True, nullable=True)
    assistant_run_id = Column(String(36), ForeignKey("assistant_run.id"), index=True, nullable=True)
    tool_call_id = Column(String(36), ForeignKey("assistant_tool_call.id"), index=True, nullable=True)
    prompt_key = Column(String(180), index=True, nullable=False)
    prompt_version = Column(String(80), nullable=False)
    prompt_hash = Column(String(64), nullable=False)
    model = Column(String(160), nullable=False)
    model_config_hash = Column(String(64), nullable=True)
    input_payload = Column(JSON, nullable=False, default=dict)
    output_payload = Column(JSON, nullable=True)
    validation_status = Column(String(80), index=True, nullable=False, default=AIValidationStatus.NOT_VALIDATED)
    validation_error = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class AIPromptVersion(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    prompt_key = Column(String(180), index=True, nullable=False)
    version = Column(String(80), nullable=False)
    status = Column(String(80), index=True, nullable=False, default=AIPromptStatus.DRAFT)
    prompt_text = Column(Text, nullable=False)
    prompt_hash = Column(String(64), index=True, nullable=False)
    description = Column(Text, nullable=True)
    model_name = Column(String(160), nullable=True)
    model_config_hash = Column(String(64), nullable=True)
    live_eval_required = Column(Boolean, nullable=False, default=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class AIConfirmation(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    session_id = Column(String(36), ForeignKey("assistant_session.id"), index=True, nullable=True)
    assistant_run_id = Column(String(36), ForeignKey("assistant_run.id"), index=True, nullable=True)
    tool_call_id = Column(String(36), ForeignKey("assistant_tool_call.id"), index=True, nullable=True)
    tool_name = Column(String(180), index=True, nullable=False)
    status = Column(String(80), index=True, nullable=False, default=AIConfirmationStatus.PENDING)
    requested_payload = Column(JSON, nullable=False, default=dict)
    tool_input = Column(JSON, nullable=False, default=dict)
    policy = Column(JSON, nullable=False, default=dict)
    resource_type = Column(String(120), index=True, nullable=True)
    resource_id = Column(String(36), index=True, nullable=True)
    resource_version = Column(String(80), nullable=True)
    idempotency_key = Column(String(255), index=True, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    provider_state = Column(JSON, nullable=False, default=dict)


class AICitation(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    skill_run_id = Column(String(36), ForeignKey("a_i_skill_run.id"), index=True, nullable=True)
    ai_call_id = Column(String(36), ForeignKey("a_i_call_log.id"), index=True, nullable=True)
    assistant_run_id = Column(String(36), ForeignKey("assistant_run.id"), index=True, nullable=True)
    tool_call_id = Column(String(36), ForeignKey("assistant_tool_call.id"), index=True, nullable=True)
    resource_type = Column(String(120), index=True, nullable=True)
    resource_id = Column(String(36), index=True, nullable=True)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=True)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=True)
    text_snapshot_id = Column(String(36), ForeignKey("contract_text_snapshot.id"), index=True, nullable=True)
    label = Column(String(255), nullable=True)
    quote = Column(Text, nullable=False)
    normalized_quote = Column(Text, nullable=True)
    page_number = Column(Integer, nullable=True)
    start_char = Column(Integer, nullable=True)
    end_char = Column(Integer, nullable=True)
    validation_status = Column(String(80), index=True, nullable=False)
    similarity_score = Column(Float, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
