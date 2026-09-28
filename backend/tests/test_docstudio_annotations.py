"""Notes on a document follow their words into every new version, or say they are lost.

End to end through the database: ingest a contract, attach notes, ingest an
edited version of the same document, and check where each note went, that a
lost one is listed and can be re-linked, and that the report says all of it.
"""

import uuid
from io import BytesIO

import pytest
from docx import Document
from sqlalchemy import select

import app.models  # noqa: F401  (register every mapper)
from app.core.database import SessionLocal
from app.docstudio.annotations import annotate, find_quote, orphans, relink
from app.docstudio.models import DsAnnotation, DsClause, DsDocument, DsEvent, DsVersion
from app.docstudio.parsing.registry import DOCX_MIME
from app.docstudio.report import report
from app.docstudio.service import ingest

ORG = "docstudio-annotations-test-org"


def _docx(*paragraphs: str) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        for model in (DsAnnotation, DsEvent, DsClause, DsVersion, DsDocument):
            for row in session.scalars(select(model).where(model.org_id == ORG)):
                session.delete(row)
            session.flush()
        session.commit()
        session.close()


def _note(db, version_id, words, body):
    version = db.get(DsVersion, version_id)
    start, end = find_quote(version.flat_text, words)
    return annotate(db, version_id=version_id, start=start, end=end, body=body)


@pytest.fixture
def edited(db):
    """Version 1 with three notes, then version 2: a clause inserted near the
    top, the liability clause reworded, and the notices clause deleted."""
    marker = f"Reference {uuid.uuid4()}."  # unique bytes, so nothing deduplicates
    first = ingest(
        db,
        org_id=ORG,
        content=_docx(
            f"1. TERM. The term of this Agreement is three years. {marker}",
            "2. FEES. Payment is due within thirty days of invoice.",
            "3. LIABILITY. Neither party shall be liable for indirect damages arising from it.",
            "4. NOTICES. Notices must be given in writing to the registered office.",
        ),
        filename="msa.docx",
        mime_type=DOCX_MIME,
    )
    notes = {
        "fees": _note(db, first.version_id, "Payment is due within thirty days", "Push for 45 days."),
        "liability": _note(
            db, first.version_id, "Neither party shall be liable for indirect damages", "Cap is too low."
        ),
        "notices": _note(
            db, first.version_id, "Notices must be given in writing to the registered office", "Add email."
        ),
    }
    second = ingest(
        db,
        org_id=ORG,
        document_id=first.document_id,
        content=_docx(
            f"1. TERM. The term of this Agreement is three years. {marker}",
            "2. AUDIT. The Customer may audit the Supplier once a year.",
            "3. FEES. Payment is due within thirty days of invoice.",
            "4. LIABILITY. Neither party shall be liable for indirect or consequential damages "
            "arising from it.",
        ),
        filename="msa-v2.docx",
        mime_type=DOCX_MIME,
    )
    return first, second, notes


def test_notes_follow_their_words_into_the_new_version(db, edited):
    _, second, notes = edited
    version = db.get(DsVersion, second.version_id)
    clauses = {c.clause_id: c for c in db.scalars(select(DsClause).where(DsClause.version_id == second.version_id))}

    fees, liability = notes["fees"], notes["liability"]

    # Renumbered from 2 to 3, but the same clause: found in it on the first rung.
    assert (fees.anchor_state, fees.anchor_rung, fees.version_id) == ("ok", 1, second.version_id)
    assert clauses[fees.anchor_clause_id].number_label == "3."
    assert version.flat_text[fees.anchor_start : fees.anchor_end] == "Payment is due within thirty days"
    # Reworded: found by its near-identical words, and now quotes the new ones.
    assert (liability.anchor_state, liability.anchor_rung) == ("moved", 4)
    assert "indirect or consequential" in liability.anchor_quote_exact
    assert clauses[liability.anchor_clause_id].number_label == "4."


def test_a_note_whose_clause_was_deleted_is_lost_listed_and_can_be_relinked(db, edited):
    first, second, notes = edited
    lost = notes["notices"]

    assert lost.anchor_state == "orphaned"
    # Nothing is deleted: it keeps the words it was on, and waits in the queue.
    assert lost.anchor_quote_exact.startswith("Notices must be given in writing")
    assert [a.id for a in orphans(db, first.document_id)] == [lost.id]

    version = db.get(DsVersion, second.version_id)
    start, end = find_quote(version.flat_text, "The Customer may audit the Supplier")
    relink(db, lost.id, start=start, end=end)

    assert (lost.anchor_state, lost.version_id) == ("ok", second.version_id)
    assert orphans(db, first.document_id) == []


