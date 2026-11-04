"""Turn an extraction into the plain rows the scorer reads. One adapter per thing under test; the scorer never
sees anything but these rows, so a later pipeline (the Tier 2 redesign) only needs its own adapter here.

A plain row: {"quote", "responsible_party", "description", "type", "timing", "due_date", "ends_on"}.
`timing` is the typed facts dict (kind, period_number, period_unit, anchor, ...) or None when the row has none.
"""

from __future__ import annotations

from .gold import Gold, atom_deadline


def _dates(timing: dict | None, quote: str, anchors: dict | None) -> tuple[str | None, str | None]:
    """The dates production would store: counted by code from the timing and the contract's anchors."""
    if timing is None or anchors is None:
        return None, None
    from app.ai.schemas import ObligationTiming
    from app.obligations.service import obligation_deadline

    block = obligation_deadline(ObligationTiming(**timing), anchors, quote=quote)
    return block["due"], block["ends_on"]


def rows_from_output(output, anchors: dict | None = None) -> list[dict]:
    """The rows of a validated obligation_extraction answer (the model's own output, before any storing)."""
    items = output.get("obligations", []) if isinstance(output, dict) else output.model_dump(mode="json")["obligations"]
    rows = []
    for item in items:
        quote = ((item.get("citations") or [{}])[0] or {}).get("quote") or ""
        timing = item.get("timing")
        due, ends = _dates(timing, quote, anchors)
        rows.append({"quote": quote, "responsible_party": item.get("responsible_party"),
                     "description": item.get("description", ""), "type": item.get("obligation_type"),
                     "timing": timing, "due_date": due, "ends_on": ends})
    return rows


def rows_from_stored(db, contract_id: str) -> list[dict]:
    """The obligations stored for a contract today (read-only). Rows read before typed timing existed carry
    no `timing`; their dates are what the database holds."""
    from sqlalchemy import select

    from app.obligations.models import Obligation

    stored = db.scalars(
        select(Obligation)
        .where(Obligation.contract_id == contract_id, Obligation.deleted_at.is_(None))
        .order_by(Obligation.created_at, Obligation.id)
    ).all()
    rows = []
    for ob in stored:
        block = (ob.metadata_json or {}).get("deadline") or {}
        rows.append({
            "quote": (ob.source_citation or {}).get("quote") or "",
            "responsible_party": ob.responsible_party, "description": ob.description,
            "type": ob.obligation_type, "timing": block.get("facts"),
            "due_date": ob.due_date.isoformat() if ob.due_date else None, "ends_on": block.get("ends_on"),
        })
    return rows


def rows_from_gold(gold: Gold) -> list[dict]:
    """The key read back as if it were an extraction: one row per duty or restriction. A scorer bug that no
    real model would reveal shows up as this not scoring perfectly."""
    parties = gold.parties
    said = {
        "us": parties["us"]["names"][0], "them": parties["them"]["names"][0],
        "both": f"Either Party ({parties['us']['names'][0]} and {parties['them']['names'][0]})",
    }
    rows = []
    for atom in gold.atoms:
        if atom.kind not in ("duty", "restriction"):
            continue
        block = atom_deadline(gold, atom)
        rows.append({"quote": atom.quote, "responsible_party": said[atom.owed_by], "description": atom.quote,
                     "type": atom.type, "timing": atom.timing, "due_date": block["due"], "ends_on": block["ends_on"]})
    return rows
