"""Annotations: create them, keep them attached across versions, never lose one.

An annotation — a comment, citation, proposal or risk — stores three ways to
find its words again (`anchoring.capture`), taken when it is created. When a new
version of the document arrives, every annotation is looked for again with the
ladder in `anchoring.resolve`, and moved to where its words now are.

One that cannot be found is *orphaned*: it keeps what it last knew, is listed
for a person to re-link, and is tried again on every later version. It is never
deleted — annotations disappearing quietly is the failure this replaces.

Every re-anchoring records which rung found each annotation, on the event log:
that distribution is the honest measure of how well anchors survive.
"""

import html
import re
from collections import Counter

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .anchoring import ORPHANED, Anchor, ClauseRef, capture, expected_window, resolve
from .models import DsAnnotation, DsClause, DsDocument, DsEvent, DsVersion

KINDS = frozenset({"comment", "citation", "proposal", "risk", "highlight"})


def clause_refs(db: Session, version_id: str) -> list[ClauseRef]:
    rows = db.scalars(
        select(DsClause).where(DsClause.version_id == version_id).order_by(DsClause.seq)
    )
    return [ClauseRef(c.clause_id, c.text, c.char_start, c.char_end) for c in rows]


def find_quote(flat_text: str, quote: str) -> tuple[int, int]:
    """Where these exact words are in the text, whatever the line breaks.

    Refused when they are not there, or are there more than once: an
    annotation attached to "whichever came first" is attached to a guess.
    """
    words = quote.split()
    if not words:
        raise LookupError("Give the words to attach to.")
    found = list(re.finditer(r"\s+".join(map(re.escape, words)), flat_text))
    if not found:
        raise LookupError("Those words are not in this version of the document.")
    if len(found) > 1:
        raise LookupError(
            f"Those words appear {len(found)} times — add a few more so they point at one place."
        )
    return found[0].start(), found[0].end()


# The drawn document and the stored text agree on the words, not on everything
# else: OCR markup, "&amp;", curly quotes, and the line breaks pdf.js joins with
# no space. So words selected on the page are matched with all of that ignored —
# every character that is not a space, lower-cased, typography made plain — and
# mapped back to where they sit in the stored text.
_TOKEN = re.compile(
    r"</?(?:b|i|u|sup|sub|br|signature|empty|table|thead|tbody|tr|th|td)(?:\s[^<>]*=[^<>]*)?\s*/?>"
    r"|&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);|\s+|.",
    re.IGNORECASE | re.DOTALL,
)
_PLAIN = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-"})


def _folded(text: str) -> tuple[str, list[tuple[int, int]]]:
    """`text` as compared, and for each compared character the span it came from."""
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    for match in _TOKEN.finditer(text):
        token = match.group()
        if (token[0] == "<" and len(token) > 1) or token.isspace():
            continue  # markup the page never shows, or spacing it may not
        plain = html.unescape(token) if token[0] == "&" else token
        for char in plain.translate(_PLAIN).casefold():
            if not char.isspace():
                out.append(char)
                spans.append(match.span())
    return "".join(out), spans


def _agree(a: str, b: str) -> int:
    count = 0
    for x, y in zip(a, b):
        if x != y:
            break
        count += 1
    return count


def locate(flat_text: str, quote: str, *, prefix: str = "", suffix: str = "") -> tuple[int, int]:
    """Where words selected on the drawn page are in the stored text.

    The same words are often in several places — "the Services" is in half the
    clauses — so the text either side of the selection decides between them,
    compared the same way. Refused when the words are not there, or are there
    more than once with nothing around them to tell the places apart: a comment
    attached to a guess is attached to the wrong clause half the time.
    """
    haystack, spans = _folded(flat_text)
    needle, _ = _folded(quote)
    if not needle:
        raise LookupError("Select some words first.")
    hits, at = [], haystack.find(needle)
    while at != -1:
        hits.append(at)
        at = haystack.find(needle, at + 1)
    if not hits:
        raise LookupError(
            "Those words are not in the text read from this file. Struck-through (deleted) "
            "words, and words the reader could not read, cannot carry a comment."
        )
    before, after = _folded(prefix)[0][-64:][::-1], _folded(suffix)[0][:64]
    ranked = sorted(
        (
            (
                _agree(haystack[max(0, hit - 64) : hit][::-1], before)
                + _agree(haystack[hit + len(needle) : hit + len(needle) + 64], after),
                hit,
            )
            for hit in hits
        ),
        reverse=True,
    )
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        raise LookupError(
            f"Those words appear {len(hits)} times and nothing around them tells the places apart "
            "— select a few more words."
        )
    hit = ranked[0][1]
    return spans[hit][0], spans[hit + len(needle) - 1][1]


