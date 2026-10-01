"""Settling what the rules could not: the AI answers closed questions, code checks.

`tree.py` places every clause it has evidence for — Word's own levels, decimal
numbering, list continuity, a sentence introducing a list — and marks the rest
"undecided". This module asks about those only. The AI reads the whole outline
as placed so far and, for each undecided clause, picks one of a closed list of
options: the clauses still open at that point (`tree.options`) and the top of
its part.

Three things keep the answer honest:

* **It can only fill gaps.** Answers are applied through the same `build_tree`
  that made the rest of the tree, and only to undecided clauses, so no answer
  can move a clause the numbering placed. Letting a model place every clause
  had put 5.5 inside 5.4 on a Franklin Madison MSA.
* **It can only choose, never invent.** An answer outside a clause's options
  is discarded and recorded, not trusted; the clause stays undecided.
* **The finished tree is checked** (`tree.violations`) before anything is
  written, and an independent check against the numbering (`broken_runs`)
  runs on the stored tree in the report.

What is applied touches `parent_clause_id`, `level` and `structure_source` and
nothing else, and the before and after go on the event log, so no annotation or
citation can move and the arrangement can be undone.
"""

import json
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DsClause, DsEvent, DsVersion
from .ocr import run_sync
from .structure import body
from .tree import Node, Placement, build_tree, depths, options, resolve_numbers, violations

PROMPT_VERSION = "hierarchy/2"
ARRANGED = "structure.arranged"
NOT_ARRANGED = "structure.not_arranged"
# Decided at ingest by evidence that is not stored — Word's levels and
# indentation — so re-running the rules keeps these as they were.
PINNED = frozenset({"document", "layout"})
# Long clauses show their opening and closing words. The close is where a list
# is announced — "...then:", "means any of the following:" — and cutting it off
# removes the one clue that the next items belong inside this clause.
OPENING_CHARS = 100
CLOSING_CHARS = 40

