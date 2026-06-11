from sqlalchemy import Column, Date, ForeignKey, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import ObligationStatus


class Obligation(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=True)
    owner_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=True)
    responsible_party = Column(String(255), nullable=True)
    obligation_type = Column(String(160), index=True, nullable=True)
    description = Column(Text, nullable=False)
    due_date = Column(Date, index=True, nullable=True)
    recurrence = Column(String(120), nullable=True)
    status = Column(String(80), index=True, nullable=False, default=ObligationStatus.OPEN)
    source_citation = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ObligationReminder(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    obligation_id = Column(String(36), ForeignKey("obligation.id"), index=True, nullable=False)
    remind_at = Column(Date, index=True, nullable=False)
    sent_at = Column(Date, nullable=True)
    channel = Column(String(80), nullable=False, default="email")
