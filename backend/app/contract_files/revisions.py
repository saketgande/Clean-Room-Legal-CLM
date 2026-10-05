"""Counterparty revisions: what they changed in the version we sent.

When a counterparty sends a draft back, the reviewer's question is not "what
does this say" but "what did they change, and did they tell us". This module
answers it clause by clause and keeps the reviewer's decisions:

1. The version we sent and theirs are split into clauses the same way
   (`split_blocks`), and matched with the Documents clause matcher
   (`docstudio.versions.carry_ids`): an unchanged clause matches even after
   renumbering, a reworded one by similarity, the rest were added or removed.
2. Each changed clause is classed against what WE changed in the version we
   sent: they kept our wording, countered it, or put back their original.
3. From a Word file, their changes are checked against its tracked changes:
   a difference the file never marked is flagged — the edit a reviewer misses.
4. Finishing the round either agrees the text, or builds our next version:
   their text, with our wording back where we kept ours or countered.

Playbook findings and comments are looked up when the round is read, not
stored, so the auto review that runs on their version shows up as it lands.
"""

from __future__ import annotations

import difflib
import io
import re
import zipfile

from app.contract_files.blocks import split_blocks
from app.docstudio.versions import carry_ids

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# A clause number at the start of a block: "3.3", "10.", "(a)", "Article 4".
_LEADING_NUMBER = re.compile(r"^\s*(?:(?:article|section)\s+)?(?:\d+(?:\.\d+)*[.)]?|\((?:[a-z]|[ivxlcdm]+|\d+)\))\s+", re.IGNORECASE)


def norm(text: str | None) -> str:
    return " ".join((text or "").split()).lower()


def _body(text: str | None) -> str:
    """A clause without its number, normalised: Word writes automatic numbers
    into the extracted text but not into a raw read of the XML."""
    return norm(_LEADING_NUMBER.sub("", text or "", count=1))


# --- their file: what it looked like before their marked changes -------------------------

def unmarked_view(docx_bytes: bytes) -> str | None:
    """Their Word file with every tracked change rejected: the text they
    received from us, give or take any edit they made WITHOUT tracking. None
    when the file isn't Word or marks no changes at all (a clean copy), since
    then nothing can be told apart."""
    try:
        from lxml import etree

        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
            root = etree.fromstring(z.read("word/document.xml"))
    except Exception:
        return None
    marked = {f"{_W}ins", f"{_W}del", f"{_W}moveFrom", f"{_W}moveTo"}
    if not any(el.tag in marked for el in root.iter()):
        return None

    def inserted(node) -> bool:
        parent = node.getparent()
        while parent is not None:
            if parent.tag in (f"{_W}ins", f"{_W}moveTo"):
                return True
            parent = parent.getparent()
        return False

    paragraphs = []
    for p in root.iter(f"{_W}p"):
        parts = [
            t.text or ""
            for t in p.iter(f"{_W}t", f"{_W}delText")
            if not inserted(t)
        ]
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


# --- the comparison ------------------------------------------------------------------------

def word_parts(ours: str, theirs: str) -> list[list[str]]:
    """Word-level difference, adjacent runs of one kind merged:
    [["=", "The Recipient shall"], ["-", "five (5)"], ["+", "three (3)"], ...]."""
    a, b = ours.split(), theirs.split()
    out: list[list[str]] = []

    def put(op: str, words: list[str]) -> None:
        if not words:
            return
        if out and out[-1][0] == op:
            out[-1][1] += " " + " ".join(words)
        else:
            out.append([op, " ".join(words)])

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            put("=", a[i1:i2])
        else:
            put("-", a[i1:i2])
            put("+", b[j1:j2])
    return out


def _pairs(old: list[str], new: list[str]) -> dict[int, int]:
    """new index -> old index, for every new clause that continues an old one.
    Matched on the words without the clause number, so a clause renumbered by
    a deletion above it is still the same clause, unchanged."""
    strip = lambda t: _LEADING_NUMBER.sub("", t, count=1)
    carried = carry_ids([(str(i), strip(t)) for i, t in enumerate(old)], [strip(t) for t in new])
    return {j: int(i) for j, i in enumerate(carried) if i is not None}


