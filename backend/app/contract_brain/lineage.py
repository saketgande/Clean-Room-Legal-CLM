"""CLM lineage edges — how contracts relate to each other.

A Statement of Work lives under a Master Services Agreement; a DPA hangs off a
services agreement; an amendment supersedes an original. These relationships are
what let the graph answer "which SoWs die if this MSA terminates".

The honest problem: this codebase has no explicit parent references on contract
rows. So lineage is INFERRED from contract type plus a shared counterparty, and
every inferred edge carries ``inferred: true`` and a confidence. It is a
suggestion the graph surfaces as "likely governed by", never an asserted fact —
merging a SoW under the wrong master would be a real error, so a human confirms.

When an explicit ``parent_contract_id`` does arrive (the agreement request forms
capture it), ``explicit_parent_edges`` uses it directly at full confidence.
"""

from __future__ import annotations

import re

# Which child types hang under which parent types. Order = preference: a SoW
# prefers an MSA parent, falling back to a framework agreement.
_CHILD_OF: dict[str, list[str]] = {
    "sow": ["master services agreement", "master service agreement", "msa", "framework agreement"],
    "statement of work": ["master services agreement", "master service agreement", "msa", "framework agreement"],
    "dpa": ["master services agreement", "services agreement", "saas", "subscription", "msa"],
    "data processing agreement": ["master services agreement", "services agreement", "saas", "subscription", "msa"],
}

# Amendment/renewal language that means "this supersedes an earlier contract".
_SUPERSEDES = re.compile(r"\b(amendment|amended|renewal|renewed|novation|addendum|supplement)\b", re.I)


def _canon_type(contract_type: str | None, title: str | None) -> str:
    """Lowercased type, falling back to the title so a SoW named in the title
    but typed 'None' is still classified."""
    return f"{contract_type or ''} {title or ''}".lower().strip()


def child_parent_kind(contract_type: str | None, title: str | None) -> tuple[str, list[str]] | None:
    """If this contract looks like a child, return (child_label, [parent types])."""
    text = _canon_type(contract_type, title)
    for child_key, parent_types in _CHILD_OF.items():
        if child_key in text:
            return child_key, parent_types
    return None


def looks_like_amendment(contract_type: str | None, title: str | None) -> bool:
    return bool(_SUPERSEDES.search(_canon_type(contract_type, title)))


def infer_parent(child: dict, candidates: list[dict]) -> tuple[dict, float] | None:
    """Pick the most likely parent for a child contract, or None.

    child / candidates are dicts with id, contract_type, title, counterparty_key,
    effective_date. A parent must share the counterparty (the strongest signal),
    be of an expected parent type, and predate the child.
    """
    kind = child_parent_kind(child.get("contract_type"), child.get("title"))
    if kind is None or not child.get("counterparty_key"):
        return None
    _, parent_types = kind

    scored: list[tuple[dict, float]] = []
    for cand in candidates:
        if cand["id"] == child["id"]:
            continue
        if cand.get("counterparty_key") != child["counterparty_key"]:
            continue                                  # different counterparty — not a parent
        cand_text = _canon_type(cand.get("contract_type"), cand.get("title"))
        if not any(pt in cand_text for pt in parent_types):
            continue
        score = 0.6                                   # same counterparty + right type
        # a parent should start on or before the child
        ce, pe = child.get("effective_date"), cand.get("effective_date")
        if ce and pe:
            if pe <= ce:
                score += 0.25
            else:
                score -= 0.2                          # parent starts after child — unlikely
        scored.append((cand, round(min(score, 0.95), 2)))

    if not scored:
        return None
    scored.sort(key=lambda t: t[1], reverse=True)
    best, conf = scored[0]
    # only offer edges we are at least moderately sure of; the rest are noise
    return (best, conf) if conf >= 0.5 else None
