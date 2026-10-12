from sqlalchemy import JSON, Column, Date, DateTime, ForeignKey, Integer, String, Text

try:
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover - used only if pgvector is missing in a dev shell
    Vector = lambda dimensions: JSON

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import (
    DocumentExtractTemplate,
    TrademarkSource,
    TrademarkStatus,
    TrademarkType,
    TrademarkWorkflowState,
)

EMBEDDING_DIMENSIONS = 384


class Trademark(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    name = Column(String(255), index=True, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(80), index=True, nullable=False, default=TrademarkStatus.DRAFT)
    trademark_type = Column(String(40), nullable=False, default=TrademarkType.WORD_MARK)
    jurisdiction = Column(String(120), nullable=False)
    jurisdictions = Column(JSON, nullable=True)
    nice_class = Column(String(255), nullable=True)
    goods_services = Column(Text, nullable=True)
    filing_context = Column(JSON, nullable=True)
    filed_on = Column(DateTime(timezone=True), nullable=True)
    renewal_due_on = Column(DateTime(timezone=True), index=True, nullable=True)
    workflow_state = Column(String(80), nullable=False, default=TrademarkWorkflowState.INTAKE)
    source = Column(String(40), nullable=False, default=TrademarkSource.INTAKE)
    source_document_extract_id = Column(String(36), ForeignKey("document_extract.id"), nullable=True)
    # The Legal Intake request this trademark was continued from, if any —
    # same deliberate choice as Notice.escalated_intake_request_id. Nullable:
    # a trademark can also be created directly (the standalone wizard path,
    # or bulk document ingestion) with no intake ticket behind it at all.
    source_intake_request_id = Column(
        String(36), ForeignKey("intake_request.id", ondelete="SET NULL"), nullable=True
    )
    embedding_text = Column(Text, nullable=True)
    embedding = Column(Vector(EMBEDDING_DIMENSIONS), nullable=True)


class PortfolioDigest(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """One row per org per calendar day — the one LLM-generated summary of
    what changed in that org's trademark portfolio that day. Generating
    "today's" digest is a get-or-create on (org_id, digest_date), so it
    never costs more than one LLM call per org per day regardless of how
    many times it's requested (e.g. a cron plus a user clicking Refresh).
    See app/trademarks/service.py.
    """

    digest_date = Column(Date, index=True, nullable=False)
    summary = Column(Text, nullable=False)
    stats = Column(JSON, nullable=False)


class DocumentExtract(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    source_filename = Column(String(500), nullable=False)
    storage_key = Column(String(500), nullable=True)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    template = Column(String(40), nullable=False, default=DocumentExtractTemplate.GENERIC)
    field_schema = Column(JSON, nullable=True)
    extracted_fields = Column(JSON, nullable=False, default=dict)
    embedding_text = Column(Text, nullable=True)
    embedding = Column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
    ingested_trademark_id = Column(String(36), ForeignKey("trademark.id"), nullable=True)
