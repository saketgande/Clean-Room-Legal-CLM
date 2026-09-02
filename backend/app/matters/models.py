from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import MatterStatus, MatterType


class Matter(
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
    matter_type = Column(String(80), nullable=False, default=MatterType.GENERAL)
    # owner_user_id is the matter's responsible lead.
    owner_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    # Matter identity + lifecycle (Projects -> Matters evolution).
    matter_number = Column(String(40), index=True, nullable=True)
    client_name = Column(String(255), index=True, nullable=True)
    status = Column(String(40), nullable=False, default=MatterStatus.ACTIVE, index=True)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("org_id", "matter_number", name="uq_matter_number_per_org"),
    )


class MatterFolder(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    matter_id = Column(String(36), ForeignKey("matter.id"), index=True, nullable=False)
    parent_folder_id = Column(String(36), ForeignKey("matter_folder.id"), nullable=True)
    name = Column(String(255), nullable=False)


class MatterMember(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    matter_id = Column(String(36), ForeignKey("matter.id"), index=True, nullable=False)
    user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    role = Column(String(120), nullable=False, default="member")

    __table_args__ = (UniqueConstraint("matter_id", "user_id", name="uq_project_member"),)


class MatterShare(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    matter_id = Column(String(36), ForeignKey("matter.id"), index=True, nullable=False)
    shared_with_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    access_level = Column(String(40), nullable=False, default="read")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class MatterContract(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    matter_id = Column(String(36), ForeignKey("matter.id"), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    folder_id = Column(String(36), ForeignKey("matter_folder.id"), nullable=True)

    __table_args__ = (UniqueConstraint("matter_id", "contract_id", name="uq_project_contract"),)


class MatterActivity(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    matter_id = Column(String(36), ForeignKey("matter.id"), index=True, nullable=False)
    actor_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    activity_type = Column(String(120), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    details = Column(JSON, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
