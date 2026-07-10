from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text

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
    # Derived sub-states of ACTIVE / CLOSED, folded out of the stage enum:
    #   renewal_due — ACTIVE contract whose renewal/notice window has opened
    #                 (set by the renewal-window job; cleared on a renewal decision)
    #   archived    — CLOSED contract kept for retention but hidden from default views
    renewal_due = Column(Boolean, index=True, nullable=False, default=False)
    archived = Column(Boolean, index=True, nullable=False, default=False)
    owner_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    counterparty_name = Column(String(255), index=True, nullable=True)
    jurisdiction = Column(String(160), index=True, nullable=True)
    # Phase 3 (MAC): confidentiality classification. Ordered ladder
    #   public < internal < confidential < restricted
    # A user may only read a contract whose classification is <= their clearance
    # (see app/contracts/access.py). Defaults to 'internal' so nothing is locked
    # down harder than before until it's deliberately reclassified.
    confidentiality = Column(String(40), index=True, nullable=False, default="internal")
    risk_level = Column(String(80), index=True, nullable=True)
    # Weighted, explainable risk. risk_summary holds the drivers behind the score.
    risk_score = Column(Integer, nullable=True)
    risk_band = Column(String(40), nullable=True)
    risk_summary = Column(JSON, nullable=True)
    value_amount = Column(Float, nullable=True)
    currency = Column(String(3), nullable=True)
    effective_date = Column(Date, nullable=True)
    expiration_date = Column(Date, nullable=True)
    current_contract_file_id = Column(
        String(36),
        ForeignKey("contract_file.id", name="fk_contract_current_contract_file_id", use_alter=True),
        nullable=True,
    )
    current_authoritative_version_id = Column(
        String(36),
        ForeignKey(
            "contract_version.id",
            name="fk_contract_current_authoritative_version_id",
            use_alter=True,
        ),
        nullable=True,
    )
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