def test_the_new_version_records_how_every_note_was_found(db, edited):
    """The rung distribution is the honest measure of how well anchors
    survive, so it is stored, not logged and thrown away."""
    _, second, _ = edited

    event = db.scalars(
        select(DsEvent).where(
            DsEvent.version_id == second.version_id, DsEvent.event_type == "annotations.reanchored"
        )
    ).one()

    assert event.details["rungs"] == {"1": 1, "4": 1, "5": 1}


def test_the_report_lists_lost_notes_first(db, edited):
    _, second, _ = edited

    page = report(db, second.version_id)

    assert "## Annotations" in page
    assert "Lost — 1 note to re-link" in page
    assert "words close to its own — they were edited" in page
    assert page.index("Lost — 1 note") < page.index("How it was found")


def test_words_that_appear_twice_are_refused_rather_than_guessed(db):
    with pytest.raises(LookupError, match="appear 2 times"):
        find_quote("The fee is due. The fee is due.", "The fee is due")


# --- words selected on the drawn page --------------------------------------------


def test_a_selection_is_found_through_markup_entities_and_line_breaks():
    """The page shows "Time & Material" on one line; the stored text holds OCR
    markup, an entity, and a line break pdf.js joined with no space."""
    from app.docstudio.annotations import locate

    text = "Fees for <b>Time &amp; Mate-\nrial</b> work are due monthly."

    start, end = locate(text, "Time & Mate-rial work")

    assert text[start:end] == "Time &amp; Mate-\nrial</b> work"


def test_the_words_around_a_selection_pick_which_of_two_identical_passages():
    """ "the Services" is in half the clauses of a real contract; the text either
    side of the selection is what says which one the reader meant."""
    from app.docstudio.annotations import locate

    text = "1. The Supplier shall start the Services. 2. The Customer may end the Services."

    start, _ = locate(text, "the Services", prefix="The Customer may end ", suffix=".")

    assert start == text.rindex("the Services")


def test_identical_words_with_nothing_to_tell_them_apart_are_refused():
    from app.docstudio.annotations import locate

    with pytest.raises(LookupError, match="appear 2 times"):
        locate("the Services and the Services", "the Services")


def test_words_not_in_the_stored_text_are_refused_saying_why():
    """Struck-through words are drawn in a Word file but are not its text."""
    from app.docstudio.annotations import locate

    with pytest.raises(LookupError, match="Struck-through"):
        locate("Liability is capped at twelve months.", "six months")


# --- threads: replies and resolving --------------------------------------------------


def test_a_reply_follows_its_comment_into_a_new_version_and_is_never_lost(db):
    """A reply has no words of its own to look for. Put through the ladder it
    would come out orphaned, and be listed as lost on every new version."""
    from app.docstudio.annotations import reply

    marker = f"Reference {uuid.uuid4()}."
    first = ingest(
        db, org_id=ORG, filename="a.docx", mime_type=DOCX_MIME,
        content=_docx(f"1. TERM. Three years. {marker}", "2. FEES. Payment is due within thirty days of invoice."),
    )
    comment = _note(db, first.version_id, "Payment is due within thirty days", "Push for 45 days.")
    answer = reply(db, comment.id, body="Agreed: ask for 45.")
    second = ingest(
        db, org_id=ORG, document_id=first.document_id, filename="b.docx", mime_type=DOCX_MIME,
        content=_docx(
            f"1. TERM. Three years. {marker}", "2. AUDIT. Once a year.",
            "3. FEES. Payment is due within thirty days of invoice.",
        ),
    )

    assert db.get(DsAnnotation, comment.id).version_id == second.version_id
    assert db.get(DsAnnotation, answer.id).parent_annotation_id == comment.id
    assert orphans(db, first.document_id) == []


def test_a_reply_to_a_reply_joins_the_same_thread(db, edited):
    """Word's threads are one level deep."""
    from app.docstudio.annotations import reply

    _, _, notes = edited
    first = reply(db, notes["fees"].id, body="a")

    assert reply(db, first.id, body="b").parent_annotation_id == notes["fees"].id


def test_resolving_and_deleting_a_thread_are_recorded_and_take_its_replies(db, edited):
    from app.docstudio.annotations import remove, reply, set_resolved

    _, _, notes = edited
    thread = notes["fees"]
    answer = reply(db, thread.id, body="ok")

    set_resolved(db, answer.id, True)  # resolving from a reply resolves its thread
    resolved = thread.status
    set_resolved(db, thread.id, False)
    remove(db, thread.id)

    events = {e.event_type for e in db.scalars(select(DsEvent).where(DsEvent.document_id == thread.document_id))}
    assert resolved == "resolved"
    assert db.get(DsAnnotation, answer.id) is None
    assert {"annotation.replied", "annotation.resolved", "annotation.reopened", "annotation.deleted"} <= events
