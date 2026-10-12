"""Settling what the rules could not: the AI answers closed questions, code checks.

`reader.tree` places every clause it has evidence for — Word's own levels,
decimal numbering, list continuity, a sentence introducing a list — and marks
the rest "undecided". This module asks about those only. The AI reads the whole
outline as placed so far and, for each undecided clause, picks one of a closed
list of options: the clauses still open at that point (`tree.options`) and the
top of its part.

Three things keep the answer honest:

* **It can only fill gaps.** Answers are applied through the same `build_tree`
  that made the rest of the tree, and only to undecided clauses, so no answer
  can move a clause the numbering placed. Letting a model place every clause
  had put 5.5 inside 5.4 on a Franklin Madison MSA.
* **It can only choose, never invent.** An answer outside a clause's options
  is discarded and reported, not trusted; the clause stays undecided.
* **The finished tree is checked** (`tree.violations`) before it is returned,
  and `broken_runs` checks it independently against the numbering.

It works on the reader's output (`reader.structure.BuiltClause`) and touches
nothing but `parent_clause_id`, `level` and `structure_source`, so offsets and
citations cannot move. It stores nothing: the caller decides what to keep.

The call goes through the AI gateway (feature "clause_hierarchy"), so it gets
the prompt override, the guard line, the token cap, the output check and a
ledger row like every other AI feature. Not yet called from the upload path —
switching it on there is a product decision (one Claude call per upload that
has undecided clauses).

Moved from docstudio/hierarchy.py, which did the same against docstudio's own
tables (removed with docstudio).
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

from .reader.structure import BuiltClause, body
from .reader.tree import Node, Placement, build_tree, depths, options, resolve_numbers, violations

FEATURE_KEY = "clause_hierarchy"
# Decided at read time by evidence that is not kept — Word's levels and
# indentation — so re-running the rules keeps these as they were.
PINNED = frozenset({"document", "layout"})
# Long clauses show their opening and closing words. The close is where a list
# is announced — "...then:", "means any of the following:" — and cutting it off
# removes the one clue that the next items belong inside this clause.
OPENING_CHARS = 100
CLOSING_CHARS = 40

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


@dataclass
class Arrangement:
    """What `arrange` decided. `clauses` is the input with the three tree
    columns updated; it is the input unchanged whenever `applied` is False."""

    clauses: list[BuiltClause]
    applied: bool
    summary: str
    asked: int = 0
    answered: int = 0
    refused: list[dict] = field(default_factory=list)
    still_undecided: int = 0
    broken: tuple = ()


# --- the tree, re-derived ---------------------------------------------------


def _body(clause: BuiltClause) -> str:
    return body(clause.number_label, clause.text)


def rebuild(
    clauses: list[BuiltClause], answers: dict[int, int | None] | None = None
) -> tuple[list[Node], list[Placement]]:
    """The tree the rules give these clauses (in seq order), with `answers` —
    by list index — filling undecided ones."""
    nodes = [Node(c.number_label, _body(c), c.clause_type) for c in clauses]
    index_of = {clause.clause_id: index for index, clause in enumerate(clauses)}
    pinned = {
        index: (index_of.get(clause.parent_clause_id), clause.structure_source)
        for index, clause in enumerate(clauses)
        if clause.structure_source in PINNED
    }
    return nodes, build_tree(nodes, answers=answers, pinned=pinned)


# --- the outline the model reads --------------------------------------------


def render_outline(clauses: list[BuiltClause], placements: list[Placement]) -> str:
    levels = depths(placements)
    lines = []
    for clause, placement, level in zip(clauses, placements, levels, strict=True):
        if placement.source == "undecided":
            lines.append(f"? {_line(clause)}")
        else:
            lines.append(f"{'  ' * level}{_line(clause)}")
    return "\n".join(lines)


def _line(clause: BuiltClause) -> str:
    label = (clause.number_label or "").strip()
    text = " ".join(_body(clause).split())
    if len(text) > OPENING_CHARS + CLOSING_CHARS + 10:
        text = f"{text[:OPENING_CHARS]} … {text[-CLOSING_CHARS:]}"
    page = f" (p{clause.page_number})" if clause.page_number else ""
    return f"[{clause.seq}] {label + ' ' if label else ''}{text}{page}"


def render_questions(questions: list[Question]) -> str:
    def name(option: int | None) -> str:
        return "null" if option is None else str(option)

    return "\n".join(f"[{q.seq}] options: {', '.join(name(o) for o in q.options)}" for q in questions)


# --- asking -----------------------------------------------------------------


def questions_for(clauses: list[BuiltClause], nodes: list[Node], placements: list[Placement]) -> list[Question]:
    """One question per undecided clause that leads its own placement.

    A list item continuing an undecided one — (b) after an undecided (a) — is
    not asked about: it goes wherever (a) goes, so asking twice could only
    split the list.
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


