from sqlalchemy import Boolean, Column, ForeignKey, Integer, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import PlaybookStatus


class Playbook(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(80), index=True, nullable=False, default=PlaybookStatus.DRAFT)
    current_version_id = Column(String(36), nullable=True)


class PlaybookVersion(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    playbook_id = Column(String(36), ForeignKey("playbook.id"), index=True, nullable=False)
    version_number = Column(Integer, nullable=False)
    status = Column(String(80), index=True, nullable=False, default=PlaybookStatus.DRAFT)
    summary = Column(Text, nullable=True)
    source_metadata = Column(JSON, nullable=True)


class PlaybookRule(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    playbook_version_id = Column(
        String(36), ForeignKey("playbook_version.id"), index=True, nullable=False
    )
    clause_type = Column(String(160), index=True, nullable=False)
    rule_type = Column(String(160), index=True, nullable=False)
    preferred_position = Column(Text, nullable=True)
    fallback_position = Column(Text, nullable=True)
    prohibited_language = Column(Text, nullable=True)
    required_language = Column(Text, nullable=True)
    risk_level = Column(String(80), index=True, nullable=True)
    rationale = Column(Text, nullable=True)
    approval_required = Column(Boolean, nullable=False, default=False)
    escalation_role = Column(String(120), nullable=True)
    sample_clause = Column(Text, nullable=True)
    negotiation_guidance = Column(Text, nullable=True)


class PlaybookRun(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    playbook_id = Column(String(36), ForeignKey("playbook.id"), index=True, nullable=False)
    playbook_version_id = Column(String(36), ForeignKey("playbook_version.id"), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=False)
    status = Column(String(80), index=True, nullable=False, default="queued")
    raw_ai_output = Column(JSON, nullable=True)
    validated_output = Column(JSON, nullable=True)
    validation_status = Column(String(80), nullable=True)
    model_name = Column(String(160), nullable=True)
    token_usage = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)


class PlaybookDeviation(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    playbook_run_id = Column(String(36), ForeignKey("playbook_run.id"), index=True, nullable=False)
    playbook_rule_id = Column(String(36), ForeignKey("playbook_rule.id"), index=True, nullable=True)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    severity = Column(String(80), index=True, nullable=False)
    clause_type = Column(String(160), index=True, nullable=True)
    issue = Column(Text, nullable=False)
    suggested_fix = Column(Text, nullable=True)
    citation = Column(JSON, nullable=True)
    status = Column(String(80), index=True, nullable=False, default="open")


class PlaybookDecision(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    playbook_deviation_id = Column(
        String(36), ForeignKey("playbook_deviation.id"), index=True, nullable=False
    )
    decision = Column(String(80), index=True, nullable=False)
    rationale = Column(Text, nullable=True)
    decided_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=False)
