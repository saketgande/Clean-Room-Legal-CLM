"""A temporary page for trying Phases 1 to 3 on a file. Development only.

Mounted inside `main.py`'s `if non_prod:` block beside the debug router, so it
does not exist in production. It has no sign-in, by decision: it is a local test
tool. Anyone who can reach the dev container's port can therefore run it and
spend its OCR and Claude credits — bind the port to 127.0.0.1 if that matters.

It calls `runner.run` — the function `make phase1` calls — so the page cannot
exercise a different pipeline from the one it is meant to test. Notes are added
and re-linked through `annotations.py`, the same functions the product will use.

**Contract text is untrusted, so it never becomes markup.** Reducto already
emits `<b>`, `<table>` and `<signature>` into clause text, and a hostile PDF can
carry `<script>`. Markdown is rendered here, on the server, with raw HTML
disabled — tags arrive as literal text, which is also what you want when
checking what extraction produced.

**The "Original" tab draws the file with the app's own viewer code**: pdf.js
for a PDF (the page as a picture, its words as an invisible layer on top) and
docx-preview for Word, the exact builds in `vendor/`. It used to frame the file
and let the browser draw it, which showed the page but checked nothing — the
browser's viewer is not the one the product ships, and a page cannot see what
is selected inside it. The Content-Security-Policy therefore allows scripts
from this server and the page's one inline script, never inline or remote
script; see `_policy` for what each library needs beyond that.
"""

import base64
import hashlib
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Body,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from markdown_it import MarkdownIt
from sqlalchemy import select

from . import marks
from .anchoring import ORPHANED
from .annotations import (
    annotate,
    find_quote,
    for_document,
    locate,
    orphans,
    relink,
    remove,
    reply,
    set_resolved,
)
from .ask import ask, label_of
from .drafting import draft
from .models import DsAnnotation, DsDocument, DsEvent, DsVersion
from .ocr import _plain, page_text
from .parsing.base import UnsupportedFormat
from .parsing.registry import DOCX_MIME
from .redline import (
    SCANNED,
    Edit,
    RedlineError,
    changes,
    clause_edits,
    format_words,
    is_scan,
    lookalike_copy,
    make_editable,
    reasons,
    settle,
    suggest,
)
from .report import findings, report
from .runner import TOOL_ORG, mime_for, run
from .scanmd import rebuild
from .service import clauses_for
from .versions import compare, history

log = logging.getLogger(__name__)
router = APIRouter(prefix="/docstudio/dev", tags=["docstudio-dev"])

# The largest contract in the sample corpus is 1.7 MB. Generous for a test tool,
# and still stops an accidental multi-gigabyte upload being read into memory.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# The document editor, ONLYOFFICE Docs (see "editing the document itself" below).
_EDITOR = os.environ.get("ONLYOFFICE_URL", "http://localhost:8082")  # as the browser reaches it
_EDITOR_INSIDE = os.environ.get("ONLYOFFICE_INTERNAL_URL", "http://onlyoffice")  # as this server reaches it
_HERE_INSIDE = os.environ.get("DOCSTUDIO_INTERNAL_URL", "http://backend:8000")  # as it reaches this server
# A local default, as the seeded admin password is; docker-compose.override.yml uses the same.
_EDITOR_SECRET = os.environ.get("ONLYOFFICE_JWT_SECRET", "aegis-dev-onlyoffice-secret-local-only")

# `html: False` is the safeguard: raw HTML in the source is escaped rather than
# passed through. markdown-it also refuses `javascript:` links by default.
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")


def render(markdown: str) -> str:
    return _MARKDOWN.render(markdown)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> HTMLResponse:
    # The API's own policy is `default-src 'none'`, which blocks this page
    # outright; the security middleware only sets a policy when none is present,
    # so this one takes precedence for this response alone.
    # `no-store` because this page changes under the developer looking at it:
    # a cached copy is a stale script that quietly lacks whatever was just
    # added, and no reload short of a hard one replaces it.
    html, policy = _page()
    return HTMLResponse(html, headers={"Content-Security-Policy": policy, "Cache-Control": "no-store"})


