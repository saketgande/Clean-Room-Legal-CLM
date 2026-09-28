"""Docstudio tables.

A rebuild of how a legal document is represented, deliberately independent of
`contract_files`. The existing model anchors every clause, citation and comment
to a character offset into one mutable string, so every new version silently
invalidates all of them. That is a data-model problem, not a code problem, which
is why these are new tables rather than new columns.

Two rules shape everything here:

* the source file is immutable and authoritative — the flat text is derived and
  safe to rebuild at any time;
* an annotation carries three independent anchors (clause, quote-with-context,
  offsets) so it can still be found after the text moves under it. See
  `anchoring.py` and the W3C Web Annotation Data Model.
"""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)


class DsDocument(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """One logical document, across all its versions."""

    # Whatever the rest of AEGIS calls this document. Deliberately a bare string
    # and not a foreign key: docstudio reads files through `FileSource` and
    # knows nothing else about the caller's domain.
    external_ref = Column(String(128), nullable=True, index=True)
    title = Column(String(500), nullable=True)
    current_version_id = Column(String(36), nullable=True)


class DsVersion(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """An immutable snapshot: these exact bytes, parsed this exact way."""

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_ds_version_document_number"),
        Index("ix_ds_version_document", "document_id", "version_number"),
    )

    document_id = Column(String(36), ForeignKey("ds_document.id"), nullable=False)
    version_number = Column(Integer, nullable=False)

    sha256 = Column(String(64), nullable=False, index=True)
    mime_type = Column(String(255), nullable=False)
    filename = Column(String(500), nullable=True)
    byte_size = Column(Integer, nullable=False)

    # Which parser produced the structure below. Re-parsing with a different
    # version produces different offsets, so a version that does not record how
    # it was parsed cannot be reproduced or audited.
    # Where the file itself is kept, so the document view can draw the real
    # page. None for versions ingested before the bytes were stored.
    storage_key = Column(String(512), nullable=True)

    parser_name = Column(String(64), nullable=False)
    parser_version = Column(String(32), nullable=False)

    # Derived from the clauses, for search and AI. Never authoritative: deleting
    # and rebuilding this column must never break an annotation.
    flat_text = Column(Text, nullable=False, default="")
    page_count = Column(Integer, nullable=True)
    parse_warnings = Column(JSON, nullable=True)
    # Everything extraction changed about the page, with the reason: what was
    # removed as not-content (page headers, footers, stamps, OCR debris) and
    # which blocks were rejoined across a page break. Nothing is dropped
    # silently — a reviewer can see what vanished, and a viewer can still
    # draw a stamp it chose not to treat as a clause.
    # {"removed": [{"reason", "text", "page", "bbox"}], "joined": [{"reason", "pages", "text"}]}
    artifacts = Column(JSON, nullable=True)


class DsClause(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    """One clause of one version."""

    __table_args__ = (
        Index("ix_ds_clause_version_seq", "version_id", "seq"),
        Index("ix_ds_clause_identity", "clause_id"),
    )

    version_id = Column(String(36), ForeignKey("ds_version.id"), nullable=False)

    # STABLE identity, carried forward across versions for a clause that did not
    # meaningfully change — which is what lets its comments survive a redline.
    # Not a content hash: a hash changes the moment the text does, which is
    # exactly when identity most needs to hold.
    clause_id = Column(String(64), nullable=False)

    seq = Column(Integer, nullable=False)
    parent_clause_id = Column(String(64), nullable=True)
    # Who decided the parent: "numbering" (the number itself), "list" (a list
    # under the clause introducing it), "part" (top of an exhibit), "top" (a
    # top-level clause), "document" (Word's own numbering level), "ai", or
    # "undecided". Recorded so the only list a reviewer must check is honest:
    # exactly the placements the AI made, and the ones nobody could.
    structure_source = Column(String(24), nullable=True)

    number_label = Column(String(64), nullable=True)  # "2.1(a)", "Article IV"
    # The number read structurally: its kind and its position. "5.5" is
    # decimal [5, 5]; "(c)" is a bracketed letter at position [3]. The parent
    # of a decimal is the clause one number up — a fact, not a judgment.
    number_scheme = Column(String(24), nullable=True)
    number_path = Column(JSON, nullable=True)
    # Depth in the finished tree, 1 for top level — not the label's shape.
    level = Column(Integer, nullable=False, default=1)
    clause_type = Column(String(32), nullable=False, default="clause")

    text = Column(Text, nullable=False)
    char_start = Column(Integer, nullable=False)
    char_end = Column(Integer, nullable=False)

    # Where the clause sits on the page, from the native PDF reader or from the
    # OCR provider's own blocks. Without it a citation can never be highlighted
    # on the page it came from — and most contracts here are scans.
    page_number = Column(Integer, nullable=True)
    # {"x0","y0","x1","y1"} as a FRACTION of the page, not points: both paths
    # report the same units, and a fraction still holds at any zoom.
    bbox = Column(JSON, nullable=True)
    # Every place the clause sits, in reading order: [{"page", "bbox"}]. A
    # clause rejoined across a page break has two, and a viewer must light up
    # both. `page_number` and `bbox` stay as the first region, for readers
    # that only need where the clause starts.
    source_regions = Column(JSON, nullable=True)


class DsOcrResult(TableNameMixin, IdMixin, TimestampMixin, Base):
    """What an OCR provider returned for exactly these bytes.

    OCR is the expensive step and the non-deterministic one: the same scan read
    twice came back as 39,209 and 39,916 characters. Re-parsing with a newer
    parser must not pay again, and must not get different text — different
    text means different clauses, and every annotation on the old ones would
    orphan. Keyed by content hash, so it serves any document holding the file.
    """

    __table_args__ = (Index("ix_ds_ocr_result_sha", "sha256", "provider"),)

    sha256 = Column(String(64), nullable=False)
    provider = Column(String(32), nullable=False)
    text = Column(Text, nullable=False, default="")
    # The provider's blocks with their own labels ("Header", "Figure",
    # "Section Header"...), page and box — the labels are the strongest
    # evidence of what a block is, and were being thrown away.
    blocks = Column(JSON, nullable=True)
    quality = Column(JSON, nullable=True)


class DsAnnotation(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """Anything attached to a clause: comment, citation, proposal, risk.

    Annotations live outside the document, never inside it — a comment must not
    travel to a counterparty in the file. The three anchor columns are the
    W3C Web Annotation Data Model's selectors: structural, quote-with-context,
    and position.
    """

    __table_args__ = (
        Index("ix_ds_annotation_document_state", "document_id", "anchor_state"),
        Index("ix_ds_annotation_clause", "anchor_clause_id"),
    )

    document_id = Column(String(36), ForeignKey("ds_document.id"), nullable=False)
    version_id = Column(String(36), nullable=True)
    kind = Column(String(24), nullable=False)  # comment | citation | proposal | risk

    anchor_clause_id = Column(String(64), nullable=True)
    anchor_quote_exact = Column(Text, nullable=True)
    # Prefix and suffix are what tell two identical sentences apart —
    # anchoring.CONTEXT_CHARS characters of each, measured on real contracts.
    anchor_quote_prefix = Column(Text, nullable=True)
    anchor_quote_suffix = Column(Text, nullable=True)
    anchor_start = Column(Integer, nullable=True)
    anchor_end = Column(Integer, nullable=True)

    # ok | moved | orphaned. An orphan is surfaced for a human to re-link and is
    # never deleted: silently dropping annotations is the failure this subsystem
    # exists to end.
    anchor_state = Column(String(16), nullable=False, default="ok")
    # Which rung of the ladder last resolved it (1-5). Logged because the
    # distribution is the only honest measure of how good clause identity is.
    anchor_rung = Column(Integer, nullable=True)

    body = Column(Text, nullable=True)
    proposed_text = Column(Text, nullable=True)  # proposals only
    status = Column(String(16), nullable=False, default="open")
    parent_annotation_id = Column(String(36), nullable=True)

    author_kind = Column(String(16), nullable=False, default="user")  # user | ai | system
    author_user_id = Column(String(36), nullable=True)
    author_name = Column(String(255), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class DsEvent(TableNameMixin, IdMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only record of what happened to a document.

    Separate from the app's audit log on purpose: docstudio must be able to
    explain a document's history without reaching into another subsystem.
    """

    __table_args__ = (Index("ix_ds_event_document", "document_id", "created_at"),)

    document_id = Column(String(36), nullable=False)
    version_id = Column(String(36), nullable=True)
    event_type = Column(String(64), nullable=False)
    details = Column(JSON, nullable=True)
    actor_user_id = Column(String(36), nullable=True)
