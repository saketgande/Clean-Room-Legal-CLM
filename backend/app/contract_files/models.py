from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import validates

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
from app.core.enums import ContractVersionSource, ShareAccessMode, StorageBackend


class StorageObject(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    storage_key = Column(String(1000), unique=True, index=True, nullable=False)
    filename = Column(String(500), nullable=False)
    mime_type = Column(String(255), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256_hash = Column(String(64), index=True, nullable=False)
    storage_backend = Column(String(80), nullable=False, default=StorageBackend.LOCAL_VOLUME)


class ContractFile(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    current_version_id = Column(
        String(36),
        ForeignKey("contract_version.id", name="fk_contract_file_current_version_id", use_alter=True),
        nullable=True,
    )
    file_label = Column(String(255), nullable=False)


class ContractVersion(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    __table_args__ = (
        UniqueConstraint("contract_file_id", "version_number", name="uq_contract_version_file_number"),
        # One authoritative version per contract — the rule several call sites used to
        # keep on their own (see contract_files.service.promote_version).
        Index(
            "uq_contract_version_authoritative",
            "contract_id",
            unique=True,
            postgresql_where=text("is_authoritative AND deleted_at IS NULL"),
        ),
    )

    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_file_id = Column(String(36), ForeignKey("contract_file.id"), index=True, nullable=False)
    version_number = Column(Integer, nullable=False)
    storage_object_id = Column(String(36), ForeignKey("storage_object.id"), nullable=False)
    text_snapshot_id = Column(
        String(36),
        ForeignKey("contract_text_snapshot.id", name="fk_contract_version_text_snapshot_id", use_alter=True),
        nullable=True,
    )
    source = Column(String(80), index=True, nullable=False, default=ContractVersionSource.UPLOAD)
    change_summary = Column(Text, nullable=True)
    is_authoritative = Column(Boolean, nullable=False, default=False)


class ContractTextSnapshot(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=False)
    extraction_method = Column(String(120), nullable=False)
    extraction_quality_score = Column(Float, nullable=False, default=0)
    text = Column(Text, nullable=False, default="")
    page_map = Column(JSON, nullable=True)
    ocr_provider = Column(String(120), nullable=True)
    validation_status = Column(String(80), nullable=True)
    # Whether this snapshot has been broken into structured elements
    # (ContractDocumentElement rows). "flat_only" = legacy text-only snapshot;
    # "structured" = elements exist and `text` is their derived concatenation.
    structure_status = Column(String(20), nullable=False, default="flat_only")
    element_count = Column(Integer, nullable=False, default=0)

    @validates("text")
    def _strip_nul(self, _key, value):
        # Postgres TEXT rejects NUL bytes, and pypdf/OCR/docx output can carry
        # them; stripping on assignment covers every snapshot writer, and runs
        # before offsets are derived from the stored text.
        return value.replace("\x00", "") if value else value


class ContractDocumentElement(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    """One structural element of a contract — a heading, clause, paragraph, list
    item, or table — extracted from a snapshot. This is the structured document
    model: the OCR/parse step already produces these (type, confidence, page),
    and this table stops us from flattening them into one lossy string. Every
    reader (redline, brain, risk, export) can move to fetching clauses instead
    of the whole blob. Immutable per snapshot; re-extraction writes new rows.
    """

    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=False)
    text_snapshot_id = Column(String(36), ForeignKey("contract_text_snapshot.id"), index=True, nullable=False)

    seq = Column(Integer, nullable=False)  # document order, 0-based
    parent_id = Column(String(36), ForeignKey("contract_document_element.id"), nullable=True)  # hierarchy
    element_type = Column(String(40), nullable=False, default="paragraph")
    level = Column(Integer, nullable=False, default=0)  # depth in the clause tree
    number_label = Column(String(40), nullable=True)  # "2.1", "Article 4", "(a)"

    block_id = Column(String(40), index=True, nullable=False)  # content-hash anchor (matches the splitter)
    text = Column(Text, nullable=False, default="")
    html = Column(Text, nullable=True)  # table markup, kept as a table

    page_number = Column(Integer, nullable=True)
    char_start = Column(Integer, nullable=True)  # offset into snapshot.text (back-compat anchoring)
    char_end = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=True)  # per-element OCR confidence
    bbox = Column(JSON, nullable=True)  # {page, x0, y0, x1, y1} when available
    source = Column(String(30), nullable=False, default="ocr")  # ocr | docx | pdf | from_flat_text

    __table_args__ = (
        UniqueConstraint("text_snapshot_id", "seq", name="uq_element_snapshot_seq"),
        Index("ix_element_version_seq", "contract_version_id", "seq"),
    )


class ContractEdit(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=False)
    edit_type = Column(String(120), nullable=False)
    status = Column(String(80), nullable=False, default="proposed")
    original_text = Column(Text, nullable=True)
    replacement_text = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    citation = Column(JSON, nullable=True)


class RevisionRound(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """One negotiation round: the version we sent (`base_version_id`) against
    the version the counterparty sent back (`revision_version_id`). Its changes
    are worked out once, when their file arrives; the decisions are ours."""

    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    base_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=False)
    revision_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=False)
    round_number = Column(Integer, nullable=False, default=1)
    status = Column(String(20), index=True, nullable=False, default="open")  # open | closed
    # agreed: every change accepted; counter: we sent back our own version.
    outcome = Column(String(20), nullable=True)
    outcome_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=True)
    # Their file marked its changes (Word tracked changes), so an unmarked one
    # can be told apart. False for a clean copy or a PDF.
    tracked = Column(Boolean, nullable=False, default=False)


class RevisionChange(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    round_id = Column(String(36), ForeignKey("revision_round.id"), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    seq = Column(Integer, nullable=False)  # order in their version
    label = Column(String(120), nullable=True)  # "10.2 Limitation of liability"
    # changed | added | removed | reverted (put back their original) |
    # countered (changed our change) | ours (kept our change; nothing to decide)
    kind = Column(String(20), nullable=False)
    unmarked = Column(Boolean, nullable=False, default=False)  # not a tracked change in their file
    our_text = Column(Text, nullable=True)
    their_text = Column(Text, nullable=True)
    parts = Column(JSON, nullable=False, default=list)  # [["=", w], ["-", w], ["+", w]]
    # open | accepted (theirs) | kept (ours) | countered | agreed (kind=ours)
    decision = Column(String(20), nullable=False, default="open")
    counter_text = Column(Text, nullable=True)
    decided_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)


class ContractShare(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=True)
    token_hash = Column(String(255), nullable=False)
    passcode_hash = Column(String(255), nullable=True)
    access_mode = Column(String(80), nullable=False, default=ShareAccessMode.VIEW_ONLY)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    download_allowed = Column(Boolean, nullable=False, default=False)


class ContractEmbedding(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=False)
    text_snapshot_id = Column(String(36), ForeignKey("contract_text_snapshot.id"), index=True, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