@router.get("/documents")
def documents() -> list[dict]:
    """The documents these runs made, most recently touched first — what a
    file can be uploaded as a new version of."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        rows = db.scalars(
            select(DsDocument)
            .where(DsDocument.org_id == TOOL_ORG)
            .order_by(DsDocument.updated_at.desc())
            .limit(50)
        ).all()
        out = []
        for document in rows:
            current = db.get(DsVersion, document.current_version_id) if document.current_version_id else None
            out.append(
                {"id": document.id, "title": document.title, "version": current.version_number if current else None}
            )
        return out
    finally:
        db.close()


@router.get("/documents/{document_id}")
def open_document(document_id: str) -> dict:
    """One already-read document, in the shape a run returns.

    Without this the page shows a document only in the seconds after running
    it, so looking at one read yesterday meant uploading it again. Nothing is
    re-read here: the stored version is what comes back.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        return _opened(db, _document(db, document_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        db.close()


def _opened(db, document: DsDocument) -> dict:
    """Everything the page shows for a document's latest version."""
    version = db.get(DsVersion, document.current_version_id)
    clauses = clauses_for(db, version.id)
    return {
        "name": version.filename or document.title or "document",
        "how": f"{version.parser_name} v{version.parser_version} — version {version.version_number}",
        "clauses": len(clauses),
        "pages": version.page_count,
        "findings": len(findings(clauses, page_count=version.page_count)) + len(version.parse_warnings or []),
        "deduplicated": False,
        "document_id": document.id,
        "structure": f"arranged when it was read (version {version.version_number})",
        "mime": version.mime_type,
        "original": bool(version.storage_key),
        "ocr_text": page_text(db, version),
        "editable": version.mime_type == DOCX_MIME,
        "scanned": is_scan(version),
        "changes": _changes(db, version),
        "converted": _converted(db, version),
        **_refreshed(db, document),
    }


def _converted(db, version: DsVersion) -> dict | None:
    """The PDF this Word document was made from, and whether its look came with
    it: what the page needs to put the two side by side."""
    if version.mime_type != DOCX_MIME:
        return None
    event = db.scalars(
        select(DsEvent)
        .where(DsEvent.document_id == version.document_id, DsEvent.event_type == "redline.made_editable")
        .order_by(DsEvent.created_at.desc())
        .limit(1)
    ).first()
    details = (event.details or {}) if event else {}
    source = db.get(DsVersion, details["from_version"]) if details.get("from_version") else None
    if source is None or not source.storage_key or source.mime_type != "application/pdf":
        return None  # only a PDF has a look to set beside it
    return {
        "version_id": source.id,
        "version_number": source.version_number,
        "look": details.get("look", "words"),  # made before the look could be kept
        "note": details.get("note", ""),
    }


def _changes(db, version: DsVersion) -> list[dict]:
    """The tracked changes in a Word version, with the reason for each one the
    AI made. Nothing for any other kind of file: only Word carries them."""
    if version.mime_type != DOCX_MIME or not version.storage_key:
        return []
    from app.integrations.storage import storage_service

    try:
        found = changes(storage_service.read_bytes(version.storage_key))
    except RedlineError:
        return []
    why = reasons(db, version.document_id)
    return [
        {
            "ids": change.ids,
            "kind": change.kind,
            "author": change.author,
            "date": change.date,
            "deleted": change.deleted,
            "inserted": change.inserted,
            "before": change.before,
            "after": change.after,
            "why": next((why[f"{wid}|{change.date}"] for wid in change.ids if f"{wid}|{change.date}" in why), ""),
        }
        for change in found
    ]


@router.post("/run")
def run_file(
    file: UploadFile = File(...), ai: bool = Form(True), version_of: str = Form("")
) -> dict:
    """Run Phase 1 on the uploaded file and return what it produced.

    Synchronous on purpose: FastAPI runs it on a worker thread, so a long OCR
    call or a long document's arrangement does not stall the event loop.
    """
    name = Path(file.filename or "").name  # never trust a client-supplied path
    if not name:
        raise HTTPException(400, "The upload has no file name.")
    if mime_for(name) is None:
        raise HTTPException(422, f"Cannot read {Path(name).suffix or 'that file'}: use .pdf, .docx or .txt.")
    content = _read_capped(file)
    if not content:
        raise HTTPException(422, "The file is empty.")

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        output = run(db, content=content, name=name, ai=ai, version_of=version_of or None)
        notes = _notes(db, output.result.document_id)
        # Read before the session closes; the response is built after it.
        version = db.get(DsVersion, output.result.version_id)
        original, mime, scanned = bool(version.storage_key), version.mime_type, is_scan(version)
        ocr_text = page_text(db, version)
        comments = _comments(db, version)
        changes_now = _changes(db, version)
    except (UnsupportedFormat, LookupError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        # A test page is for finding out what went wrong, and this route is
        # never mounted in production, so the real error is the useful answer.
        log.exception("docstudio dev run failed for %s", name)
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
    finally:
        db.close()

    return {
        "name": output.name,
        "how": output.how,
        "clauses": output.clause_count,
        "pages": output.page_count,
        "findings": output.finding_count,
        "deduplicated": output.result.deduplicated,
        "document_id": output.result.document_id,
        "version_id": output.result.version_id,
        "mime": mime,
        "original": original,
        "ocr_text": ocr_text,
        "comments": comments,
        "editable": mime == DOCX_MIME,
        "scanned": scanned,
        "changes": changes_now,
        "report": output.report_md,
        "report_html": render(output.report_md),
        "structure": output.structure,
        **notes,
    }


@router.get("/versions/{version_id}/file", include_in_schema=False)
def version_file(version_id: str) -> Response:
    """The bytes a version was read from, for the page's Original tab.

    An attachment, like the product route: the page fetches the bytes and its
    viewer draws them, so the browser itself never renders an uploaded file in
    this origin.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        if not version.storage_key:
            raise HTTPException(404, "This version was read before files were kept. Run it again.")
        key, mime, name = version.storage_key, version.mime_type, version.filename
    finally:
        db.close()

    from app.integrations.storage import storage_service

    try:
        content = storage_service.read_bytes(key)
    except Exception as exc:
        log.exception("docstudio dev could not read %s", key)
        raise HTTPException(404, "The stored file could not be read.") from exc

    return _attachment(content, mime, name)


def _attachment(content: bytes, mime: str, name: str | None, **headers) -> Response:
    # A quote or control character here would corrupt the header for the client.
    safe = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", name or "")[:100].strip() or "document"
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff",
                 **headers},
    )


_VENDOR = Path(__file__).parent / "vendor"
# The only files the vendor route serves, by exact name — a name from the
# request is looked up here, never joined onto a path.
_VENDOR_FILES = ("pdf.min.mjs", "pdf.worker.min.mjs", "jszip.min.js", "docx-preview.min.js")


@router.get("/vendor/{name}", include_in_schema=False)
def vendor(name: str) -> FileResponse:
    """The viewer's libraries, from this server: the page loads nothing remote."""
    if name not in _VENDOR_FILES:
        raise HTTPException(404, "No such file.")
    return FileResponse(
        _VENDOR / name,
        media_type="text/javascript",
        headers={
            # Revalidated each time (a 304 when unchanged), so a library bumped
            # to match the app is never shadowed by a cached older one.
            "Cache-Control": "no-cache",
            # This response's policy is the policy of pdf.js's worker, which
            # parses the PDF. It needs nothing from anywhere; WebAssembly is
            # allowed because pdf.js decodes some scanned-image formats with it.
            "Content-Security-Policy": "default-src 'none'; script-src 'wasm-unsafe-eval'",
        },
    )


@router.post("/ask")
def ask_document(version_id: str = Form(...), question: str = Form(...)) -> dict:
    """Answer a question from the version on screen, every point cited.

    The version the page shows, not the document's latest: a citation has to
    land on the page being looked at.
    """
    question = question.strip()
    if not question:
        raise HTTPException(422, "Type a question first.")
    if len(question) > 2000:
        raise HTTPException(422, "Keep the question under 2,000 characters.")
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        try:
            return ask(db, version, question)
        except LookupError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            log.exception("docstudio dev ask failed")
            raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
    finally:
        db.close()


@router.post("/comments")
def add_comment(
    version_id: str = Form(...),
    quote: str = Form(...),
    body: str = Form(...),
    prefix: str = Form(""),
    suffix: str = Form(""),
) -> dict:
    """A comment on words selected in the viewer, the way Word takes one.

    `prefix` and `suffix` are the text either side of the selection on the
    page: they pick the right place when the same words appear more than once.
    """
    body = body.strip()
    if not body:
        raise HTTPException(422, "Write the comment first.")
    if len(body) > 4000:
        raise HTTPException(422, "Keep the comment under 4,000 characters.")
    if len(quote) > 5000:
        raise HTTPException(422, "Select fewer words: a comment sits on a passage, not a whole document.")
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        try:
            document = _document(db, version.document_id)
            if document.current_version_id != version.id:
                raise LookupError(
                    "This page shows an older version, and comments go on the latest. "
                    "Open the document from the list to comment on it."
                )
            start, end = locate(version.flat_text or "", quote, prefix=prefix[-400:], suffix=suffix[:400])
            annotate(db, version_id=version.id, start=start, end=end, body=body, kind="comment")
            db.commit()
            return _refreshed(db, document)
        except (LookupError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
    finally:
        db.close()


def _thread_action(annotation_id: str, act) -> dict:
    """Load a comment of this tool's scope, act on it, and send the page back."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        note = db.get(DsAnnotation, annotation_id)
        if note is None or note.org_id != TOOL_ORG:
            raise HTTPException(404, "No such comment.")
        document = _document(db, note.document_id)
        act(db, note)
        db.commit()
        return _refreshed(db, document)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        db.close()


@router.post("/comments/{annotation_id}/replies")
def reply_to_comment(annotation_id: str, body: str = Form(...)) -> dict:
    """A reply in a comment's thread, as Word's Reply box adds one."""
    body = body.strip()
    if not body:
        raise HTTPException(422, "Write the reply first.")
    if len(body) > 4000:
        raise HTTPException(422, "Keep the reply under 4,000 characters.")
    return _thread_action(annotation_id, lambda db, note: reply(db, note.id, body=body))


@router.post("/comments/{annotation_id}/resolve")
def resolve_comment(annotation_id: str, resolved: bool = Form(True)) -> dict:
    """Resolve a thread, or reopen it with `resolved=false`, as Word's ✓ does."""
    return _thread_action(annotation_id, lambda db, note: set_resolved(db, note.id, resolved))


@router.delete("/comments/{annotation_id}")
def delete_comment(annotation_id: str) -> dict:
    """Delete a comment with its thread, or one reply, as Word's Delete does.
    The deletion is recorded."""
    return _thread_action(annotation_id, lambda db, note: remove(db, note.id))


def _redline(version_id: str, act) -> dict:
    """Load the version shown, act on it, and send the document's new latest
    version back. Only the latest can be changed: an edit on an older one would
    fork the document's history."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        try:
            document = _document(db, version.document_id)
            if document.current_version_id != version.id:
                raise LookupError("This page shows an older version. Open the document from the list to change it.")
            extra = act(db, version) or {}
            db.commit()
            db.refresh(document)
            return {**_opened(db, document), **extra}
        except (LookupError, RedlineError) as exc:
            db.rollback()
            raise HTTPException(422, str(exc)) from exc
    finally:
        db.close()


@router.post("/redline/suggest")
def suggest_edit(
    version_id: str = Form(...),
    quote: str = Form(...),
    replacement: str = Form(""),
    prefix: str = Form(""),
    suffix: str = Form(""),
) -> dict:
    """Words selected on the page, replaced (or deleted, with no replacement) as a
    tracked change by "You" — the next version of the document."""
    if not quote.strip():
        raise HTTPException(422, "Select the words to change first.")
    if len(quote) > 5000 or len(replacement) > 10000:
        raise HTTPException(422, "Suggest a change to a passage, not a whole document.")
    edit = Edit(find=quote, replace=replacement, before=prefix[-400:], after=suffix[:400])
    return _redline(version_id, lambda db, version: suggest(db, version, [edit], author="You") and None)


@router.post("/redline/resolve")
def resolve_changes(
    version_id: str = Form(...), ids: str = Form(""), accept: bool = Form(True), everything: bool = Form(False)
) -> dict:
    """Accept or reject tracked changes by their ids (comma-separated), or all of
    them — anyone's, including the other side's."""
    chosen = None if everything else [i for i in ids.split(",") if i.strip()]
    if chosen is not None and not chosen:
        raise HTTPException(422, "Choose a change to accept or reject.")
    return _redline(version_id, lambda db, version: settle(db, version, chosen, accept=accept) and None)


@router.post("/redline/draft")
def draft_edits(version_id: str = Form(...), instruction: str = Form(...)) -> dict:
    """The AI's edits for an instruction, as tracked changes by "AEGIS AI"."""
    instruction = instruction.strip()
    if not instruction:
        raise HTTPException(422, "Say what should change first.")
    if len(instruction) > 2000:
        raise HTTPException(422, "Keep the instruction under 2,000 characters.")

    def act(db, version):
        drafted = draft(db, version, instruction)
        return {"drafted": {k: drafted[k] for k in ("summary", "placed", "skipped", "by")}}

    return _redline(version_id, act)


@router.post("/redline/editable")
def editable_copy(version_id: str = Form(...)) -> dict:
    """A typed PDF or text file as a Word document — its next version — so it
    can be redlined. A scan is refused: it changes by amendment."""
    return _redline(version_id, lambda db, version: make_editable(db, version) and None)


@router.post("/notes")
def add_note(
    document_id: str = Form(...),
    quote: str = Form(...),
    body: str = Form(...),
    kind: str = Form("comment"),
    version_id: str = Form(""),
) -> dict:
    """Attach a note to the given words of the document's current version.

    `version_id` is the version the page is showing. Re-uploading an older file
    shows that older version, and a note typed against it must not land
    silently on a different, newer text — so it is refused, saying why.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        document = _document(db, document_id)
        version = db.get(DsVersion, document.current_version_id)
        if version_id and version_id != version.id:
            shown = db.get(DsVersion, version_id)
            raise LookupError(
                f"This page shows version {shown.version_number if shown else '?'}, but notes go on "
                f"the latest version ({version.version_number}). Upload the latest file to add notes."
            )
        start, end = find_quote(version.flat_text or "", quote)
        annotate(db, version_id=version.id, start=start, end=end, body=body.strip(), kind=kind)
        db.commit()
        return _refreshed(db, document)
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        db.close()


@router.post("/notes/{annotation_id}/relink")
def relink_note(annotation_id: str, quote: str = Form(...)) -> dict:
    """Attach a lost note to new words in the document's current version."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        note = db.get(DsAnnotation, annotation_id)
        if note is None or note.org_id != TOOL_ORG:
            raise LookupError("No such note.")
        document = _document(db, note.document_id)
        version = db.get(DsVersion, document.current_version_id)
        start, end = find_quote(version.flat_text or "", quote)
        relink(db, annotation_id, start=start, end=end)
        db.commit()
        return _refreshed(db, document)
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        db.close()


def _document(db, document_id: str) -> DsDocument:
    document = db.get(DsDocument, document_id)
    if document is None or document.org_id != TOOL_ORG or not document.current_version_id:
        raise LookupError("No such document — run a file first.")
    return document


def _notes(db, document_id: str) -> dict:
    return {
        "notes": len(for_document(db, document_id)),
        "lost": [
            {"id": note.id, "body": note.body or "", "quote": note.anchor_quote_exact or ""}
            for note in orphans(db, document_id)
        ],
    }


def _refreshed(db, document: DsDocument) -> dict:
    markdown = report(db, document.current_version_id)
    return {
        "version_id": document.current_version_id,
        "report": markdown,
        "report_html": render(markdown),
        "comments": _comments(db, db.get(DsVersion, document.current_version_id)),
        **_notes(db, document.id),
    }


def _comments(db, version: DsVersion) -> list[dict]:
    """The annotations on this version, in reading order, for the margin.

    With the words they are on and the text either side, which is how the page
    finds them again in what it drew — the same three things the anchor keeps.
    """
    clauses = clauses_for(db, version.id)
    by_id = {clause.clause_id: clause for clause in clauses}
    rows = db.scalars(
        select(DsAnnotation)
        .where(
            DsAnnotation.version_id == version.id,
            DsAnnotation.anchor_state != ORPHANED,
            DsAnnotation.parent_annotation_id.is_(None),
        )
        .order_by(DsAnnotation.anchor_start)
    ).all()
    # Each thread's replies, oldest first, in one query.
    replies: dict[str, list[dict]] = {}
    if rows:
        for answer in db.scalars(
            select(DsAnnotation)
            .where(DsAnnotation.parent_annotation_id.in_([note.id for note in rows]))
            .order_by(DsAnnotation.created_at)
        ):
            replies.setdefault(answer.parent_annotation_id, []).append(
                {"id": answer.id, "body": answer.body or "", "created_at": _iso(answer.created_at)}
            )
    return [
        {
            "id": note.id,
            "kind": note.kind,
            "body": note.body or "",
            "quote": note.anchor_quote_exact or "",
            "prefix": note.anchor_quote_prefix or "",
            "suffix": note.anchor_quote_suffix or "",
            "label": label_of(by_id[note.anchor_clause_id], by_id) if note.anchor_clause_id in by_id else "",
            "moved": note.anchor_state == "moved",
            "created_at": _iso(note.created_at),
            "resolved": note.status == "resolved",
            "resolved_at": _iso(note.resolved_at),
            "status": note.status,
            "proposed_text": note.proposed_text,
            "author": note.author_name or ("AEGIS AI" if note.author_kind == "ai" else "You"),
            "replies": replies.get(note.id, []),
        }
        for note in rows
    ]


def _iso(moment) -> str | None:
    return moment.isoformat() if moment else None


def _read_capped(upload: UploadFile) -> bytes:
    # ponytail: the multipart body is fully received before this runs, so the
    # cap bounds what is processed, not what is uploaded. Enough for a dev-only
    # tool; stream the body if this ever serves anyone else.
    chunks, size = [], 0
    while chunk := upload.file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"The file is over the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)



# The page itself lives in devpage/ as ordinary files, so its script is written
# and checked as JavaScript rather than escaped inside a Python string — where
# one missing backslash once broke the whole page without a word.
_ASSETS = Path(__file__).parent / "devpage"


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


def _sha256(source: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(source.encode()).digest()).decode()


def _policy(script: str) -> str:
    """Scripts: this page's one inline script by hash, and the viewer libraries
    in vendor/ ('self') — never inline or remote script, never eval. What else
    the viewer needs, and why:

    * worker-src — pdf.js parses the PDF in a worker, loaded from vendor/.
    * style-src — docx-preview writes the Word file's own stylesheet into the
      page as it draws, so it cannot be hashed ahead of time. Styles only: a
      stylesheet cannot run code, and with images, fonts and fetches limited to
      this server it has nowhere to send anything.
    * img, font — a Word file's own images and fonts, as blob: and data: URLs.

    No forms, no framing of this page, nothing fetched from anywhere else.
    """
    return "; ".join(
        [
            "default-src 'none'",
            # The document editor (ONLYOFFICE): its loader script, and the frame it draws in.
            f"script-src 'self' '{_sha256(script)}' {_EDITOR}",
            f"frame-src {_EDITOR}",
            "worker-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' blob: data:",
            "font-src 'self' blob: data:",
            "connect-src 'self'",
            "form-action 'none'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
        ]
    )


def _page() -> tuple[str, str]:
    """The page and its policy, read fresh on every request: uvicorn reloads on
    .py changes only, and a stale script after an edit is exactly the confusion
    the no-store header exists to prevent."""
    style, script, body = _asset("page.css"), _asset("page.js"), _asset("body.html")
    html = (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Docstudio \u00b7 dev</title>\n"
        f"<style>{style}</style>\n</head>\n<body>{body}<script>{script}</script>\n</body>\n</html>\n"
    )
    return html, _policy(script)


# What the page serves, for the tests to hold it to.
_STYLE, _SCRIPT = _asset("page.css"), _asset("page.js")
_CSP = _policy(_SCRIPT)


# --- marks on a PDF, formatting in Word, clause editing ------------------------------------
# A PDF cannot take typing — its words are painted in place — so a change to one
# is a mark beside it (marks.py). A Word file takes the change itself, tracked.


def _selected(version: DsVersion, quote: str, prefix: str, suffix: str) -> tuple[int, int]:
    if not quote.strip():
        raise LookupError("Select the words first.")
    if len(quote) > 5000:
        raise LookupError("Select fewer words: a change sits on a passage, not a whole document.")
    return locate(version.flat_text or "", quote, prefix=prefix[-400:], suffix=suffix[:400])


@router.post("/marks")
def add_mark(
    version_id: str = Form(...),
    quote: str = Form(...),
    replacement: str = Form(""),
    note: str = Form(""),
    prefix: str = Form(""),
    suffix: str = Form(""),
) -> dict:
    """Selected words on a PDF, suggested for deletion (no replacement) or
    replacement, as a mark beside the page. The PDF does not change."""
    if len(replacement) > 10000 or len(note) > 4000:
        raise HTTPException(422, "Suggest a change to a passage, not a whole document.")

    def act(db, version):
        _selected(version, quote, prefix, suffix)
        placed, refused = marks.add(db, version, [Edit(quote, replacement, prefix[-400:], suffix[:400], note.strip())],
                                    author="You")
        if not placed:
            raise LookupError(refused[0][1])

    return _redline(version_id, act)


@router.post("/marks/{annotation_id}/status")
def mark_status(annotation_id: str, status: str = Form(...)) -> dict:
    """Agree to a suggested change, reject it, or reopen it."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        mark = db.get(DsAnnotation, annotation_id)
        if mark is None or mark.org_id != TOOL_ORG:
            raise HTTPException(404, "No such suggested change.")
        try:
            marks.set_status(db, annotation_id, status)
        except (LookupError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        db.commit()
        return _refreshed(db, _document(db, mark.document_id))
    finally:
        db.close()


@router.post("/highlights")
def add_highlight(version_id: str = Form(...), quote: str = Form(...), prefix: str = Form(""),
                  suffix: str = Form("")) -> dict:
    """Highlight selected words: a mark beside a PDF, a tracked highlight in a Word file."""

    def act(db, version):
        if version.mime_type == DOCX_MIME:
            format_words(db, version, Edit(quote, "", prefix[-400:], suffix[:400]), "highlight", author="You")
        else:
            start, end = _selected(version, quote, prefix, suffix)
            marks.highlight(db, version, start, end, author="You")

    return _redline(version_id, act)


@router.post("/redline/format")
def format_selection(version_id: str = Form(...), quote: str = Form(...), style: str = Form(...),
                     prefix: str = Form(""), suffix: str = Form("")) -> dict:
    """Bold, italic or underline on selected words of a Word file, as a tracked
    formatting change — Word's buttons with Track Changes on."""
    if not quote.strip():
        raise HTTPException(422, "Select the words first.")
    return _redline(version_id, lambda db, version: format_words(
        db, version, Edit(quote, "", prefix[-400:], suffix[:400]), style, author="You") and None)


@router.post("/clauses/at")
def clause_at(version_id: str = Form(...), quote: str = Form(...), prefix: str = Form(""),
              suffix: str = Form("")) -> dict:
    """The clause holding the selected words, to edit it as text."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        try:
            start, _ = _selected(version, quote, prefix, suffix)
        except LookupError as exc:
            raise HTTPException(422, str(exc)) from exc
        clauses = clauses_for(db, version.id)
        clause = next((c for c in clauses if c.char_start <= start < c.char_end), None)
        if clause is None:
            raise HTTPException(422, "Those words are not inside a clause.")
        if clause.clause_type == "table":
            raise HTTPException(422, "A table is edited cell by cell: select the words in it and suggest an edit.")
        by_id = {c.clause_id: c for c in clauses}
        return {"clause_id": clause.clause_id, "label": label_of(clause, by_id), "text": _plain(clause.text or "")}
    finally:
        db.close()


@router.post("/clauses/edit")
def edit_clause(version_id: str = Form(...), clause_id: str = Form(...), text: str = Form(...)) -> dict:
    """A clause retyped: the words that changed become tracked changes in a Word
    file, or marks beside a PDF — never a silent rewrite of the whole clause."""
    if len(text) > 20000:
        raise HTTPException(422, "A clause edit is limited to 20,000 characters.")

    def act(db, version):
        clause = next((c for c in clauses_for(db, version.id) if c.clause_id == clause_id), None)
        if clause is None:
            raise LookupError("That clause is not in this version any more. Open the document again.")
        edits = clause_edits(_plain(clause.text or ""), text)
        if not edits:
            raise LookupError("Nothing was changed.")
        if version.mime_type == DOCX_MIME:
            _, applied = suggest(db, version, edits, author="You")
            placed, refused = len(applied.edits), [why for _, why in applied.refused]
        else:
            done, missed = marks.add(db, version, edits, author="You")
            if not done:
                raise LookupError(missed[0][1])
            placed, refused = len(done), [why for _, why in missed]
        return {"edited": {"placed": placed, "refused": refused}}

    return _redline(version_id, act)


# --- what becomes of a PDF's agreed marks ----------------------------------------------------


@router.get("/versions/{version_id}/marked-up", include_in_schema=False)
def marked_up_pdf(version_id: str) -> Response:
    """The original PDF with its marks as standard PDF annotations, the original
    bytes kept at its start."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        try:
            content, placed = marks.marked_up(db, version)
        except (LookupError, RedlineError) as exc:
            raise HTTPException(422, str(exc)) from exc
        stem = (version.filename or "document").rsplit(".", 1)[0]
        return _attachment(content, "application/pdf", f"{stem} (marked up).pdf",
                           **{"X-Marks-On-Words": str(placed["on_words"]), "X-Marks-As-Notes": str(placed["as_notes"])})
    finally:
        db.close()


@router.get("/versions/{version_id}/reading", include_in_schema=False)
def scan_reading(version_id: str) -> Response:
    """What was read on a scan, as the markdown the Word version is built from.

    The record of the machine's reading: it can be read, corrected by hand and
    built again, which a Word file cannot show.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        if not is_scan(version):
            raise HTTPException(422, "This document is not a scan; its own text is what it says.")
        try:
            _, said, _ = rebuild(db, version, _bytes_of(version))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        stem = (version.filename or "document").rsplit(".", 1)[0]
        return _attachment(said.encode(), "text/markdown; charset=utf-8", f"{stem} (what was read).md")
    finally:
        db.close()


def _bytes_of(version) -> bytes:
    from app.integrations.storage import storage_service

    if not version.storage_key:
        raise HTTPException(422, "The file itself was not kept for this version. Add it again first.")
    return storage_service.read_bytes(version.storage_key)


@router.get("/versions/{version_id}/word-lookalike", include_in_schema=False)
def lookalike_docx(version_id: str, request: Request) -> Response:
    """A Word copy that looks like the PDF — every line in its own box, so it is
    for sending, not for editing. `marks.carry` makes the editable one."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        if not (version.filename or "").lower().endswith(".pdf"):
            raise HTTPException(422, "This is already a Word file.")
        if is_scan(version):
            raise HTTPException(422, SCANNED)
        stem = (version.filename or "document").rsplit(".", 1)[0]
        try:
            content = lookalike_copy(
                stem, key=version.id,
                fetch=_HERE_INSIDE + request.url_for("version_file", version_id=version.id).path,
                secret=_EDITOR_SECRET, editor=_EDITOR_INSIDE)
        except RedlineError as exc:
            raise HTTPException(422, str(exc)) from exc
        return _attachment(content, DOCX_MIME, f"{stem} (looks the same).docx")
    finally:
        db.close()


@router.post("/marks/carry")
def carry_marks(version_id: str = Form(...)) -> dict:
    """A Word version of the typed PDF with the agreed marks as tracked changes."""
    return _redline(version_id, lambda db, version: {"carried": marks.carry(db, version)})


@router.post("/marks/patch")
def patch_pdf(version_id: str = Form(...)) -> dict:
    """The agreed changes that fit, written on the PDF's page — checked, or refused."""
    return _redline(version_id, lambda db, version: {"patched": marks.patch(db, version)})


@router.get("/versions/{version_id}/amendment", include_in_schema=False)
def amendment_docx(version_id: str) -> Response:
    """The agreed changes as a separate amendment document."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        document = _document(db, version.document_id)
        title = version.filename or document.title or "the agreement"
        try:
            content = marks.amendment(db, version, title.rsplit(".", 1)[0])
        except LookupError as exc:
            raise HTTPException(422, str(exc)) from exc
        db.commit()
        return _attachment(content, DOCX_MIME, f"{title.rsplit('.', 1)[0]} - Amendment.docx")
    finally:
        db.close()


# --- history ------------------------------------------------------------------------------


@router.get("/documents/{document_id}/history")
def document_history(document_id: str) -> dict:
    """Every version, newest first, with what made it."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        try:
            _document(db, document_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"versions": history(db, document_id)}
    finally:
        db.close()


@router.get("/compare")
def compare_versions(older: str, newer: str) -> dict:
    """What words changed between two versions of one document."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        a, b = db.get(DsVersion, older), db.get(DsVersion, newer)
        if a is None or b is None or TOOL_ORG not in (a.org_id, b.org_id) or a.org_id != b.org_id:
            raise HTTPException(404, "No such version.")
        if a.document_id != b.document_id:
            raise HTTPException(422, "Compare two versions of the same document.")
        return {"older": a.version_number, "newer": b.version_number, **compare(db, older, newer)}
    finally:
        db.close()


# --- editing the document itself, in ONLYOFFICE --------------------------------------------
# The Original tab's "Edit document" opens the file in ONLYOFFICE Docs, which
# draws a Word file or a PDF with its own fonts, sizes and positions and edits
# it as a word processor does. Saving hands the edited file back here, where it
# becomes the document's next version like any other: the original stays in
# the history, clause ids carry over, every note looks for its words again.



# --- finding words inside the editor ---------------------------------------------------------
# The free editor has no way in from outside — that is its Developer Edition —
# but a plugin runs inside it, and a plugin can fetch. So a citation or a change
# clicked on the page is left here, and the plugin asks for it and selects those
# words in the document.
#
# Kept by version, because more than one editor can be open at once — two tabs,
# two people, a document and the one before it — and each plugin asks only for
# the words meant for the document it holds. One queue for all of them sent a
# citation from one contract to whichever editor happened to ask first.

_FIND: dict[str, dict] = {}
_PLUGIN_GUID = "asc.{6F2A1C54-6E8B-4A1F-9D2C-3B7E5A904F11}"


@router.get("/editor/health", include_in_schema=False)
def editor_health() -> dict:
    """Is the document service answering? Asked while an editor that lost its
    connection waits to be opened again."""
    import httpx

    try:
        answer = httpx.get(f"{_EDITOR_INSIDE}/healthcheck", timeout=5)
        return {"up": answer.status_code == 200 and "true" in answer.text.lower()}
    except httpx.HTTPError:
        return {"up": False}


_WHY: dict[str, str] = {}


@router.post("/editor/save-now")
def editor_save_now(version_id: str = Form(...), why: str = Form("")) -> dict:
    """Ask the editor to write out what is open in it, now.

    The page's own redlining — the AI's suggestions, accepting, rejecting —
    changes the file, and the editor is holding it. So the editor saves first
    (it calls back and that becomes a version), the change is made on what it
    saved, and the editor is opened again on the result. Without this, work
    done in the editor would be overwritten by a change made underneath it.
    """
    import httpx
    import jwt

    if why in ("accepted", "rejected"):
        # The editor saves a moment later, through its own callback; what the
        # version is for is known here and nowhere else.
        _WHY[version_id] = why
    payload = {"c": "forcesave", "key": version_id}
    try:
        answer = httpx.post(f"{_EDITOR_INSIDE}/coauthoring/CommandService.ashx",
                            json={**payload, "token": jwt.encode(payload, _EDITOR_SECRET, algorithm="HS256")},
                            timeout=60)
        answer.raise_for_status()
        body = answer.json()
    except httpx.HTTPError as exc:
        raise HTTPException(422, f"The document editor could not be reached to save: {exc}") from exc
    # 0 saved, 4 nothing to save — both mean the file on the server is current.
    error = int(body.get("error", -1))
    if error not in (0, 4):
        raise HTTPException(422, f"The editor would not save what is open ({body}).")
    return {"saved": error == 0, "nothing_to_save": error == 4}


@router.post("/editor/find")
def editor_find(version_id: str = Form(...), quote: str = Form(""), action: str = Form("find")) -> dict:
    """What the editor holding this version should do next: find these words,
    or accept or reject every tracked change in it.

    Accepting inside the editor is what the editor's own Collaboration tab
    does — it happens at once and in front of the person doing it. Accepting on
    the server would mean closing the editor, changing the file and opening it
    again, which looks like the document reloading for no reason.
    """
    if action not in ("find", "accept", "reject"):
        raise HTTPException(422, "The editor is asked to find, accept or reject.")
    found = _FIND.setdefault(version_id, {"id": 0, "text": "", "action": "find"})
    found.update(id=found["id"] + 1, text=quote.strip()[:400], action=action)
    if len(_FIND) > 50:      # a dev tool's worth of open editors, no more
        for old_key in list(_FIND)[:-50]:
            _FIND.pop(old_key, None)
    return dict(found)


@router.get("/editor/plugin/command", include_in_schema=False)
def editor_command(key: str = "", since: int = 0) -> Response:
    """What the plugin holding this version should look for, if it has not
    looked for it already."""
    found = _FIND.get(key) or {"id": 0, "text": "", "action": "find"}
    return _for_plugin(dict(found) if found["id"] > since
                       else {"id": found["id"], "text": "", "action": "find"})


def _for_plugin(body: dict) -> Response:
    # The plugin's page belongs to the editor, so these two answers are read
    # from its origin and no other.
    return JSONResponse(body, headers={"Access-Control-Allow-Origin": _EDITOR})


@router.get("/editor/plugin/log", include_in_schema=False)
def editor_plugin_log(m: str = "") -> Response:
    """The plugin runs inside the editor, where its console is out of reach."""
    log.info("docstudio editor plugin: %s", m[:300])
    return _for_plugin({"ok": True})


@router.get("/editor/config")
def editor_config(version_id: str, request: Request) -> dict:
    """What the page hands ONLYOFFICE to open this version, signed so the editor
    trusts it. Only the latest version is edited, and never a scan: it is the
    signed contract, changed by amendment."""
    import jwt

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG:
            raise HTTPException(404, "No such version — run a file first.")
        document = _document(db, version.document_id)
        if document.current_version_id != version.id:
            raise HTTPException(422, "Only the latest version is edited. Open the document from the list.")
        if not version.storage_key:
            raise HTTPException(422, "The file itself was not kept for this version. Add it again first.")
        if is_scan(version):
            raise HTTPException(422, "This is a scan of a signed document: it is not edited. "
                                     "Changes to it go in an amendment (Changes tab).")
        if version.mime_type not in (DOCX_MIME, "application/pdf"):
            raise HTTPException(422, "Only Word files and PDFs open in the editor.")
        word = version.mime_type == DOCX_MIME
        config = {
            "document": {
                "fileType": "docx" if word else "pdf",
                "key": version.id,  # a new version is a new key, so the editor never serves a stale copy
                "title": version.filename or document.title or "document",
                "url": _HERE_INSIDE + request.url_for("version_file", version_id=version.id).path,
                "permissions": {"edit": True, "review": word, "comment": True, "download": True, "print": True},
            },
            "documentType": "word" if word else "pdf",
            "editorConfig": {
                "mode": "edit",
                "lang": "en",
                "callbackUrl": f"{_HERE_INSIDE}{request.url_for('editor_saved').path}"
                               f"?document_id={version.document_id}&version_id={version.id}",
                "user": {"id": "you", "name": "You"},
                # Save writes a version at once; in a Word file every edit is a tracked change by You.
                "customization": {"forcesave": True, "compactHeader": True,
                                  **({"review": {"trackChanges": True}} if word else {})},
                # Started with the document, hidden: it is how the page finds words in here.
                "plugins": {"autostart": [_PLUGIN_GUID],
                            # Which document this plugin holds, so the page can
                            # ask this editor — and only this one — to find words.
                            "options": {_PLUGIN_GUID: {
                                "base": str(request.url_for("editor_command")).rsplit("/", 1)[0] + "/",
                                "key": version.id}}},
            },
            "width": "100%",
            "height": "100%",
            "type": "desktop",
        }
        config["token"] = jwt.encode(config, _EDITOR_SECRET, algorithm="HS256")
        return {"server": _EDITOR, "config": config}
    finally:
        db.close()


@router.post("/editor/saved", include_in_schema=False)
def editor_saved(
    document_id: str, version_id: str, body: dict = Body(...), authorization: str = Header("")
) -> dict:
    """ONLYOFFICE handing back an edited file (status 2: the editor closed with
    changes; 6: Save pressed). Believed only if signed with the shared secret,
    and fetched only from the editor itself — never from an address it names."""
    import httpx
    import jwt

    token = body.get("token") or authorization.removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(token, _EDITOR_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(403, "Not signed by the editor.") from None
    claims = claims.get("payload", claims)  # a header token wraps the body
    log.info("docstudio editor callback: status %s for %s", claims.get("status"), version_id)
    if claims.get("status") not in (2, 6):
        return {"error": 0}
    where = urlsplit(str(claims.get("url") or ""))
    source = f"{_EDITOR_INSIDE}{where.path}" + (f"?{where.query}" if where.query else "")

    from app.core.database import SessionLocal

    from .service import ingest, record_event

    db = SessionLocal()
    try:
        version = db.get(DsVersion, version_id)
        if version is None or version.org_id != TOOL_ORG or version.document_id != document_id:
            raise HTTPException(404, "No such version.")
        response = httpx.get(source, timeout=60)
        response.raise_for_status()
        content = response.content
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(422, "The edited file is larger than this page accepts.")
        if not content.startswith(b"PK" if version.mime_type == DOCX_MIME else b"%PDF"):
            raise HTTPException(422, "The editor sent back a file of another kind.")
        # The editor sends the file again when it closes, even with nothing new
        # since Save was pressed: that is not a version.
        latest = db.get(DsVersion, _document(db, document_id).current_version_id)
        if latest is not None and latest.storage_key and _same_file(content, _stored(latest)):
            return {"error": 0}
        result = ingest(db, org_id=TOOL_ORG, content=content, filename=version.filename or "document",
                        mime_type=version.mime_type, document_id=document_id)
        if not result.deduplicated:
            record_event(db, org_id=TOOL_ORG, document_id=document_id, version_id=result.version_id,
                         event_type="edited.in_editor",
                         details={"from_version": version_id,
                                  "saved": _WHY.pop(version_id, None)
                                  or ("Save pressed" if claims["status"] == 6 else "editor closed")})
        db.commit()
        return {"error": 0}
    except HTTPException:
        raise
    except Exception:
        log.exception("docstudio: saving the edited file for %s failed", version_id)
        db.rollback()
        return {"error": 1}
    finally:
        db.close()


def _stored(version: DsVersion) -> bytes:
    from app.integrations.storage import storage_service

    return storage_service.read_bytes(version.storage_key)


def _same_file(a: bytes, b: bytes) -> bool:
    """The same document, if not the same bytes: a Word file saved twice differs
    only in its zip's timestamps, so its parts are compared, not its bytes."""
    import io
    import zipfile

    if a == b:
        return True
    try:
        x, y = zipfile.ZipFile(io.BytesIO(a)), zipfile.ZipFile(io.BytesIO(b))
        return sorted(x.namelist()) == sorted(y.namelist()) and all(x.read(n) == y.read(n) for n in x.namelist())
    except zipfile.BadZipFile:
        return False