def compare(ours_text: str, theirs_text: str, *, before_text: str | None = None,
            unmarked: str | None = None) -> list[dict]:
    """Every clause that differs between the version we sent (`ours_text`) and
    theirs, in their order. `before_text` is the version before ours, so a
    change to a clause WE had changed reads as a counter; `unmarked` is their
    file with its tracked changes rejected (see `unmarked_view`)."""
    ours = [b.text for b in split_blocks(ours_text)]
    theirs = [b.text for b in split_blocks(theirs_text)]
    to_ours = _pairs(ours, theirs)
    matched = set(to_ours.values())

    # What we changed in the version we sent: clauses new or reworded since the one before.
    ours_changed: dict[int, str | None] = {}
    if before_text is not None:
        before = [b.text for b in split_blocks(before_text)]
        back = _pairs(before, ours)
        for i, text in enumerate(ours):
            prior = before[back[i]] if i in back else None
            if prior is None or _body(prior) != _body(text):
                ours_changed[i] = prior

    seen_unmarked = _body(unmarked) if unmarked is not None else None

    def unmarked_change(kind: str, our: str | None, their: str | None) -> bool:
        if seen_unmarked is None:
            return False
        if kind == "added":  # a marked insertion is not in the unmarked view
            return _body(their) in seen_unmarked
        return _body(our) not in seen_unmarked  # our words should still be there

    rows: list[dict] = []
    for j, their in enumerate(theirs):
        i = to_ours.get(j)
        if i is None:
            rows.append({"seq": j, "kind": "added", "our_text": None, "their_text": their,
                         "parts": [["+", " ".join(their.split())]]})
            continue
        our = ours[i]
        same = _body(our) == _body(their)
        if i in ours_changed:
            prior = ours_changed[i]
            kind = "ours" if same else ("reverted" if prior is not None and _body(prior) == _body(their) else "countered")
        elif same:
            continue
        else:
            kind = "changed"
        rows.append({"seq": j, "kind": kind, "our_text": our, "their_text": their,
                     "parts": [["=", " ".join(their.split())]] if same else word_parts(our, their)})

    # A clause of ours with no partner in theirs was removed; it sits before
    # the first of their clauses that continues a later clause of ours.
    for i, our in enumerate(ours):
        if i in matched:
            continue
        after = [j for j, k in to_ours.items() if k > i]
        rows.append({"seq": min(after) if after else len(theirs), "kind": "removed",
                     "our_text": our, "their_text": None, "parts": [["-", " ".join(our.split())]],
                     "removed_at": True})
    rows.sort(key=lambda r: (r["seq"], 0 if r.get("removed_at") else 1))
    their_heads, our_heads = _headings(theirs), _headings(ours)
    for r in rows:
        removed = r.pop("removed_at", False)
        r["unmarked"] = r["kind"] != "ours" and unmarked_change(r["kind"], r["our_text"], r["their_text"])
        if removed:
            heading = our_heads[ours.index(r["our_text"])]
        else:
            heading = their_heads[r["seq"]] if r["seq"] < len(their_heads) else None
        r["label"] = label_for(r["their_text"] or r["our_text"] or "", heading)
    return rows


def _is_heading(text: str) -> bool:
    """A section heading: short, and not a sentence ("8. TERM AND TERMINATION")."""
    body = _LEADING_NUMBER.sub("", text, count=1).strip()
    return 0 < len(body) <= 70 and not re.search(r"[.;:]\s", body) and not body.endswith((".", ";", ","))


def _headings(blocks: list[str]) -> list[str | None]:
    """For each block, the section heading it sits under (its own text if it is one)."""
    out, current = [], None
    for text in blocks:
        if _is_heading(text):
            current = _LEADING_NUMBER.sub("", text, count=1).strip()
        out.append(current)
    return out


