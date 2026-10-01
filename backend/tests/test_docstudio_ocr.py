"""Scanned documents: detecting them, reading them, and failing loudly.

Found by a real file — a 14-page scanned MSA e-signed through DocuSign. Its
pages are images, so it holds no contract text at all, but DocuSign had stamped
an envelope ID across every page. Those 14 identical stamps were 798 of its 860
characters, which was enough to clear a naive "is there any text here?" check.

The document parsed cleanly, reported no warnings, and produced sixteen
"clauses" of which fourteen were the same stamp. A contract containing nothing
readable looked exactly like a contract that had been read.
"""

from dataclasses import dataclass

import pytest

from app.docstudio.ocr import OcrResult, run_ocr
from app.docstudio.parsing.base import ParsedBlock, ParsedDocument
from app.docstudio.parsing.furniture import _furniture_key, _strip_furniture
from app.docstudio.parsing.text import blocks_from_text
from app.docstudio.service import _apply_ocr


def _page_blocks(per_page: dict[int, list[str]]) -> list[ParsedBlock]:
    return [
        ParsedBlock(text=text, page_number=page)
        for page, texts in per_page.items()
        for text in texts
    ]


# --- furniture --------------------------------------------------------------


def test_a_stamp_on_every_page_is_not_content():
    """The defect this file exists for. Fourteen copies of a DocuSign envelope
    id are page furniture; counting them as text is what hid an unreadable
    contract."""
    stamp = "DocuSign Envelope ID: D6EA132C-BF63-4318-9363-35DB39116D9F"
    blocks = _page_blocks({page: [stamp] for page in range(1, 15)})
    blocks[10] = ParsedBlock(text="Robert Dudacek", page_number=11)

    kept, removed = _strip_furniture(blocks, page_count=14)

    assert [b.text for b in kept] == ["Robert Dudacek"]
    assert removed == len(stamp) * 13


def test_a_running_header_matches_across_pages_despite_its_page_number():
    """"Legal Services Agreement Page 1 of 7" differs on every page by two
    digits. Comparing raw text would treat each as unique content and leave all
    seven in the clause list."""
    assert _furniture_key("Legal Services Agreement Page 1 of 7") == _furniture_key(
        "Legal Services Agreement Page 12 of 7"
    )


def test_a_repeated_line_in_a_short_document_is_left_alone():
    """Two pages sharing a heading is not the same evidence as fourteen sharing
    a stamp. Stripping on that little signal would delete real content."""
    blocks = _page_blocks({1: ["TERM"], 2: ["TERM"]})
    kept, removed = _strip_furniture(blocks, page_count=2)

    assert len(kept) == 2
    assert removed == 0


def test_a_clause_appearing_on_only_some_pages_survives():
    """Furniture is defined by appearing on *most* pages. A clause quoted twice
    in a long contract must not vanish."""
    blocks = _page_blocks({1: ["Indemnity applies."], 2: ["Indemnity applies."], 3: ["x"],
                           4: ["y"], 5: ["z"], 6: ["w"]})
    kept, _ = _strip_furniture(blocks, page_count=6)

    assert sum(1 for b in kept if b.text == "Indemnity applies.") == 2


# --- when OCR runs ----------------------------------------------------------


@dataclass
class _Provider:
    name: str
    text: str = ""
    error: Exception | None = None
    calls: int = 0

    def extract(self, content, *, filename, mime_type):
        self.calls += 1
        if self.error:
            raise self.error
        return OcrResult(text=self.text, provider=self.name, quality=0.9)


def _scanned() -> ParsedDocument:
    return ParsedDocument(blocks=[], page_count=14, warnings=["scanned"], needs_ocr=True)


def _apply(parsed, providers):
    return _apply_ocr(
        parsed, b"%PDF", filename="c.pdf", mime_type="application/pdf", providers=providers
    )


def test_ocr_reads_a_document_whose_pages_are_pictures():
    provider = _Provider("reducto", text="# 1. DEFINITIONS\n\nConfidential Information means...")
    parsed, used, notes = _apply(_scanned(), [provider])

    assert used == "reducto"
    assert parsed.needs_ocr is False
    assert next(b.text for b in parsed.blocks) == "DEFINITIONS"
    assert any("read by OCR" in n for n in notes)


def test_ocr_is_not_run_on_a_document_that_already_has_text():
    """OCR is charged per page. A contract with real text gains nothing from
    it, and re-reading every upload would be a standing bill for no benefit."""
    provider = _Provider("reducto", text="should not be called")
    readable = ParsedDocument(blocks=[ParsedBlock(text="1. TERM")], page_count=3)

    parsed, used, _ = _apply(readable, [provider])

    assert provider.calls == 0
    assert used is None
    assert parsed is readable


