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
from app.core.enums import ProjectType


class Project(
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
    project_type = Column(String(80), nullable=False, default=ProjectType.GENERAL)
    owner_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ProjectFolder(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    project_id = Column(String(36), ForeignKey("project.id"), index=True, nullable=False)
    parent_folder_id = Column(String(36), ForeignKey("project_folder.id"), nullable=True)
    name = Column(String(255), nullable=False)


class ProjectMember(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    project_id = Column(String(36), ForeignKey("project.id"), index=True, nullable=False)
    user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    role = Column(String(120), nullable=False, default="member")

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)


class ProjectContract(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    project_id = Column(String(36), ForeignKey("project.id"), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    folder_id = Column(String(36), ForeignKey("project_folder.id"), nullable=True)

    __table_args__ = (UniqueConstraint("project_id", "contract_id", name="uq_project_contract"),)


class ProjectActivity(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    project_id = Column(String(36), ForeignKey("project.id"), index=True, nullable=False)
    actor_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    activity_type = Column(String(120), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    details = Column(JSON, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