def label_for(text: str, heading: str | None = None) -> str:
    """"8.2 The Recipient's…" under "8. TERM AND TERMINATION" -> "8.2 Term and termination";
    with no heading, the clause's own opening words."""
    first = " ".join(text.split())
    number = _LEADING_NUMBER.match(first)
    if heading and number:
        return f"{number.group(0).strip()} {heading.capitalize() if heading.isupper() else heading}"[:120]
    m = re.match(r"^(\S+\s+)?([^.:;]{3,60})", first)
    return (m.group(0) if m else first[:60]).strip()


# --- the next version --------------------------------------------------------------------

def counter_text(theirs_text: str, changes: list) -> str:
    """Their text with our decisions applied: our wording back where we kept
    ours, the counter wording where we countered, theirs everywhere else.
    `changes` are the round's rows (objects with seq/kind/decision/...)."""
    theirs = [b.text for b in split_blocks(theirs_text)]
    removed_at: dict[int, list] = {}
    at: dict[int, object] = {}
    for c in changes:
        if c.kind == "removed":
            removed_at.setdefault(c.seq, []).append(c)
        else:
            at[c.seq] = c
    out: list[str] = []

    def ours_or_counter(c) -> str | None:
        if c.decision == "kept":
            return c.our_text
        if c.decision == "countered":
            return c.counter_text
        return c.their_text

    for j in range(len(theirs) + 1):
        for c in removed_at.get(j, []):
            text = ours_or_counter(c)  # accepting a removal leaves nothing
            if text:
                out.append(text)
        if j == len(theirs):
            break
        c = at.get(j)
        text = ours_or_counter(c) if c is not None else theirs[j]
        if text:
            out.append(text)
    return "\n\n".join(out)


# --- rounds in the database ----------------------------------------------------------------

def _version_text(db, version_id: str | None) -> str | None:
    from app.contract_files.models import ContractTextSnapshot, ContractVersion

    version = db.get(ContractVersion, version_id) if version_id else None
    snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version and version.text_snapshot_id else None
    return snapshot.text if snapshot else None


def _version_before(db, version) -> str | None:
    """The contract's version just before `version`: what `version` changed."""
    from sqlalchemy import select

    from app.contract_files.models import ContractVersion

    prior = db.scalar(
        select(ContractVersion)
        .where(ContractVersion.contract_id == version.contract_id,
               ContractVersion.version_number < version.version_number,
               ContractVersion.deleted_at.is_(None))
        .order_by(ContractVersion.version_number.desc())
        .limit(1)
    )
    return prior.id if prior else None


def start_round(db, *, contract, base_version_id: str, revision_version, user, file_bytes: bytes | None):
    """Work out a returned version's changes against the one we sent. A round
    still open for this contract is closed first: their newer file replaces it."""
    from sqlalchemy import func, select

    from app.contract_files.models import ContractVersion, RevisionChange, RevisionRound

    for stale in db.scalars(select(RevisionRound).where(
            RevisionRound.contract_id == contract.id, RevisionRound.status == "open")):
        stale.status, stale.outcome = "closed", "superseded"
    base = db.get(ContractVersion, base_version_id)
    unmarked = unmarked_view(file_bytes) if file_bytes else None
    rows = compare(
        _version_text(db, base_version_id) or "",
        _version_text(db, revision_version.id) or "",
        before_text=_version_text(db, _version_before(db, base)) if base else None,
        unmarked=unmarked,
    )
    number = (db.scalar(select(func.count(RevisionRound.id)).where(
        RevisionRound.contract_id == contract.id, RevisionRound.outcome != "superseded")) or 0) + 1
    rnd = RevisionRound(
        org_id=contract.org_id, contract_id=contract.id, base_version_id=base_version_id,
        revision_version_id=revision_version.id, round_number=number, tracked=unmarked is not None,
        created_by_user_id=user.id, updated_by_user_id=user.id,
    )
    db.add(rnd)
    db.flush()
    for r in rows:
        db.add(RevisionChange(
            org_id=contract.org_id, round_id=rnd.id, contract_id=contract.id, seq=r["seq"],
            label=r["label"][:120], kind=r["kind"], unmarked=r["unmarked"], our_text=r["our_text"],
            their_text=r["their_text"], parts=r["parts"], decision="agreed" if r["kind"] == "ours" else "open",
        ))
    db.flush()
    return rnd