def annotate(
    db: Session,
    *,
    version_id: str,
    start: int,
    end: int,
    body: str,
    kind: str = "comment",
    author_name: str | None = None,
    author_user_id: str | None = None,
    proposed_text: str | None = None,
) -> DsAnnotation:
    """Attach an annotation to `flat_text[start:end]` of a version."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    version = db.get(DsVersion, version_id)
    if version is None:
        raise LookupError(f"no such version: {version_id}")
    text = version.flat_text or ""
    if not 0 <= start < end <= len(text):
        raise ValueError(f"the span {start}-{end} is not inside this version's text")

    anchor = capture(text, start, end, clause_refs(db, version_id))
    annotation = DsAnnotation(
        org_id=version.org_id,
        document_id=version.document_id,
        version_id=version.id,
        kind=kind,
        status="open",
        body=body,
        proposed_text=proposed_text,
        author_kind="user",
        author_user_id=author_user_id,
        author_name=author_name,
        anchor_state="ok",
        anchor_rung=None,  # placed by a person, not found by the ladder
    )
    _set_anchor(annotation, anchor)
    db.add(annotation)
    db.flush()
    _record(db, version, "annotation.created", {"annotation_id": annotation.id, "kind": kind})
    return annotation


def reanchor(db: Session, version: DsVersion) -> dict[int, int]:
    """Find every annotation of this version's document again, in this version.

    Returns how many each rung found. Called by `service.ingest` for every new
    version; an annotation already on this version is left alone.
    """
    annotations = list(
        db.scalars(
            select(DsAnnotation).where(
                DsAnnotation.document_id == version.document_id,
                or_(DsAnnotation.version_id.is_(None), DsAnnotation.version_id != version.id),
                # A reply has no words of its own to look for: it goes wherever
                # its comment goes, and would otherwise be listed as lost.
                DsAnnotation.parent_annotation_id.is_(None),
            )
        )
    )
    if not annotations:
        return {}
    text = version.flat_text or ""
    refs = clause_refs(db, version.id)
    # Each annotation's clause order in the version it was last found in: what
    # tells rung 3 and 4 where a clause that lost its identity should be now.
    order: dict[str | None, list[str]] = {}
    rungs: Counter = Counter()
    outcomes = []
    for annotation in annotations:
        if annotation.version_id not in order:
            order[annotation.version_id] = [ref.clause_id for ref in clause_refs(db, annotation.version_id)]
        near = expected_window(order[annotation.version_id], annotation.anchor_clause_id, refs)
        found = resolve(_anchor(annotation), text, refs, near=near)
        rungs[found.rung] += 1
        outcomes.append(
            {
                "annotation_id": annotation.id,
                "rung": found.rung,
                "state": found.state,
                "from_clause": annotation.anchor_clause_id,
                "to_clause": found.clause_id,
            }
        )
        annotation.anchor_state, annotation.anchor_rung = found.state, found.rung
        if found.state == ORPHANED:
            # Keep what it last knew: it is what a later version, or a person,
            # finds it again from.
            continue
        # Re-captured where it was found, so the next version is searched for
        # the words as they are now, not as they were three versions ago.
        _set_anchor(annotation, capture(text, found.start, found.end, refs))
        annotation.version_id = version.id
    _record(
        db,
        version,
        "annotations.reanchored",
        {"rungs": {str(k): v for k, v in sorted(rungs.items())}, "annotations": outcomes},
    )
    db.flush()
    return dict(rungs)


def orphans(db: Session, document_id: str) -> list[DsAnnotation]:
    """Annotations whose words could not be found — the queue a person clears."""
    return list(
        db.scalars(
            select(DsAnnotation)
            .where(
                DsAnnotation.document_id == document_id,
                DsAnnotation.anchor_state == ORPHANED,
                DsAnnotation.parent_annotation_id.is_(None),
            )
            .order_by(DsAnnotation.created_at)
        )
    )


def relink(db: Session, annotation_id: str, *, start: int, end: int) -> DsAnnotation:
    """A person attaches an annotation to new words in the current version."""
    annotation = db.get(DsAnnotation, annotation_id)
    if annotation is None:
        raise LookupError(f"no such annotation: {annotation_id}")
    document = db.get(DsDocument, annotation.document_id)
    version = db.get(DsVersion, document.current_version_id)
    text = version.flat_text or ""
    if not 0 <= start < end <= len(text):
        raise ValueError(f"the span {start}-{end} is not inside this version's text")
    was = annotation.anchor_quote_exact
    _set_anchor(annotation, capture(text, start, end, clause_refs(db, version.id)))
    annotation.version_id = version.id
    annotation.anchor_state, annotation.anchor_rung = "ok", None
    _record(
        db,
        version,
        "annotation.relinked",
        {"annotation_id": annotation.id, "was": (was or "")[:200], "now": annotation.anchor_quote_exact[:200]},
    )
    db.flush()
    return annotation


def reply(db: Session, parent_id: str, *, body: str, author_name: str | None = None) -> DsAnnotation:
    """A reply in a comment's thread, as Word keeps them.

    It carries no anchor: its place is its comment's, wherever the ladder moves
    that. A reply to a reply joins the same thread, since Word's threads are
    one level deep.
    """
    parent = db.get(DsAnnotation, parent_id)
    if parent is None:
        raise LookupError(f"no such annotation: {parent_id}")
    if parent.parent_annotation_id:
        parent = db.get(DsAnnotation, parent.parent_annotation_id)
    answer = DsAnnotation(
        org_id=parent.org_id,
        document_id=parent.document_id,
        version_id=parent.version_id,
        kind="comment",
        status="open",
        body=body,
        parent_annotation_id=parent.id,
        author_kind="user",
        author_name=author_name,
        anchor_state="ok",
    )
    db.add(answer)
    db.flush()
    _record(db, _current(db, parent), "annotation.replied", {"annotation_id": answer.id, "thread": parent.id})
    return answer


def set_resolved(db: Session, annotation_id: str, resolved: bool) -> DsAnnotation:
    """Resolve a thread, or reopen it. Its replies go with it."""
    thread = db.get(DsAnnotation, annotation_id)
    if thread is None:
        raise LookupError(f"no such annotation: {annotation_id}")
    if thread.parent_annotation_id:
        thread = db.get(DsAnnotation, thread.parent_annotation_id)
    if (thread.status == "resolved") != resolved:
        thread.status = "resolved" if resolved else "open"
        thread.resolved_at = func.now() if resolved else None
        _record(
            db,
            _current(db, thread),
            "annotation.resolved" if resolved else "annotation.reopened",
            {"annotation_id": thread.id},
        )
        db.flush()
    return thread


def remove(db: Session, annotation_id: str) -> None:
    """A person deletes their own annotation — a decision, so it is recorded.

    A comment goes with its whole thread, a reply alone, as in Word. Distinct
    from an orphan, which the ladder could not place and which is kept until
    someone re-links it: nothing here disappears without a person asking.
    """
    annotation = db.get(DsAnnotation, annotation_id)
    if annotation is None:
        raise LookupError(f"no such annotation: {annotation_id}")
    replies = list(
        db.scalars(select(DsAnnotation).where(DsAnnotation.parent_annotation_id == annotation.id))
    )
    _record(
        db,
        _current(db, annotation),
        "annotation.deleted",
        {
            "annotation_id": annotation.id,
            "kind": annotation.kind,
            "quote": (annotation.anchor_quote_exact or "")[:200],
            "body": (annotation.body or "")[:200],
            "replies": [answer.id for answer in replies],
        },
    )
    for answer in replies:
        db.delete(answer)
    db.delete(annotation)
    db.flush()


def _current(db: Session, annotation: DsAnnotation) -> DsVersion:
    """The version a thread's history is written against: the document's latest."""
    document = db.get(DsDocument, annotation.document_id)
    return db.get(DsVersion, document.current_version_id)


def for_document(db: Session, document_id: str) -> list[DsAnnotation]:
    return list(
        db.scalars(
            select(DsAnnotation)
            .where(DsAnnotation.document_id == document_id)
            .order_by(DsAnnotation.created_at)
        )
    )


def _anchor(annotation: DsAnnotation) -> Anchor:
    return Anchor(
        quote_exact=annotation.anchor_quote_exact or "",
        quote_prefix=annotation.anchor_quote_prefix or "",
        quote_suffix=annotation.anchor_quote_suffix or "",
        clause_id=annotation.anchor_clause_id,
        start=annotation.anchor_start,
        end=annotation.anchor_end,
    )


def _set_anchor(annotation: DsAnnotation, anchor: Anchor) -> None:
    annotation.anchor_clause_id = anchor.clause_id
    annotation.anchor_quote_exact = anchor.quote_exact
    annotation.anchor_quote_prefix = anchor.quote_prefix
    annotation.anchor_quote_suffix = anchor.quote_suffix
    annotation.anchor_start = anchor.start
    annotation.anchor_end = anchor.end


def _record(db: Session, version: DsVersion, event_type: str, details: dict) -> None:
    db.add(
        DsEvent(
            org_id=version.org_id,
            document_id=version.document_id,
            version_id=version.id,
            event_type=event_type,
            details=details,
        )
    )
