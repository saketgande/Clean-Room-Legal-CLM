"""Docstudio ingest: what gets written, and what must not be.

These guard the boundary rules the subsystem exists to enforce — a document
nobody could read is never stored as a document with no text, and re-ingesting
identical bytes never mints a second set of clause ids underneath annotations
anchored to the first.
"""

from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from sqlalchemy import select

import app.models  # noqa: F401  (register every mapper)
from app.core.database import SessionLocal
from app.docstudio.models import DsClause, DsDocument, DsEvent, DsVersion
from app.docstudio.parsing.base import UnsupportedFormat
from app.docstudio.parsing.registry import DOCX_MIME
from app.docstudio.service import ingest

ORG = "docstudio-test-org"


def _docx(paragraphs: list[str]) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def db():
    """A session scoped to a throwaway org, cleaned up afterwards — the suite
    runs against the dev database and must not leave rows behind."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        for model in (DsEvent, DsClause, DsVersion, DsDocument):
            for row in session.scalars(select(model).where(model.org_id == ORG)):
                session.delete(row)
        session.commit()
        session.close()


def _ingest(db, content=None, *, mime=DOCX_MIME, filename="c.docx", **kwargs):
    return ingest(
        db,
        org_id=ORG,
        content=content if content is not None else _docx(["1. TERM.", "2. FEES."]),
        filename=filename,
        mime_type=mime,
        **kwargs,
    )


# --- what ingest writes ------------------------------------------------------


def test_ingest_writes_a_document_a_version_and_its_clauses(db):
    result = _ingest(db)

    assert result.version_number == 1
    assert result.clause_count == 2
    document = db.get(DsDocument, result.document_id)
    assert document.current_version_id == result.version_id
    clauses = db.scalars(
        select(DsClause).where(DsClause.version_id == result.version_id)
    ).all()
    assert {c.text for c in clauses} == {"1. TERM.", "2. FEES."}


def test_the_version_records_which_parser_produced_it(db):
    """Re-parsing with a different parser version yields different offsets, so a
    version that does not say how it was parsed cannot be reproduced or audited
    — and an offset bug becomes impossible to attribute."""
    result = _ingest(db)
    version = db.get(DsVersion, result.version_id)

    assert version.parser_name == "docx"
    assert version.parser_version


def test_flat_text_contains_every_clause_at_its_stored_offsets(db):
    """The invariant the whole citation model rests on."""
    result = _ingest(db)
    version = db.get(DsVersion, result.version_id)

    for clause in db.scalars(
        select(DsClause).where(DsClause.version_id == result.version_id)
    ):
        assert version.flat_text[clause.char_start : clause.char_end] == clause.text


def test_an_event_records_the_ingest(db):
    """An operator asking why a document looks thin reads the event log. If
    ingest leaves no trace, the answer is unavailable after the fact."""
    result = _ingest(db)
    event = db.scalar(
        select(DsEvent).where(DsEvent.document_id == result.document_id)
    )

    assert event.event_type == "version.ingested"
    assert event.details["clauses"] == 2
    assert "docx" in event.details["parser"]


# --- what ingest refuses -----------------------------------------------------


def test_an_unreadable_format_writes_nothing_at_all(db):
    """A document nobody could read must not become a document with no text in
    it — everything downstream then treats "we could not read this" as "this
    contract says nothing", which is how an empty contract reached REVIEW and
    was reported clean."""
    before = db.scalar(select(DsDocument).where(DsDocument.org_id == ORG))

    with pytest.raises(UnsupportedFormat):
        _ingest(db, b"\xd0\xcf\x11\xe0" + b"\x00" * 200, mime="application/msword")

    assert db.scalar(select(DsDocument).where(DsDocument.org_id == ORG)) is before


# --- versions ----------------------------------------------------------------


def test_identical_bytes_do_not_create_a_second_version(db):
    """Re-parsing the same bytes with the same parser produces an identical
    version carrying *new* clause ids, silently orphaning every annotation
    anchored to the first set."""
    # Built once: python-docx stamps its zip entries with the clock, at two
    # seconds' resolution, so two builds straddling a tick are different bytes
    # and this test failed now and then while testing nothing.
    content = _docx(["1. TERM.", "2. FEES."])
    first = _ingest(db, content)
    again = _ingest(db, content, document_id=first.document_id)

    assert again.deduplicated is True
    assert again.version_id == first.version_id
    assert again.clause_count == first.clause_count
    assert db.scalar(
        select(DsVersion).where(DsVersion.document_id == first.document_id).order_by(
            DsVersion.version_number.desc()
        )
    ).version_number == 1


def test_different_bytes_add_a_version_and_leave_the_first_alone(db):
    """Versions are immutable. A new version must never rewrite the old one —
    that is what makes a citation into V1 still true after V2 exists."""
    first = _ingest(db)
    first_text = db.get(DsVersion, first.version_id).flat_text

    second = _ingest(
        db,
        _docx(["1. TERM.", "2. FEES.", "3. LIABILITY."]),
        document_id=first.document_id,
    )

    assert second.version_number == 2
    assert second.version_id != first.version_id
    assert db.get(DsVersion, first.version_id).flat_text == first_text
    assert db.get(DsDocument, first.document_id).current_version_id == second.version_id


def test_clause_ids_are_unique_within_a_version(db):
    """Two clauses sharing an id would make an annotation ambiguous — it would
    resolve to whichever row came back first."""
    result = _ingest(db, _docx(["Reserved.", "Reserved.", "Reserved."]))
    ids = [
        c.clause_id
        for c in db.scalars(select(DsClause).where(DsClause.version_id == result.version_id))
    ]

    assert len(ids) == len(set(ids)) == 3


def test_re_ingesting_a_scanned_document_does_not_ocr_it_again(db):
    """A version read by OCR records "pdf+ocr:reducto" as its parser, so
    matching the bare parser name never found one and dedup was dead for every
    scanned document — twelve of the thirteen in the sample corpus.

    The cost is not just a wasted paid API call. A second ingest minted a fresh
    set of clause ids, orphaning every annotation anchored to the first, which
    is the exact failure the check exists to prevent. And OCR is not
    deterministic, so the re-parse is not even the same text.
    """
    from app.docstudio.ocr import OcrResult

    calls = []

    class _Provider:
        name = "fake"

        def extract(self, content, *, filename, mime_type):
            calls.append(filename)
            return OcrResult(text="1. TERM. Three years.", provider="fake")

    scan = b"%PDF-1.4\n%\xc2\xb5\xc2\xb6\ntrailer<<>>"
    import fitz

    document = fitz.open()
    document.new_page()  # a page with no text at all, so OCR is required
    scan = document.tobytes()

    first = _ingest(db, scan, mime="application/pdf", filename="scan.pdf", ocr_providers=[_Provider()])
    second = _ingest(
        db,
        scan,
        mime="application/pdf",
        filename="scan.pdf",
        document_id=first.document_id,
        ocr_providers=[_Provider()],
    )

    assert second.deduplicated is True
    assert second.version_id == first.version_id
    assert len(calls) == 1


# --- hostile and damaged files -----------------------------------------------


@pytest.mark.parametrize(
    ("content", "mime", "filename"),
    [
        (b"not a zip at all", DOCX_MIME, "c.docx"),
        (None, DOCX_MIME, "c.docx"),  # built in the test: a zip, but not Word
        (b"garbage that is not a pdf", "application/pdf", "c.pdf"),
    ],
    ids=["docx-not-a-zip", "docx-zip-without-word-parts", "pdf-garbage"],
)
def test_a_damaged_file_is_refused_not_crashed_on(db, content, mime, filename):
    """A corrupt upload is the uploader's problem, not a server fault. Each of
    these escaped as a raw ValueError / KeyError / FileDataError, which the
    dev page returned as a 500 — and a real API would have too."""
    if content is None:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "an xlsx renamed to .docx looks like this")
        content = buffer.getvalue()

    with pytest.raises(UnsupportedFormat):
        _ingest(db, content, mime=mime, filename=filename)

    db.rollback()
    assert db.scalar(select(DsDocument).where(DsDocument.org_id == ORG)) is None


def test_nul_bytes_in_extracted_text_do_not_fail_the_insert(db):
    """Postgres TEXT rejects NUL, so one stray 0x00 in a .txt (or in OCR or PDF
    text) made the whole ingest die with a psycopg DataError at INSERT time."""
    result = _ingest(db, b"1. TERM\x00. Three years.\x00", mime="text/plain", filename="c.txt")
    db.flush()

    version = db.get(DsVersion, result.version_id)
    assert "\x00" not in version.flat_text
    assert "TERM. Three years." in version.flat_text
