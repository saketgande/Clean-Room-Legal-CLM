from sqlalchemy import Column, Date, ForeignKey, JSON, String, Text

from app.core.database import ActorTrackedMixin, Base, IdMixin, OrgScopedMixin, TableNameMixin, TimestampMixin
from app.core.enums import RenewalDecision


class RenewalEvent(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=True)
    expiration_date = Column(Date, index=True, nullable=True)
    notice_date = Column(Date, index=True, nullable=True)
    renewal_window_starts_at = Column(Date, index=True, nullable=True)
    owner_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    decision = Column(String(80), index=True, nullable=False, default=RenewalDecision.UNDECIDED)
    decision_note = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
