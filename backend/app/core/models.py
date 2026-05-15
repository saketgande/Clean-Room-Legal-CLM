from sqlalchemy import Boolean, Column, Float, Integer, JSON, String, Text, event

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)


class AuditLog(TableNameMixin, IdMixin, TimestampMixin, Base):
    org_id = Column(String(36), index=True, nullable=True)
    actor_user_id = Column(String(36), index=True, nullable=True)
    action = Column(String(120), index=True, nullable=False)
    resource_type = Column(String(120), index=True, nullable=False)
    resource_id = Column(String(36), index=True, nullable=True)
    request_id = Column(String(80), index=True, nullable=True)
    ip_address = Column(String(80), nullable=True)
    user_agent = Column(Text, nullable=True)
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)


@event.listens_for(AuditLog, "before_update", propagate=True)
def _prevent_audit_log_update(mapper, connection, target) -> None:  # noqa: ANN001
    raise RuntimeError("Audit logs are immutable")


@event.listens_for(AuditLog, "before_delete", propagate=True)
def _prevent_audit_log_delete(mapper, connection, target) -> None:  # noqa: ANN001
    raise RuntimeError("Audit logs are immutable")


class UsageRecord(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    user_id = Column(String(36), index=True, nullable=True)
    resource_type = Column(String(120), index=True, nullable=False)
    resource_id = Column(String(36), index=True, nullable=True)
    metric = Column(String(120), index=True, nullable=False)
    quantity = Column(Float, nullable=False, default=0)
    metadata_json = Column(JSON, nullable=True)


class AdminSetting(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    key = Column(String(180), index=True, nullable=False)
    value = Column(JSON, nullable=True)
    is_secret = Column(Boolean, nullable=False, default=False)


class ResourceTimelineEvent(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    resource_type = Column(String(120), index=True, nullable=False)
    resource_id = Column(String(36), index=True, nullable=False)
    event_type = Column(String(120), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    details = Column(JSON, nullable=True)
    request_id = Column(String(80), index=True, nullable=True)
    job_id = Column(String(36), index=True, nullable=True)
    skill_run_id = Column(String(36), index=True, nullable=True)
    assistant_run_id = Column(String(36), index=True, nullable=True)
    ai_call_id = Column(String(36), index=True, nullable=True)


class RequestLog(TableNameMixin, IdMixin, TimestampMixin, Base):
    request_id = Column(String(80), unique=True, index=True, nullable=False)
    user_id = Column(String(36), index=True, nullable=True)
    org_id = Column(String(36), index=True, nullable=True)
    method = Column(String(16), nullable=False)
    route = Column(String(500), index=True, nullable=False)
    status_code = Column(Integer, nullable=False)
    latency_ms = Column(Float, nullable=False)
    error_class = Column(String(160), nullable=True)
    request_metadata = Column(JSON, nullable=True)


class AICallLog(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    request_id = Column(String(80), index=True, nullable=True)
    skill_run_id = Column(String(36), index=True, nullable=True)
    job_id = Column(String(36), index=True, nullable=True)
    session_id = Column(String(36), index=True, nullable=True)
    assistant_run_id = Column(String(36), index=True, nullable=True)
    tool_call_id = Column(String(36), index=True, nullable=True)
    resource_type = Column(String(120), index=True, nullable=True)
    resource_id = Column(String(36), index=True, nullable=True)
    provider = Column(String(80), nullable=False, default="claude")
    model = Column(String(160), nullable=False)
    model_config_hash = Column(String(64), nullable=True)
    prompt_key = Column(String(180), index=True, nullable=True)
    prompt_version = Column(String(80), nullable=True)
    prompt_hash = Column(String(64), nullable=True)
    input_payload = Column(JSON, nullable=True)
    output_schema_name = Column(String(180), nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    status = Column(String(80), index=True, nullable=True)
    validation_status = Column(String(80), index=True, nullable=True)
    validation_error = Column(Text, nullable=True)
    error_class = Column(String(160), nullable=True)
    provider_request_id = Column(String(255), nullable=True)
    stop_reason = Column(String(120), nullable=True)
    redaction_status = Column(String(80), nullable=True)
    raw_ai_output = Column(JSON, nullable=True)
    validated_output = Column(JSON, nullable=True)