SYSTEM = """You are given the outline of a legal document: one line per block, in \
reading order, written as [number] label text (page). Long blocks show their \
opening and closing words with an ellipsis between. Blocks are indented under the \
block they sit inside. Lines marked ? are not placed yet.

For each question, choose the block that question's block sits directly inside -- \
its parent -- from the options listed for it. null means the top level.

- A paragraph that carries on a clause sits inside that clause.
- A list sits inside the clause or heading that introduces it.
- A paragraph that closes a list ("No other compensation shall be paid...") sits \
beside the list's owner, not inside the list's last item.
- Title lines, a preamble, top-level clauses and signature lines have no parent.
- The words that end the recitals and begin the agreement ("NOW, THEREFORE, ... the \
parties agree as follows:") sit at the top level, beside the recitals, not inside them.

Answer every question once, choosing only from its options."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "seq": {"type": "integer"},
                    "parent": {"type": ["integer", "null"]},
                },
                "required": ["seq", "parent"],
            },
        }
    },
    "required": ["answers"],
}


@dataclass(frozen=True)
class Question:
    seq: int
    options: tuple[int | None, ...]  # clause seqs, nearest first; None is the top level


class HierarchyJudge(Protocol):
    name: str

    def answer(self, outline: str, questions: list[Question]) -> list[tuple[int, int | None]]: ...


# --- the stored tree, re-derived --------------------------------------------


def _body(clause: DsClause) -> str:
    return body(clause.number_label, clause.text)


def rebuild(
    clauses: list[DsClause], answers: dict[int, int | None] | None = None
) -> tuple[list[Node], list[Placement]]:
    """The tree the rules give these stored clauses (in seq order), with
    `answers` — by list index — filling undecided ones."""
    nodes = [Node(c.number_label, _body(c), c.clause_type) for c in clauses]
    index_of = {clause.clause_id: index for index, clause in enumerate(clauses)}
    pinned = {
        index: (index_of.get(clause.parent_clause_id), clause.structure_source)
        for index, clause in enumerate(clauses)
        if clause.structure_source in PINNED
    }
    return nodes, build_tree(nodes, answers=answers, pinned=pinned)


# --- the outline the model reads --------------------------------------------


def render_outline(clauses: list[DsClause], placements: list[Placement]) -> str:
    levels = depths(placements)
    lines = []
    for clause, placement, level in zip(clauses, placements, levels, strict=True):
        if placement.source == "undecided":
            lines.append(f"? {_line(clause)}")
        else:
            lines.append(f"{'  ' * level}{_line(clause)}")
    return "\n".join(lines)


def _line(clause: DsClause) -> str:
    label = (clause.number_label or "").strip()
    text = " ".join(_body(clause).split())
    if len(text) > OPENING_CHARS + CLOSING_CHARS + 10:
        text = f"{text[:OPENING_CHARS]} … {text[-CLOSING_CHARS:]}"
    page = f" (p{clause.page_number})" if clause.page_number else ""
    return f"[{clause.seq}] {label + ' ' if label else ''}{text}{page}"


def render_questions(questions: list[Question]) -> str:
    def name(option: int | None) -> str:
        return "null" if option is None else str(option)

    return "\n".join(
        f"[{q.seq}] options: {', '.join(name(o) for o in q.options)}" for q in questions
    )


# --- asking -----------------------------------------------------------------


def questions_for(
    clauses: list[DsClause], nodes: list[Node], placements: list[Placement]
) -> list[Question]:
    """One question per undecided clause that leads its own placement.

    A list item continuing an undecided one — (b) after an undecided (a) — is
    not asked about: it goes wherever (a) goes, so asking twice could only
    split the list. Nothing else is left out: stamps and debris were removed
    before the tree was built (`cleanup.py`), and a second, report-side guess
    at what is a stamp had kept seven schedule and exhibit titles from ever
    being asked about.
    """
    out = []
    for index, placement in enumerate(placements):
        if placement.source != "undecided" or placement.follows is not None:
            continue
        choices = options(nodes, placements, index)
        out.append(
            Question(
                seq=clauses[index].seq,
                options=tuple(None if o is None else clauses[o].seq for o in choices),
            )
        )
    return out


def accept(
    rows: list[tuple[int, int | None]], questions: list[Question]
) -> tuple[dict[int, int | None], list[dict]]:
    """Answers that are one of the options offered, and what was refused.

    An answer outside the options is not a difference of opinion: the options
    are every clause still open at that point, so anything else is a misreading.
    A clause answered twice has two answers and therefore none.
    """
    allowed = {q.seq: q.options for q in questions}
    counts = Counter(seq for seq, _ in rows)
    accepted: dict[int, int | None] = {}
    refused: list[dict] = []
    for seq, parent in rows:
        if seq not in allowed:
            why = "not asked about"
        elif counts[seq] > 1:
            why = "answered more than once"
        elif parent not in allowed[seq]:
            why = "not one of its options"
        else:
            accepted[seq] = parent
            continue
        refused.append({"seq": seq, "parent": parent, "why": why})
    return accepted, refused


# --- checking against the numbering -----------------------------------------


def _ancestors(parents: dict[int, int | None], seq: int) -> list[int]:
    chain = []
    while (seq := parents.get(seq)) is not None:
        chain.append(seq)
    return chain


def broken_runs(clauses: list[DsClause], parents: dict[int, int | None]):
    """Consecutive items of one list — (a) then (b), 4. then 5. — in different folders.

    Siblings in a list share a parent in every numbering style, so this is the
    one kind of mistake the numbering alone can prove, and it is checked on the
    tree as stored — whoever placed it. Pairs under different roots are skipped:
    an attached work order numbering its own clauses from 16.17 is not a
    continuation of the main agreement's 16.16.
    """
    ordered = sorted(clauses, key=lambda clause: clause.seq)
    numbers = resolve_numbers([Node(c.number_label, _body(c)) for c in ordered])
    last_in_run: dict[tuple, tuple[int, int]] = {}
    broken = []
    for clause, number in zip(ordered, numbers, strict=True):
        if number is None:
            continue
        run = (number.scheme, number.path[:-1])
        previous = last_in_run.get(run)
        last_in_run[run] = (number.path[-1], clause.seq)
        if not previous or previous[0] != number.path[-1] - 1:
            continue
        before, after = previous[1], clause.seq
        before_chain, after_chain = _ancestors(parents, before), _ancestors(parents, after)
        before_root = before_chain[-1] if before_chain else before
        after_root = after_chain[-1] if after_chain else after
        if before_root != after_root or parents.get(before) == parents.get(after):
            continue
        why = (
            "placed inside the item before it"
            if before in after_chain
            else "split from the item before it"
        )
        broken.append((after, before, why))
    return tuple(broken)


# --- applying ---------------------------------------------------------------


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
    db.flush()


def _apply(
    db: Session,
    version: DsVersion,
    clauses: list[DsClause],
    placements: list[Placement],
    details: dict,
) -> int:
    """Write the three columns, and record everything needed to undo it."""
    changes = []
    for clause, placement, level in zip(clauses, placements, depths(placements), strict=True):
        parent = None if placement.parent is None else clauses[placement.parent].clause_id
        before = {
            "parent": clause.parent_clause_id,
            "level": clause.level,
            "source": clause.structure_source,
        }
        after = {"parent": parent, "level": level, "source": placement.source}
        if before == after:
            continue
        changes.append({"clause_id": clause.clause_id, "seq": clause.seq, "from": before, "to": after})
        clause.parent_clause_id = parent
        clause.level = level
        clause.structure_source = placement.source
    _record(db, version, ARRANGED, {**details, "changed": len(changes), "changes": changes})
    return len(changes)


def arranged(db: Session, version_id: str) -> bool:
    return (
        db.scalar(
            select(DsEvent.id)
            .where(DsEvent.version_id == version_id, DsEvent.event_type == ARRANGED)
            .limit(1)
        )
        is not None
    )


def arrange_version(db: Session, version_id: str, *, judge: HierarchyJudge | None = None) -> str:
    """Settle a stored version's undecided clauses once, and say in one line
    what happened.

    Once only: the tree is stored and a model is not a parser, so re-asking on
    every view would cost money to risk a different answer about the same bytes.
    """
    if arranged(db, version_id):
        return "arranged (already done for this version)"
    version = db.get(DsVersion, version_id)
    if version is None:
        raise LookupError(f"no such version: {version_id}")
    clauses = list(
        db.scalars(select(DsClause).where(DsClause.version_id == version_id).order_by(DsClause.seq))
    )
    if clauses and all(clause.structure_source is None for clause in clauses):
        return (
            "rules only — this version was ingested before the tree recorded its evidence; "
            "upload the file again to rebuild it"
        )

    nodes, placements = rebuild(clauses)
    questions = questions_for(clauses, nodes, placements)
    if not questions:
        _apply(db, version, clauses, placements, {"judge": None, "asked": 0})
        return "every clause placed by the numbering — nothing needed the AI"

    if judge is None:
        unavailable = _ai_unavailable()
        if unavailable:
            return f"rules only — {unavailable}; {_clauses(len(questions))} left undecided"
        judge = ClaudeHierarchyJudge(org_id=version.org_id)
    try:
        rows = judge.answer(render_outline(clauses, placements), questions)
    except Exception as exc:
        reason = f"the AI call failed ({type(exc).__name__})"
        _record(db, version, NOT_ARRANGED, {"judge": judge.name, "reason": reason})
        return f"rules only — {reason}; {_clauses(len(questions))} left undecided"

    accepted, refused = accept(rows, questions)
    index_of = {clause.seq: index for index, clause in enumerate(clauses)}
    answers = {
        index_of[seq]: None if parent is None else index_of[parent]
        for seq, parent in accepted.items()
    }
    nodes, placements = rebuild(clauses, answers)
    problems = violations(nodes, placements)
    if problems:
        # By construction this cannot happen; if it does, the tree is not
        # trusted and nothing is written.
        reason = "the finished tree broke its own rules"
        _record(
            db, version, NOT_ARRANGED, {"judge": judge.name, "reason": reason, "violations": problems}
        )
        return f"rules only — {reason}"

    _apply(
        db,
        version,
        clauses,
        placements,
        {
            "judge": judge.name,
            "prompt_version": PROMPT_VERSION,
            "asked": len(questions),
            "answered": len(accepted),
            "refused": refused,
        },
    )
    seq_of = {clause.clause_id: clause.seq for clause in clauses}
    parents = {clause.seq: seq_of.get(clause.parent_clause_id) for clause in clauses}
    broken = broken_runs(clauses, parents)
    left = sum(placement.source == "undecided" for placement in placements)
    return (
        f"arranged — the AI settled {len(accepted)} of {len(questions)} questions"
        + (f", {len(refused)} answers refused" if refused else "")
        + (f", {_clauses(left)} still undecided" if left else "")
        + f"; {len(broken)} likely mistakes"
    )


def _clauses(count: int) -> str:
    return f"{count} clause" + ("" if count == 1 else "s")


def _ai_unavailable() -> str | None:
    from app.core.config import settings

    # A mocked client answers with a canned payload matching no tool here, which
    # would be rejected anyway — saying so up front is clearer than a failure.
    if settings.mock_claude:
        return "the Claude client is mocked"
    if not getattr(settings, "claude_api_key", None):
        return "no Claude API key is configured"
    return None


# --- the judge --------------------------------------------------------------


class ClaudeHierarchyJudge:
    """The app's Claude client behind `HierarchyJudge`.

    Adapted rather than called directly, as `ocr.py` does, so docstudio depends
    on an interface it owns and every test runs against a fake.
    """

    def __init__(self, *, org_id: str | None = None, model: str | None = None):
        from app.core.config import settings

        self._org_id = org_id
        self._model = model
        self.name = f"claude:{model or settings.claude_model}"

    def answer(self, outline: str, questions: list[Question]) -> list[tuple[int, int | None]]:
        from app.integrations.claude import claude_client

        response = run_sync(
            claude_client.complete_structured(
                org_id=self._org_id,
                system_prompt=SYSTEM,
                user_prompt=f"OUTLINE\n{outline}\n\nQUESTIONS\n{render_questions(questions)}",
                tool_name="clause_parents",
                input_schema=_SCHEMA,
                # One short row per question, with room to spare; a truncated
                # answer shows up as questions left undecided, not a wrong tree.
                max_tokens=512 + 24 * len(questions),
                # Zero, because two looks at the same bytes should not disagree
                # about its structure.
                temperature=0.0,
                model=self._model,
            )
        )
        return _rows_from(response)


def _rows_from(response) -> list[tuple[int, int | None]]:
    """The tool call's rows. Malformed rows are dropped, and stay undecided."""
    blocks = getattr(response, "tool_use_blocks", None) or []
    if not blocks:
        return []
    payload = blocks[0].get("input") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    rows = []
    for entry in payload.get("answers") or []:
        # "parent": null is an answer — top level. No "parent" key at all is a
        # row the model never finished, and reading it as top level would
        # quietly promote a clause.
        if not isinstance(entry, dict) or "parent" not in entry:
            continue
        seq, parent = entry.get("seq"), entry["parent"]
        if isinstance(seq, int) and (parent is None or isinstance(parent, int)):
            rows.append((seq, parent))
    return rows
