"""Score extracted obligation rows against an answer key. Pure: no database, no network, no model.

A ROW is a plain dict (see adapters.py): quote, responsible_party, description, type, timing, due_date, ends_on.
Matching is by where the row's quote sits in the contract text, never by wording, and never by an AI judge:

  * an ATOM is FOUND when some row's quote covers at least half of the atom's characters;
  * a ROW is TRUE when its quote touches (shares MIN_OVERLAP_CHARS or more with) at least one duty or
    restriction atom. Otherwise it is classed condition_only, commitment_only, not_a_duty (it quotes something
    a lawyer judged not to be a duty), unlabelled (the key has nothing there: a hole in the key or a made-up
    row) or quote_not_found.

The two constants below are fixed by a calibration against the audit's hand counts (see README), not tuned
per run.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from rapidfuzz import fuzz

from .gold import HEADLINE_KINDS, Atom, Gold, _clause_base, atom_deadline

MIN_SHARE_OF_ATOM = 0.5
MIN_OVERLAP_CHARS = 15
FUZZY_CUTOFF = 90  # the same bar the app's citation check uses


def locate(quote: str | None, text: str) -> tuple[int, int, str] | None:
    """Where a quote sits in the text: exact, then ignoring spacing, then 90 percent fuzzy. None if nowhere."""
    quote = (quote or "").strip()
    if not quote:
        return None
    at = text.find(quote)
    if at >= 0:
        return at, at + len(quote), "exact"
    loose = re.search(r"\s+".join(re.escape(token) for token in quote.split()), text)
    if loose:
        return loose.start(), loose.end(), "spacing"
    fuzzy = fuzz.partial_ratio_alignment(quote, text, score_cutoff=FUZZY_CUTOFF)
    if fuzzy is not None:
        return fuzzy.dest_start, fuzzy.dest_end, "fuzzy"
    return None


def side_of(party_text: str | None, parties: dict) -> str | None:
    """us / them / both from the words of a responsible_party, using the key's party names and role words."""
    said = (party_text or "").lower()
    if not said:
        return None

    def mentioned(side: str) -> bool:
        names = [*parties[side].get("names", []), *parties[side].get("roles", [])]
        return any(name.lower() in said for name in names)

    us, them = mentioned("us"), mentioned("them")
    if (us and them) or re.search(r"\b(either|each|both)\s+part(?:y|ies)\b|\bthe parties\b|\bmutual", said):
        return "both"
    return "us" if us else "them" if them else None


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def _share(rate: tuple[int, int]) -> float | None:
    return None if rate[1] == 0 else rate[0] / rate[1]


