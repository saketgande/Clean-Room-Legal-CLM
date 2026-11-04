"""The fixed list of contract types, and how a stored free-text type is read against it.

Contract.contract_type is free text (the AI reading, an upload, a form, a person all write it),
so "is this an NDA?" cannot be a string compare. ``canonical_contract_type`` maps what is stored
onto the list below; anything it cannot map means "the type is not known" and the contract is
asked about (``type_needed``). The title is never used to recognise a type (FR-8): it only
raises a flag when a contract marked Other looks like something else.
"""

from __future__ import annotations

import re
from datetime import date

# (value, label): display order. Values are exactly what drafting already stores
# (playbooks.library CONTRACT_TYPE_OF_KIND / _FORM + the custom-draft "other"), so a person's
# choice and a drafted contract never differ in spelling.
TYPES: list[tuple[str, str]] = [
    ("NDA", "NDA"),
    ("MSA", "MSA (master services)"),
    ("Statement of Work", "Statement of Work (SOW)"),
    ("Software / SaaS Agreement", "Software / SaaS"),
    ("Vendor Agreement", "Vendor"),
    ("Consultancy Agreement", "Consultancy"),
    ("DPA", "Data processing (DPA)"),
    ("Customer Agreement", "Customer"),
    ("other", "Other"),
]
VALUES = [v for v, _ in TYPES]

# First match wins, so order matters (a "data processing agreement for software" is a DPA).
_RECOGNISE: list[tuple[str, re.Pattern[str]]] = [
    (v, re.compile(p))
    for v, p in [
        ("NDA", r"\b(nda|mnda|non disclosure|nondisclosure|confidentiality agreement)\b"),
        ("DPA", r"\b(dpa|data processing)\b"),
        ("Statement of Work", r"\b(sow|statement of work)\b"),
        ("MSA", r"\b(msa|master services? agreement)\b"),
        ("Software / SaaS Agreement", r"\b(saas|software)\b"),
        ("Consultancy Agreement", r"\b(consultancy|consulting)\b"),
        ("Vendor Agreement", r"\b(vendor|supplier)\b"),
        ("Customer Agreement", r"\bcustomer agreement\b"),
    ]
]


def canonical_contract_type(raw: str | None) -> str | None:
    text = re.sub(r"[^a-z0-9]+", " ", (raw or "").lower()).strip()
    if not text:
        return None
    for value, pattern in _RECOGNISE:
        if pattern.search(text):
            return value
    return "other" if text == "other" else None


def label_of(value: str | None) -> str:
    canon = canonical_contract_type(value)
    return dict(TYPES).get(canon or "", (value or "").strip())


def type_needed(
    contract_type: str | None,
    title: str | None,
    metadata_json: dict | None,
    lifecycle_stage: str | None,
    archived: bool | None,
) -> bool:
    """Whether to ask a person for this contract's type. Closed and archived contracts are
    never asked (nothing there can still be checked against)."""
    if archived or lifecycle_stage == "closed":
        return False
    canon = canonical_contract_type(contract_type)
    if canon is None:
        return True
    if canon != "other":
        return False
    titled = canonical_contract_type(title)
    chosen = ((metadata_json or {}).get("field_sources") or {}).get("contract_type") == "user"
    return titled not in (None, "other") and not chosen


def suggested_type(contract_type, title, metadata_json, lifecycle_stage, archived) -> str | None:
    """The document reading's guess, offered only while a type is needed. Never the title."""
    if not type_needed(contract_type, title, metadata_json, lifecycle_stage, archived):
        return None
    reading = (((metadata_json or {}).get("ai_suggestions") or {}).get("metadata") or {})
    return canonical_contract_type(reading.get("contract_type"))


def contract_status(c, today: date) -> str | None:
    """"in_force" / "in_progress" for a live contract, None once it is closed, archived or ended."""
    if c.archived or c.lifecycle_stage == "closed":
        return None
    if c.expiration_date and c.expiration_date < today:
        return None
    return "in_force" if c.lifecycle_stage == "active" else "in_progress"