def changes_of(db, round_id: str) -> list:
    from sqlalchemy import select

    from app.contract_files.models import RevisionChange

    return list(db.scalars(select(RevisionChange).where(RevisionChange.round_id == round_id)
                           .order_by(RevisionChange.seq, RevisionChange.created_at)))


def serialize_round(db, rnd) -> dict:
    """The round with, per change, the playbook findings quoting their wording
    and the comments that were on our wording (still there, or gone)."""
    from sqlalchemy import select

    from app.contracts.comments_models import ContractComment
    from app.playbooks.models import PlaybookDeviation

    findings = [
        (norm((d.citation or {}).get("quote") if isinstance(d.citation, dict) else ""), d)
        for d in db.scalars(select(PlaybookDeviation).where(
            PlaybookDeviation.contract_id == rnd.contract_id,
            PlaybookDeviation.status.in_(("open", "needs_review"))))
    ]
    comments = [
        (norm((c.anchor or {}).get("quote")), c)
        for c in db.scalars(select(ContractComment).where(
            ContractComment.contract_id == rnd.contract_id, ContractComment.resolved_at.is_(None),
            ContractComment.deleted_at.is_(None)))
        if isinstance(c.anchor, dict) and (c.anchor or {}).get("quote")
    ]
    changes = []
    for c in changes_of(db, rnd.id):
        ours, theirs = norm(c.our_text), norm(c.their_text)
        changes.append({
            "id": c.id, "seq": c.seq, "label": c.label, "kind": c.kind, "unmarked": c.unmarked,
            "our_text": c.our_text, "their_text": c.their_text, "parts": c.parts,
            "decision": c.decision, "counter_text": c.counter_text,
            "decided_at": c.decided_at.isoformat() if c.decided_at else None,
            "playbook": [{"id": d.id, "severity": d.severity, "issue": d.issue, "suggested_fix": d.suggested_fix}
                         for q, d in findings if q and theirs and q in theirs],
            "carried": [{"id": cm.id, "body": cm.body, "still_there": bool(theirs) and q in theirs}
                        for q, cm in comments if ours and q in ours],
        })
    open_ = sum(1 for c in changes if c["decision"] == "open")
    return {
        "id": rnd.id, "contract_id": rnd.contract_id, "round_number": rnd.round_number,
        "base_version_id": rnd.base_version_id, "revision_version_id": rnd.revision_version_id,
        "status": rnd.status, "outcome": rnd.outcome, "outcome_version_id": rnd.outcome_version_id,
        "tracked": rnd.tracked, "changes": changes, "open": open_,
        "pushed_back": sum(1 for c in changes if c["decision"] in ("kept", "countered")),
        "created_at": rnd.created_at.isoformat() if rnd.created_at else None,
    }


DECISIONS = ("open", "accepted", "kept", "countered")


def decide(change, *, decision: str, counter: str | None, user) -> None:
    from fastapi import HTTPException, status

    from app.core.database import utcnow

    if change.kind == "ours":
        raise HTTPException(status.HTTP_409_CONFLICT, "They kept our wording here; there is nothing to decide.")
    if decision not in DECISIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Decide accepted, kept or countered.")
    if decision == "countered" and not (counter or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Write the wording you are proposing instead.")
    change.decision = decision
    change.counter_text = counter.strip() if decision == "countered" else None
    change.decided_by_user_id = None if decision == "open" else user.id
    change.decided_at = None if decision == "open" else utcnow()
