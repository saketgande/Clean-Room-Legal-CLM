from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import ContractLifecycleStage


class Contract(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    title = Column(String(500), nullable=False)
    contract_type = Column(String(160), index=True, nullable=True)
    lifecycle_stage = Column(
        String(80), index=True, nullable=False, default=ContractLifecycleStage.INTAKE
    )
    owner_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    counterparty_name = Column(String(255), index=True, nullable=True)
    jurisdiction = Column(String(160), index=True, nullable=True)
    risk_level = Column(String(80), index=True, nullable=True)
    value_amount = Column(Float, nullable=True)
    currency = Column(String(3), nullable=True)
    effective_date = Column(Date, nullable=True)
    expiration_date = Column(Date, nullable=True)
    current_contract_file_id = Column(String(36), nullable=True)
    current_authoritative_version_id = Column(String(36), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ContractParty(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    name = Column(String(255), index=True, nullable=False)
    party_type = Column(String(120), nullable=True)
    contact_email = Column(String(320), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ContractStageHistory(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    from_stage = Column(String(80), nullable=True)
    to_stage = Column(String(80), index=True, nullable=False)
    reason = Column(Text, nullable=True)
    changed_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    changed_at = Column(DateTime(timezone=True), nullable=False)
    override_used = Column(Boolean, nullable=False, default=False)


class ContractActivity(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    actor_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    activity_type = Column(String(120), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    details = Column(JSON, nullable=True)
