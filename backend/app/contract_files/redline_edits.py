"""Reading a ContractEdit's `citation`.

A suggested change (a playbook fix, an assistant edit from chat, or a manual
redline) is stored as one ContractEdit. Where it belongs is recorded in its
`citation`, a list of small typed entries:

* ``assistant_edit_version``: the proposal version holding the tracked-change preview
* ``anchor``: where the edit sits in its base version's text (start/end/matched)
* ``playbook_run`` / ``playbook_deviation``: the playbook issue this edit fixes
* ``source_quote``, ``author``: display details

These helpers only read that list, so the routes (which decide edits) and the
response schema (which tells the page what it can do with an edit) agree on
what an edit means.
"""

from __future__ import annotations

from typing import Any

from app.contracts.blanks import first_blank


def _entries(citation: list | None) -> list[dict[str, Any]]:
    return [entry for entry in citation or [] if isinstance(entry, dict)]


def anchor(citation: list | None) -> dict[str, Any]:
    for entry in _entries(citation):
        if entry.get("type") == "anchor":
            return entry
    return {}


def anchor_span(citation: list | None) -> tuple[int, int] | None:
    """The edit's [start, end) in its base version's text, or None if it was never located."""
    found = anchor(citation)
    start, end = found.get("start", -1), found.get("end", -1)
    if not found.get("matched") or not isinstance(start, int) or not isinstance(end, int):
        return None
    if start < 0 or end < start:
        return None
    return start, end


def is_appended_insertion(edit_type: str | None, citation: list | None, replacement_text: str | None) -> bool:
    """Playbook redlines append new clauses they couldn't place to the end of the document."""
    return edit_type == "playbook_redline" and anchor_span(citation) is None and bool(replacement_text)


def is_locatable(edit_type: str | None, citation: list | None, replacement_text: str | None) -> bool:
    """Whether accepting this edit can change the document: it was placed in the
    text, or it is a new clause that goes at the end."""
    return anchor_span(citation) is not None or is_appended_insertion(edit_type, citation, replacement_text)


def blank_in(edit_type: str | None, replacement_text: str | None) -> str | None:
    """The first fill-in blank the wording of this change would write into the contract. A manual
    change is what the person typed, so it is never held to this (contracts.blanks)."""
    if edit_type == "manual":
        return None
    return first_blank(replacement_text)


def proposal_version_id(citation: list | None) -> str | None:
    """The proposal version this edit belongs to (the document with the whole redline applied)."""
    for entry in _entries(citation):
        if entry.get("type") == "assistant_edit_version" and entry.get("contract_version_id"):
            return entry["contract_version_id"]
    return None


def paired_deviation_id(citation: list | None) -> str | None:
    """The playbook issue this edit fixes, if it was made for one."""
    for entry in _entries(citation):
        deviation_id = entry.get("playbook_deviation_id")
        if isinstance(deviation_id, str) and deviation_id:
            return deviation_id
    return None