def broken_runs(clauses: list[BuiltClause], parents: dict[int, int | None]):
    """Consecutive items of one list — (a) then (b), 4. then 5. — in different folders.

    Siblings in a list share a parent in every numbering style, so this is the
    one kind of mistake the numbering alone can prove. Pairs under different
    roots are skipped: an attached work order numbering its own clauses from
    16.17 is not a continuation of the main agreement's 16.16.
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
        why = "placed inside the item before it" if before in after_chain else "split from the item before it"
        broken.append((after, before, why))
    return tuple(broken)


# --- arranging --------------------------------------------------------------


def _placed(clauses: list[BuiltClause], placements: list[Placement]) -> list[BuiltClause]:
    out = []
    for clause, placement, level in zip(clauses, placements, depths(placements), strict=True):
        parent = None if placement.parent is None else clauses[placement.parent].clause_id
        out.append(
            dataclasses.replace(clause, parent_clause_id=parent, level=level, structure_source=placement.source)
        )
    return out


def _count(count: int) -> str:
    return f"{count} clause" + ("" if count == 1 else "s")


def arrange(clauses: list[BuiltClause], judge: HierarchyJudge) -> Arrangement:
    """Settle the undecided clauses of one document (its clauses in seq order).

    Never raises for an AI failure: the clauses come back as the rules left
    them, with the reason in `summary`.
    """
    clauses = sorted(clauses, key=lambda clause: clause.seq)
    nodes, placements = rebuild(clauses)
    questions = questions_for(clauses, nodes, placements)
    if not questions:
        return Arrangement(_placed(clauses, placements), True, "every clause placed by the numbering — nothing needed the AI")

    try:
        rows = judge.answer(render_outline(clauses, placements), questions)
    except Exception as exc:
        return Arrangement(
            clauses, False,
            f"rules only — the AI call failed ({type(exc).__name__}); {_count(len(questions))} left undecided",
            asked=len(questions), still_undecided=len(questions),
        )

    accepted, refused = accept(rows, questions)
    index_of = {clause.seq: index for index, clause in enumerate(clauses)}
    answers = {index_of[seq]: None if parent is None else index_of[parent] for seq, parent in accepted.items()}
    nodes, placements = rebuild(clauses, answers)
    if violations(nodes, placements):
        # By construction this cannot happen; if it does, the tree is not trusted.
        return Arrangement(clauses, False, "rules only — the finished tree broke its own rules",
                           asked=len(questions), refused=refused)

    placed = _placed(clauses, placements)
    seq_of = {clause.clause_id: clause.seq for clause in placed}
    parents = {clause.seq: seq_of.get(clause.parent_clause_id) for clause in placed}
    broken = broken_runs(placed, parents)
    left = sum(placement.source == "undecided" for placement in placements)
    summary = (
        f"arranged — the AI settled {len(accepted)} of {len(questions)} questions"
        + (f", {len(refused)} answers refused" if refused else "")
        + (f", {_count(left)} still undecided" if left else "")
        + f"; {len(broken)} likely mistakes"
    )
    return Arrangement(placed, True, summary, asked=len(questions), answered=len(accepted),
                       refused=refused, still_undecided=left, broken=broken)


# --- the judge --------------------------------------------------------------


def rows_from(payload: dict) -> list[tuple[int, int | None]]:
    """The tool call's rows. Malformed rows are dropped, and stay undecided."""
    rows = []
    for entry in (payload or {}).get("answers") or []:
        # "parent": null is an answer — top level. No "parent" key at all is a
        # row the model never finished, and reading it as top level would
        # quietly promote a clause.
        if not isinstance(entry, dict) or "parent" not in entry:
            continue
        seq, parent = entry.get("seq"), entry["parent"]
        if isinstance(seq, int) and not isinstance(seq, bool) and (
            parent is None or (isinstance(parent, int) and not isinstance(parent, bool))
        ):
            rows.append((seq, parent))
    return rows


class GatewayHierarchyJudge:
    """Asks Claude through the AI gateway (feature "clause_hierarchy")."""

    name = f"gateway:{FEATURE_KEY}"

    def __init__(self, db: Session, *, org_id: str, resource: tuple[str, str] | None = None, provider=None):
        self._db = db
        self._org_id = org_id
        self._resource = resource
        self._provider = provider

    def answer(self, outline: str, questions: list[Question]) -> list[tuple[int, int | None]]:
        from app.ai.gateway import AICallContext, gateway_for

        result = gateway_for(self._provider).structured_sync(
            self._db,
            FEATURE_KEY,
            ctx=AICallContext(org_id=self._org_id, resource=self._resource),
            user_prompt=f"OUTLINE\n{outline}\n\nQUESTIONS\n{render_questions(questions)}",
            input_schema=_SCHEMA,
            log_input={"questions": len(questions)},
        )
        return rows_from(result.data)