def score(gold: Gold, rows: list[dict]) -> dict:
    spans = [locate(row.get("quote"), gold.text) for row in rows]
    headline = [a for a in gold.atoms if a.kind in HEADLINE_KINDS]

    covers: dict[str, list[int]] = {}  # atom id -> indexes of rows that find it
    for atom in gold.atoms:
        length = atom.end - atom.start
        covers[atom.id] = [
            i for i, span in enumerate(spans)
            if span and _overlap(span[0], span[1], atom.start, atom.end) >= MIN_SHARE_OF_ATOM * length
        ]

    def touching(span, items):
        return [x for x in items if _overlap(span[0], span[1], x.start, x.end) >= MIN_OVERLAP_CHARS]

    classes: list[str] = []
    touched_atoms: list[list[Atom]] = []
    for span in spans:
        if span is None:
            classes.append("quote_not_found")
            touched_atoms.append([])
            continue
        atoms_here = touching(span, gold.atoms)
        touched_atoms.append(atoms_here)
        kinds = {a.kind for a in atoms_here}
        if kinds & set(HEADLINE_KINDS):
            classes.append("duty")
        elif "condition" in kinds:
            classes.append("condition_only")
        elif "commitment" in kinds:
            classes.append("commitment_only")
        elif touching(span, gold.not_duties):
            classes.append("not_a_duty")
        else:
            classes.append("unlabelled")

    found = [a for a in headline if covers[a.id]]
    by_clause: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for atom in headline:
        by_clause[_clause_base(atom.clause)][1] += 1
        by_clause[_clause_base(atom.clause)][0] += bool(covers[atom.id])

    side_ok = [0, 0]
    timing_ok = {"kind": [0, 0], "period": [0, 0], "anchor": [0, 0]}
    for atom in found:
        row = rows[covers[atom.id][0]]
        side_ok[1] += 1
        side_ok[0] += side_of(row.get("responsible_party"), gold.parties) == atom.owed_by
        facts = row.get("timing")
        if facts is not None and atom.timing.get("stated_in_text") and atom.timing.get("period_number") is not None:
            timing_ok["kind"][1] += 1
            timing_ok["kind"][0] += facts.get("kind") == atom.timing.get("kind")
            timing_ok["period"][1] += 1
            timing_ok["period"][0] += (facts.get("period_number"), facts.get("period_unit")) == (
                atom.timing.get("period_number"), atom.timing.get("period_unit"))
            timing_ok["anchor"][1] += 1
            timing_ok["anchor"][0] += facts.get("anchor") == atom.timing.get("anchor")

    invented = []
    for i, row in enumerate(rows):
        for field in ("due_date", "ends_on"):
            if not row.get(field):
                continue
            best = max(touched_atoms[i], key=lambda a: _overlap(spans[i][0], spans[i][1], a.start, a.end), default=None)
            wanted = atom_deadline(gold, best)["due" if field == "due_date" else "ends_on"] if best else None
            if wanted != row[field]:
                invented.append({"row": i, "field": field, "row_value": row[field], "key_value": wanted,
                                 "kind": "invented" if wanted is None else "different"})

    true_rows = [i for i, c in enumerate(classes) if c == "duty"]
    credited = sum(1 for i in true_rows for a in headline if i in covers[a.id])
    other = {kind: [sum(1 for a in gold.atoms if a.kind == kind and covers[a.id]),
                    sum(1 for a in gold.atoms if a.kind == kind)] for kind in ("condition", "commitment")}
    return {
        "key": gold.id, "fingerprint": gold.fingerprint, "key_status": gold.effective_status,
        "atoms": len(headline), "found": len(found), "recall": _share((len(found), len(headline))),
        "recall_by_clause": {k: tuple(v) for k, v in sorted(by_clause.items())},
        "missed": [{"id": a.id, "clause": a.clause, "start": a.quote[:60]} for a in headline if not covers[a.id]],
        "rows": len(rows), "row_classes": dict(Counter(classes)),
        "precision": _share((len(true_rows), len(rows))),
        "side": {"n": side_ok[1], "correct": side_ok[0], "value": _share(tuple(side_ok))},
        "timing": {k: {"n": v[1], "correct": v[0], "value": _share(tuple(v))} for k, v in timing_ok.items()},
        "invented_dates": invented,
        "quote_valid": _share((sum(1 for s in spans if s), len(rows))),
        "atoms_per_true_row": None if not true_rows else credited / len(true_rows),
        "other_kinds": {k: tuple(v) for k, v in other.items()},
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def scorecard(result: dict) -> str:
    """The plain-language scorecard. Always says whether the key is only a draft."""
    r = result
    lines = [
        f"KEY {r['key']} ({r['fingerprint']}) - KEY STATUS: {r['key_status']}"
        + ("   (a draft key: numbers are a rehearsal, not a baseline)" if r["key_status"] != "lawyer_confirmed" else ""),
        f"  recall        {_pct(r['recall'])}   ({r['found']} of {r['atoms']} duties and restrictions found)",
        f"  precision     {_pct(r['precision'])}   ({r['row_classes'].get('duty', 0)} of {r['rows']} rows are real duties)",
        f"  row classes   {r['row_classes']}",
        f"  side correct  {_pct(r['side']['value'])}   ({r['side']['correct']} of {r['side']['n']} found duties name the right party)",
    ]
    t = r["timing"]
    lines.append(
        "  timing        " + ("n/a (these rows carry no typed timing)" if t["kind"]["n"] == 0 else
                              f"kind {_pct(t['kind']['value'])}, period {_pct(t['period']['value'])}, "
                              f"anchor {_pct(t['anchor']['value'])}  (over {t['kind']['n']} dated duties)")
    )
    lines.append(f"  invented/wrong dates  {len(r['invented_dates'])}   (must be 0)")
    lines.append(f"  quotes found in the text  {_pct(r['quote_valid'])}")
    if r["atoms_per_true_row"] is not None:
        lines.append(f"  atoms per true row  {r['atoms_per_true_row']:.1f}   (above 1 = rows bundle several duties)")
    lines.append("  also: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in r["other_kinds"].items()))
    if r["missed"]:
        lines.append("  missed duties:")
        lines += [f"    {m['clause']:6} {m['id']}  {m['start']}" for m in r["missed"]]
    lines.append("  recall by clause: " + ", ".join(f"{k}: {a}/{b}" for k, (a, b) in r["recall_by_clause"].items()))
    return "\n".join(lines)