def test_a_failing_provider_falls_through_to_a_working_one():
    """One deployment's primary provider answers 403 to every upload while a
    working key sits unused in the same settings object. Choosing a provider by
    "is it configured" leaves every scanned contract permanently empty."""
    broken = _Provider("databricks", error=RuntimeError("403 Forbidden"))
    working = _Provider("reducto", text="MASTER SERVICES AGREEMENT")

    _, used, notes = _apply(_scanned(), [broken, working])

    assert used == "reducto"
    assert working.calls == 1
    assert any("403" in n for n in notes)


def test_a_provider_returning_no_text_is_also_fallen_through():
    """A 200 with an empty body has failed just as completely as a raise, and a
    scanned PDF is exactly the input that produces it."""
    empty = _Provider("databricks", text="")
    working = _Provider("reducto", text="MASTER SERVICES AGREEMENT")

    _, used, _ = _apply(_scanned(), [empty, working])

    assert used == "reducto"


def test_a_working_provider_is_not_billed_twice():
    first = _Provider("databricks", text="MASTER SERVICES AGREEMENT")
    second = _Provider("reducto", text="unreachable")

    _, used, _ = _apply(_scanned(), [first, second])

    assert used == "databricks"
    assert second.calls == 0


# --- when OCR cannot help ---------------------------------------------------


def test_a_failed_ocr_keeps_saying_the_document_is_unreadable():
    """The whole point. If OCR fails and the parse quietly becomes a short
    document, an empty contract advances through review reported as clean."""
    parsed, used, notes = _apply(
        _scanned(),
        [_Provider("databricks", error=RuntimeError("403")),
         _Provider("reducto", error=RuntimeError("401"))],
    )

    assert used is None
    assert parsed.needs_ocr is True
    assert parsed.warnings == ["scanned"]
    assert any("403" in n and "401" in n for n in notes)


def test_no_configured_provider_says_so_rather_than_going_quiet():
    parsed, used, notes = _apply(_scanned(), [])

    assert used is None
    assert parsed.needs_ocr is True
    assert any("No OCR provider is configured" in n for n in notes)


def test_every_provider_reason_is_reported_not_just_the_first():
    """An operator looking at an empty contract has to tell "one key is wrong"
    from "this document is genuinely unreadable"."""
    outcome = run_ocr(
        [_Provider("a", error=RuntimeError("403")), _Provider("b", error=RuntimeError("401"))],
        b"x",
        filename="c.pdf",
        mime_type="application/pdf",
    )

    assert outcome.succeeded is False
    assert outcome.errors == ["a: 403", "b: 401"]


# --- OCR output becomes clauses ---------------------------------------------


@pytest.mark.parametrize(
    "line, label, level",
    [
        ("# 1. DEFINITIONS", "1.", 1),
        ("## 2.1 Change Order", "2.1", 2),
        ("### (a) Fixed Price", "(a)", 3),
    ],
)
def test_a_markdown_heading_keeps_its_clause_number(line, label, level):
    """OCR returns markdown, so a numbered heading arrives as "## 2.1 Change
    Order". Taking the level from the number of hashes loses the number
    entirely — and the number is the only citable part."""
    block = blocks_from_text(line)[0]

    assert block.number_label == label
    assert block.level == level
    assert not block.text.startswith(label)


# --- the two defects a real contract exposed --------------------------------


@pytest.mark.parametrize(
    "line, label",
    [
        ('- 1.1 "Agreement" means this Master Services Agreement.', "1.1"),
        ("- 3.1 Client at its cost shall provide Mindtree with Inputs.", "3.1"),
        ("* 9.2 Direct Damages. Aggregate liability shall be limited.", "9.2"),
        ("- (a) Fixed Price Project: such projects are for a fixed fee.", "(a)"),
    ],
)
def test_a_markdown_bullet_does_not_hide_the_clause_number(line, label):
    """OCR returns numbered sub-clauses as list items. Requiring the digit first
    left 29 of one MSA's clauses with their text present but no number — so
    every definition in section 1 existed and none could be cited as 1.1."""
    block = blocks_from_text(line)[0]

    assert block.number_label == label
    assert not block.text.startswith(label)


def test_furniture_is_stripped_when_blocks_have_no_page_numbers():
    """OCR returns one flat string, so its blocks carry no page number and the
    page-based test silently does nothing — on exactly the documents that need
    it most. In a real scanned MSA that left 68 of 219 clause rows as headers,
    footers and captions describing the company seal."""
    stamp = "DocuSign Envelope ID: D6EA132C-BF63-4318-9363-35DB39116D9F"
    blocks = [ParsedBlock(text=stamp) for _ in range(14)]
    blocks.insert(3, ParsedBlock(text="9.2 Direct Damages. Liability is capped."))

    kept, removed = _strip_furniture(blocks, page_count=14)

    assert [b.text for b in kept] == ["9.2 Direct Damages. Liability is capped."]
    assert removed == len(stamp) * 14


