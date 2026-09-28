"""Editing a PDF without changing it, and every way an agreed change leaves it.

A PDF's words are painted in place, so a change to one is a mark beside it,
and each output is checked: the marked-up file keeps the original's bytes, the
Word version carries the marks as tracked changes, the page itself is changed
only where the new words fit and the page then reads exactly as agreed, and an
amendment says what changed clause by clause. Each test names the way that
goes wrong.
"""

import io
import uuid
import zipfile

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import delete

import app.models
from app.core.database import SessionLocal
from app.docstudio.models import DsAnnotation, DsClause, DsDocument, DsEvent, DsVersion
from app.main import app

URL = "/api/v1/docstudio/dev"
LINES = [
    "1. TERM. This Agreement lasts three years from the date of signature.",
    "2. FEES. Payment is due within thirty days of the date of invoice.",
    "3. LAW. This Agreement is governed by the laws of England.",
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def made():
    ids: list[str] = []
    yield ids
    session = SessionLocal()
    try:
        for document_id in ids:
            version_ids = [v.id for v in session.query(DsVersion).filter(DsVersion.document_id == document_id)]
            session.execute(delete(DsAnnotation).where(DsAnnotation.document_id == document_id))
            session.execute(delete(DsEvent).where(DsEvent.document_id == document_id))
            session.execute(delete(DsClause).where(DsClause.version_id.in_(version_ids)))
            session.execute(delete(DsVersion).where(DsVersion.document_id == document_id))
            session.execute(delete(DsDocument).where(DsDocument.id == document_id))
        session.commit()
    finally:
        session.close()


def _pdf(subset: bool = False) -> bytes:
    """A typed contract whose font is inside the file, as real ones are."""
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_font(fontname="F0", fontbuffer=fitz.Font("tiro").buffer)
    for i, line in enumerate(LINES + [f"Reference {uuid.uuid4()}."]):
        page.insert_text((72, 100 + 30 * i), line, fontname="F0", fontsize=11)
    if subset:
        document.subset_fonts()
    return document.tobytes()


def _run(client, made, content: bytes, name: str = "msa.pdf") -> dict:
    body = client.post(f"{URL}/run", files={"file": (name, content)}, data={"ai": "false"}).json()
    made.append(body["document_id"])
    return body


def _mark(client, version_id: str, quote: str, replacement: str) -> dict:
    res = client.post(f"{URL}/marks", data={"version_id": version_id, "quote": quote, "replacement": replacement})
    assert res.status_code == 200, res.text
    return res.json()


def _agree_all(client, body: dict) -> dict:
    for comment in body["comments"]:
        if comment["kind"] == "proposal":
            body = client.post(f"{URL}/marks/{comment['id']}/status", data={"status": "agreed"}).json()
    return body


def test_a_mark_on_a_pdf_changes_nothing_in_it(client, made):
    """Typing into a PDF cannot re-flow its page; the change is a mark beside it
    and the version's file stays byte for byte what it was."""
    body = _run(client, made, content := _pdf())

    marked = _mark(client, body["version_id"], "thirty days", "forty-five days")
    [mark] = [c for c in marked["comments"] if c["kind"] == "proposal"]

    assert marked["version_id"] == body["version_id"]  # no new version
    assert (mark["quote"], mark["proposed_text"], mark["status"], mark["author"]) == (
        "thirty days", "forty-five days", "open", "You")
    assert client.get(f"{URL}/versions/{body['version_id']}/file").content == content


def test_the_marked_up_pdf_keeps_the_original_bytes_and_marks_the_right_words(client, made):
    """An annotation written by rewriting the file would break a signature over
    it; one on the wrong words would mislead whoever reads it."""
    body = _run(client, made, content := _pdf())
    _mark(client, body["version_id"], "thirty days", "forty-five days")

    res = client.get(f"{URL}/versions/{body['version_id']}/marked-up")
    page = fitz.open(stream=res.content, filetype="pdf")[0]
    [words] = page.search_for("thirty days")
    annots = {a.type[1]: a for a in page.annots()}

    assert res.content.startswith(content) and res.headers["x-marks-on-words"] == "1"
    assert {"StrikeOut", "Caret"} <= set(annots) and annots["StrikeOut"].rect.intersects(words)
    assert "forty-five days" in annots["Caret"].info["content"]


def test_retyping_a_clause_makes_marks_on_a_pdf_and_tracked_changes_in_word(client, made):
    """A retyped clause saved as a whole new clause would bury the one word that
    changed; only the changed words are marked, as Word's compare does."""
    body = _run(client, made, _pdf())
    clause = client.post(f"{URL}/clauses/at", data={"version_id": body["version_id"], "quote": "thirty days"}).json()
    edited = client.post(f"{URL}/clauses/edit", data={
        "version_id": body["version_id"], "clause_id": clause["clause_id"],
        "text": clause["text"].replace("thirty", "forty-five").replace("invoice", "a valid invoice"),
    }).json()

    marks = [(c["quote"], c["proposed_text"]) for c in edited["comments"] if c["kind"] == "proposal"]
    assert edited["edited"]["placed"] == 2
    # An insertion is anchored on the word before it: "of" → "of a valid".
    assert marks == [("thirty", "forty-five"), ("of", "of a valid")]


def test_agreed_marks_become_tracked_changes_in_the_word_version(client, made):
    """Carried by hand, the agreed changes would be retyped; carried here they
    are the other side's tracked changes to accept, and leave the margin."""
    body = _agree_all(client, _mark(client, _run(client, made, _pdf())["version_id"], "thirty days",
                                    "forty-five days"))

    word = client.post(f"{URL}/marks/carry", data={"version_id": body["version_id"]}).json()

    assert word["carried"]["carried"] == 1 and word["editable"] is True
    assert [(c["deleted"], c["inserted"], c["author"]) for c in word["changes"]] == [("thirty", "forty-five", "You")]
    assert not [c for c in word["comments"] if c["kind"] == "proposal"]


def test_a_change_that_fits_is_written_on_the_page_and_checked(client, made):
    """The page must then read exactly as agreed, and the original's bytes stay
    at the start of the file, so the version before is never lost."""
    body = _agree_all(client, _mark(client, _run(client, made, content := _pdf())["version_id"], "thirty", "forty"))

    patched = client.post(f"{URL}/marks/patch", data={"version_id": body["version_id"]}).json()
    new_file = client.get(f"{URL}/versions/{patched['version_id']}/file").content

    assert patched["patched"]["patched"] == 1 and patched["patched"]["refused"] == []
    assert new_file.startswith(content)
    assert "within forty days" in fitz.open(stream=new_file, filetype="pdf")[0].get_text()


@pytest.mark.parametrize(("replacement", "why"), [
    ("", "leave a gap"),
    ("thirty days thirty days thirty days thirty days thirty days thirty", "longer than the room"),
    ("qz", "never prints"),  # letters the document never used, so its subset may lack them
])
def test_a_change_that_would_not_print_faithfully_is_refused_and_nothing_written(client, made, replacement, why):
    """A deletion leaves a hole, long words run into the next ones, and a letter
    the embedded subset lacks prints as a blank: each is refused with the reason."""
    body = _run(client, made, _pdf(subset=True))
    body = _agree_all(client, _mark(client, body["version_id"], "thirty", replacement))

    patched = client.post(f"{URL}/marks/patch", data={"version_id": body["version_id"]}).json()

    assert patched["patched"]["patched"] == 0 and why in patched["patched"]["refused"][0]["why"]
    assert patched["version_id"] == body["version_id"]


def test_an_amendment_says_what_changed_clause_by_clause(client, made):
    """A signed contract is not edited; its change is a separate document a
    court could read on its own."""
    body = _agree_all(client, _mark(client, _run(client, made, _pdf())["version_id"], "thirty days",
                                    "forty-five days"))

    res = client.get(f"{URL}/versions/{body['version_id']}/amendment")
    text = "\n".join(p.text for p in Document(io.BytesIO(res.content)).paragraphs)

    assert "In clause 2" in text and "“thirty days” with “forty-five days”" in text
    assert "remains unchanged" in text


def test_formatting_in_word_is_a_tracked_change_and_a_highlight_on_a_pdf_is_a_mark(client, made):
    """Bold applied silently could never be rejected; a highlight written into
    a PDF would change the legal record."""
    document = Document()
    document.add_paragraph(f"Payment is due within thirty days. Reference {uuid.uuid4()}.")
    buffer = io.BytesIO()
    document.save(buffer)
    word = _run(client, made, buffer.getvalue(), "msa.docx")
    pdf = _run(client, made, content := _pdf())

    bold = client.post(f"{URL}/redline/format", data={
        "version_id": word["version_id"], "quote": "thirty days", "style": "bold"}).json()
    lit = client.post(f"{URL}/highlights", data={"version_id": pdf["version_id"], "quote": "three years"}).json()

    assert [(c["kind"], c["inserted"]) for c in bold["changes"]] == [("format", "thirty days")]
    assert "<w:b/>" in zipfile.ZipFile(io.BytesIO(client.get(f"{URL}/versions/{bold['version_id']}/file").content)
                                       ).read("word/document.xml").decode()
    assert [c["kind"] for c in lit["comments"]] == ["highlight"]
    assert client.get(f"{URL}/versions/{pdf['version_id']}/file").content == content


def test_the_history_keeps_every_version_and_the_compare_shows_only_what_changed(client, made):
    """Version 1 is the file as it arrived, for ever; and comparing the Word
    version with the one before shows the one change, not the whole document."""
    body = _agree_all(client, _mark(client, _run(client, made, _pdf())["version_id"], "thirty days",
                                    "forty-five days"))
    word = client.post(f"{URL}/marks/carry", data={"version_id": body["version_id"]}).json()
    accepted = client.post(f"{URL}/redline/resolve", data={"version_id": word["version_id"], "everything": "true"}).json()

    versions = client.get(f"{URL}/documents/{accepted['document_id']}/history").json()["versions"]
    diff = client.get(f"{URL}/compare", params={"older": versions[1]["version_id"],
                                                "newer": versions[0]["version_id"]}).json()

    assert [v["number"] for v in versions] == [3, 2, 1] and versions[-1]["what"] == "The file as it arrived"
    assert "with 1 agreed change" in versions[1]["what"]
    assert diff["counts"]["clauses"] == 0  # accepting changes the markup, not the words


def test_a_scan_takes_marks_for_its_amendment_and_not_as_tracked_changes(client, made):
    """A signed scan changes by amendment. It can be rebuilt as Word to read and
    edit (from what OCR read), but tracked changes against a machine's reading
    of a photograph are not how a signed contract is changed."""
    body = _run(client, made, _pdf())
    session = SessionLocal()
    try:
        session.get(DsVersion, body["version_id"]).parser_name = "pdf+ocr:test"
        session.commit()
    finally:
        session.close()
    body = _agree_all(client, _mark(client, body["version_id"], "thirty days", "forty-five days"))

    refused = client.post(f"{URL}/marks/carry", data={"version_id": body["version_id"]})
    amendment = client.get(f"{URL}/versions/{body['version_id']}/amendment")

    assert refused.status_code == 422 and "amendment" in refused.json()["detail"]
    assert amendment.status_code == 200
