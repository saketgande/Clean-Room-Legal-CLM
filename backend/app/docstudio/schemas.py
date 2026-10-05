"""What the API sends and receives. Shapes only — no logic lives here."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ClauseOut(BaseModel):
    clause_id: str
    seq: int
    parent_clause_id: str | None = None
    number_label: str | None = None
    level: int
    clause_type: str
    text: str
    char_start: int
    char_end: int
    page_number: int | None = None
    # Page fractions, origin top-left: what a viewer needs to draw a box on the
    # page at any zoom. `regions` holds every piece of a clause rejoined across
    # a page break; `bbox` is the first of them.
    bbox: dict | None = None
    regions: list[dict] | None = None
    # Who decided where this clause sits: numbering, list, ai, undecided…
    structure_source: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AnnotationOut(BaseModel):
    id: str
    kind: str
    body: str | None = None
    status: str
    anchor_clause_id: str | None = None
    anchor_quote_exact: str | None = None
    anchor_start: int | None = None
    anchor_end: int | None = None
    anchor_state: str
    anchor_rung: int | None = None

    model_config = ConfigDict(from_attributes=True)


class VersionSummary(BaseModel):
    id: str
    document_id: str
    version_number: int
    filename: str | None = None
    mime_type: str
    byte_size: int
    page_count: int | None = None
    # False when the file itself was never stored (ingested before Phase 3, or
    # storage was unavailable). The view says so rather than showing an error.
    has_file: bool
    # A scan whose OCR found nothing has no text to show; the view opens on the
    # original instead of an empty panel.
    has_text: bool
    read_by: str
    created_at: datetime | None = None


class DocumentOut(BaseModel):
    id: str
    title: str | None = None
    external_ref: str | None = None
    version_count: int
    current: VersionSummary | None = None


class VersionDetail(VersionSummary):
    flat_text: str
    parse_warnings: list[str] | None = None
    clauses: list[ClauseOut]
    annotations: list[AnnotationOut]
