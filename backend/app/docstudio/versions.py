"""Which clause in a new version is which clause in the last one.

A clause keeps its `clause_id` across versions when it is recognisably the same
clause, and that is what lets its comments follow it (anchoring's rung 1). A
content hash changes on any edit — precisely when identity matters most — and
position alone breaks the moment a clause is inserted above. So both:

1. The two versions' clauses are aligned in order, and a clause whose words are
   unchanged keeps its id. The body is compared, not the number, so the clauses
   renumbered by an insertion above them are still the same clauses.
2. Inside each stretch that changed, clauses are paired by similarity, best
   pair first; a pair at or above `CARRY_FLOOR` keeps its id — a clause reworded.
3. Everything else is new, and an old clause with no partner was deleted.
"""

import difflib

from rapidfuzz import fuzz

# How alike a reworded clause must stay to still be the same clause, as a
# rapidfuzz ratio (0-100). Below it the id is not carried, and the clause's
# annotations are found by their quotes instead — or orphaned, honestly.
CARRY_FLOOR = 70.0


def carry_ids(previous: list[tuple[str, str]], bodies: list[str]) -> list[str | None]:
    """For each new clause body, the id of the clause it continues, or None.

    `previous` is the last version's clauses as (clause_id, body), in order.
    """
    old = [_norm(text) for _, text in previous]
    new = [_norm(text) for text in bodies]
    carried: list[str | None] = [None] * len(new)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                carried[j1 + k] = previous[i1 + k][0]
        elif tag == "replace":
            pairs = sorted(
                ((fuzz.ratio(old[i], new[j]), i, j) for i in range(i1, i2) for j in range(j1, j2)),
                reverse=True,
            )
            taken_old: set[int] = set()
            for score, i, j in pairs:
                if score < CARRY_FLOOR:
                    break
                if i in taken_old or carried[j] is not None:
                    continue
                taken_old.add(i)
                carried[j] = previous[i][0]
    return carried


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


# --- the history a reader sees ------------------------------------------------------------

# What made a version, in the words of its history, by the event that made it —
# most specific first, since carrying marks to Word records two.
_MADE = (
    ("pdf.patched", lambda d: f"{len(d.get('changes', []))} agreed change(s) written into the PDF, checked"),
    ("edited.in_editor", lambda d: {
        "accepted": "Changes accepted in the editor",
        "rejected": "Changes rejected in the editor",
    }.get(d.get("saved"), f"Edited in the document editor ({d.get('saved', 'saved')})")),
    ("redline.made_editable", lambda d: "Word version made from the PDF"
     + (f", with {d['carried']} agreed change(s) as tracked changes" if d.get("carried") else "")),
    ("redline.suggested", lambda d: f"Changes suggested by {d.get('author') or 'someone'}"),
    ("redline.formatted", lambda d: f"Formatting ({d.get('style', '')}) by {d.get('author') or 'someone'}"),
    ("redline.accepted", lambda d: f"{d.get('count', '')} change(s) accepted"),
    ("redline.rejected", lambda d: f"{d.get('count', '')} change(s) rejected"),
)


def history(db, document_id: str) -> list[dict]:
    """Every version of a document, newest first, each with what made it. None
    is ever changed or deleted: version 1 is the file as it arrived."""
    from sqlalchemy import select

    from .models import DsDocument, DsEvent, DsVersion

    document = db.get(DsDocument, document_id)
    versions = db.scalars(
        select(DsVersion).where(DsVersion.document_id == document_id).order_by(DsVersion.version_number.desc())
    ).all()
    events: dict[str, dict[str, dict]] = {}
    for event in db.scalars(select(DsEvent).where(DsEvent.document_id == document_id)):
        events.setdefault(event.version_id, {}).setdefault(event.event_type, event.details or {})
    rows = []
    for version in versions:
        made = events.get(version.id, {})
        what = next((describe(made[kind]) for kind, describe in _MADE if kind in made), None)
        rows.append({
            "version_id": version.id,
            "number": version.version_number,
            "filename": version.filename,
            "mime": version.mime_type,
            "created_at": version.created_at.isoformat() if version.created_at else None,
            "what": what or ("The file as it arrived" if version.version_number == 1 else "A new file added"),
            "current": document is not None and document.current_version_id == version.id,
            "kept": bool(version.storage_key),
        })
    return rows


def compare(db, older_id: str, newer_id: str) -> dict:
    """What words changed between two versions, shown in the newer version's
    clauses. Compared as one run of words, not clause against clause: a PDF and
    the Word version made from it split paragraphs differently, and a clause
    split in two has not changed. Words removed show where they stood."""
    import re

    from .ask import label_of
    from .ocr import _plain
    from .service import clauses_for

    old, new = clauses_for(db, older_id), clauses_for(db, newer_id)
    by_id = {c.clause_id: c for c in new}
    a = [w for c in old for w in re.findall(r"\S+", _plain(c.text or ""))]
    b = [(w, k) for k, c in enumerate(new) for w in re.findall(r"\S+", _plain(c.text or ""))]
    parts: dict[int, list[list[str]]] = {}

    def put(k: int, op: str, text: str) -> None:
        row = parts.setdefault(k, [])
        if row and row[-1][0] == op:
            row[-1][1] += " " + text
        else:
            row.append([op, text])

    ops = difflib.SequenceMatcher(None, a, [w for w, _ in b], autojunk=False).get_opcodes()
    changed = {b[j][1] for tag, i1, i2, j1, j2 in ops if tag != "equal" for j in range(j1, j2)}
    changed |= {b[min(j1, len(b) - 1)][1] for tag, i1, i2, j1, j2 in ops if tag in ("delete", "replace") and b}
    for tag, i1, i2, j1, j2 in ops:
        if tag in ("delete", "replace") and b:
            put(b[min(j1, len(b) - 1)][1], "-", " ".join(a[i1:i2]))
        for w, k in b[j1:j2]:
            if k in changed:
                put(k, "=" if tag == "equal" else "+", w)
    rows = []
    for k in sorted(parts):
        ops_here = {op for op, _ in parts[k]}
        status = "added" if ops_here == {"+"} else "changed"
        rows.append({"status": status, "label": label_of(new[k], by_id), "parts": parts[k]})
    removed = sum(i2 - i1 for tag, i1, i2, *_ in ops if tag in ("delete", "replace"))
    inserted = sum(j2 - j1 for tag, _, _, j1, j2 in ops if tag in ("insert", "replace"))
    return {"rows": rows, "counts": {"clauses": len(rows), "words_removed": removed, "words_added": inserted},
            "unchanged": len(new) - len(rows)}
