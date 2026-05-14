from sqlalchemy import Column, ForeignKey, Integer, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import TabularCellStatus


class TabularReview(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    name = Column(String(255), nullable=False)
    project_id = Column(String(36), ForeignKey("project.id"), nullable=True)
    source_contract_ids = Column(JSON, nullable=False, default=list)
    status = Column(String(80), index=True, nullable=False, default="draft")
    metadata_json = Column(JSON, nullable=False, default=dict)


class TabularReviewColumn(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    tabular_review_id = Column(
        String(36), ForeignKey("tabular_review.id"), index=True, nullable=False
    )
    name = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    metadata_json = Column(JSON, nullable=False, default=dict)


class TabularReviewCell(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    tabular_review_id = Column(
        String(36), ForeignKey("tabular_review.id"), index=True, nullable=False
    )
    column_id = Column(String(36), ForeignKey("tabular_review_column.id"), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    status = Column(String(80), index=True, nullable=False, default=TabularCellStatus.PENDING)
    answer = Column(Text, nullable=True)
    reasoning = Column(Text, nullable=True)
    citations = Column(JSON, nullable=True)
    confidence = Column(String(40), nullable=True)
    raw_ai_output = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)


class TabularReviewChat(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    tabular_review_id = Column(
        String(36), ForeignKey("tabular_review.id"), index=True, nullable=False
    )
    role = Column(String(40), nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(JSON, nullable=True)
