from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text

from app.core.database import ActorTrackedMixin, Base, IdMixin, OrgScopedMixin, TableNameMixin, TimestampMixin
from app.core.enums import SignatureStatus


class SignatureRequest(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=False)
    provider = Column(String(80), nullable=False, default="docusign")
    provider_envelope_id = Column(String(255), index=True, nullable=True)
    status = Column(String(80), index=True, nullable=False, default=SignatureStatus.DRAFT)
    sent_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    signed_contract_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class SignatureRecipient(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    signature_request_id = Column(
        String(36), ForeignKey("signature_request.id"), index=True, nullable=False
    )
    name = Column(String(255), nullable=False)
    email = Column(String(320), index=True, nullable=False)
    role = Column(String(120), nullable=True)
    routing_order = Column(String(40), nullable=True)
    status = Column(String(80), index=True, nullable=True)


class SignatureEvent(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    signature_request_id = Column(
        String(36), ForeignKey("signature_request.id"), index=True, nullable=False
    )
    event_type = Column(String(120), index=True, nullable=False)
    payload = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
