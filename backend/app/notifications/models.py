from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text

from app.core.database import ActorTrackedMixin, Base, IdMixin, OrgScopedMixin, TableNameMixin, TimestampMixin


class Notification(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=True)
    channel = Column(String(80), index=True, nullable=False, default="email")
    event_type = Column(String(160), index=True, nullable=False)
    subject = Column(String(500), nullable=True)
    body = Column(Text, nullable=True)
    status = Column(String(80), index=True, nullable=False, default="queued")
    provider_message_id = Column(String(255), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