def test_a_clause_repeated_a_few_times_is_not_stripped_as_furniture():
    """Without page numbers the test counts occurrences, so the threshold has to
    stay tied to the page count — or a phrase used three times in a 40-page
    contract disappears."""
    blocks = [ParsedBlock(text="Confidential Information") for _ in range(3)]
    blocks += [ParsedBlock(text=f"clause {i}") for i in range(20)]

    kept, removed = _strip_furniture(blocks, page_count=40)

    assert sum(1 for b in kept if b.text == "Confidential Information") == 3
    assert removed == 0


# --- reading any bytes once -------------------------------------------------


@pytest.fixture
def db():
    import uuid

    from sqlalchemy import delete

    import app.models  # noqa: F401  (register every mapper)
    from app.core.database import SessionLocal
    from app.docstudio.models import DsOcrResult

    session = SessionLocal()
    session.sha = f"test-{uuid.uuid4().hex}"
    try:
        yield session
    finally:
        session.rollback()
        session.execute(delete(DsOcrResult).where(DsOcrResult.sha256 == session.sha))
        session.commit()
        session.close()


def _read(providers):
    outcome = run_ocr(providers, b"%PDF", filename="c.pdf", mime_type="application/pdf")
    return outcome.result


def test_the_same_bytes_are_read_by_ocr_once(db):
    """OCR is paid per page and not deterministic: a second reading costs money
    and mints different clauses, orphaning everything anchored to the first."""
    from app.docstudio.ocr import cached

    first = _Provider("reducto", text="1. TERM. Three years.")
    _read(cached(db, db.sha, [first]))
    db.flush()
    second = _Provider("reducto", text="1. TERM. Three yeers.")

    result = _read(cached(db, db.sha, [second]))

    assert (first.calls, second.calls) == (1, 0)
    assert result.text == "1. TERM. Three years."


def test_a_stored_reading_from_a_provider_no_longer_configured_is_not_used(db):
    from app.docstudio.ocr import cached

    _read(cached(db, db.sha, [_Provider("databricks", text="old reading")]))
    db.flush()
    current = _Provider("reducto", text="new reading")

    assert _read(cached(db, db.sha, [current])).text == "new reading"
    assert current.calls == 1


def test_a_failed_reading_is_not_stored(db):
    """Storing an empty answer would make the failure permanent."""
    from app.docstudio.ocr import cached

    _read(cached(db, db.sha, [_Provider("reducto", error=RuntimeError("403"))]))
    db.flush()
    retry = _Provider("reducto", text="read on the second try")

    assert _read(cached(db, db.sha, [retry])).text == "read on the second try"


# --- the words laid over a scan ------------------------------------------------


def test_a_scans_paragraphs_come_back_as_printed_with_their_boxes(db):
    """The viewer lays these invisibly over a scanned page so it can be selected
    and searched. So they must be the words on the page: the provider's markup
    gone, a template's printed fill-ins kept, and a picture's *description*
    left out — nobody could find "blue circular seal" on the paper."""
    from types import SimpleNamespace

    from app.docstudio.models import DsOcrResult
    from app.docstudio.ocr import page_text

    box = {"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.3}
    db.add(DsOcrResult(sha256=db.sha, provider="reducto", text="-", blocks=[
        {"type": "Text", "page": 1, "bbox": box, "content": "Fees for <b>Time &amp; Material</b> on <Date>."},
        {"type": "Table", "page": 1, "bbox": box,
         "content": "<table><tr><th>No</th><th>Item</th></tr><tr><td>1</td><td><empty></td></tr></table>"},
        {"type": "Key Value", "page": 2, "bbox": box, "content": "By: <signature>\nName: Said Taiym"},
        {"type": "Figure", "page": 2, "bbox": box, "content": "Blue circular seal reading BENGALURU"},
    ]))
    db.flush()

    scan = page_text(db, SimpleNamespace(parser_name="pdf+ocr:reducto", sha256=db.sha))

    assert [(r["page"], r["text"]) for r in scan] == [
        (1, "Fees for Time & Material on <Date>."),
        (1, "No Item\n1"),
        (2, "By:\nName: Said Taiym"),
    ]
    assert scan[0]["bbox"] == box


def test_a_file_read_from_its_own_text_gets_no_ocr_words(db):
    """Its pages already carry their words; laying a second set over them would
    make every search hit twice."""
    from types import SimpleNamespace

    from app.docstudio.ocr import page_text

    assert page_text(db, SimpleNamespace(parser_name="pdf", sha256=db.sha)) == []
