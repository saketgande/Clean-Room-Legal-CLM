"""The agreement wizard's nine forms, as request types the server can enforce.

``agreement_forms.json`` is the server's copy of the fields each wizard form asks
for (``AGREEMENT_FORMS`` in frontend ``_agreement-forms.tsx``). A frontend test
fails if the two drift apart, so the wizard and the server always agree on what
is required.

The fields are code-owned: ``ensure_agreement_types`` re-syncs them on every
call, so an edit in code reaches every organisation without a data migration.
Name, SLA hours and active flag stay admin-editable.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intake.models import IntakeRequestField, IntakeRequestType

_PATH = Path(__file__).with_name("agreement_forms.json")


@lru_cache(maxsize=1)
def form_defs() -> tuple[dict, ...]:
    return tuple(json.loads(_PATH.read_text(encoding="utf-8")))


def _field_rows(org_id: str, form: dict) -> list[IntakeRequestField]:
    return [
        IntakeRequestField(
            org_id=org_id, key=f["key"], label=f["label"], kind=f["kind"],
            required=f["required"], sort_order=i,
            options=[{"value": o, "label": o} for o in f["options"]] if f.get("options") else None,
        )
        for i, f in enumerate(form["fields"])
    ]


def _same_fields(t: IntakeRequestType, form: dict) -> bool:
    have = [(f.key, f.label, f.kind, f.required, [o["value"] for o in (f.options or [])])
            for f in sorted(t.fields, key=lambda f: f.sort_order)]
    want = [(f["key"], f["label"], f["kind"], f["required"], f.get("options") or [])
            for f in form["fields"]]
    return have == want


def ensure_agreement_types(db: Session, org_id: str) -> None:
    """Create any missing agreement-form request types for ``org_id`` and bring
    their fields in line with code. Idempotent; flushes but does not commit."""
    existing = {
        t.form_key: t
        for t in db.scalars(
            select(IntakeRequestType).where(
                IntakeRequestType.org_id == org_id, IntakeRequestType.form_key.is_not(None)
            )
        )
    }
    for form in form_defs():
        t = existing.get(form["key"])
        if t is None:
            t = IntakeRequestType(
                org_id=org_id, key=f"form_{form['key']}", name=form["name"],
                workstream=form["group"], sort_order=form["sort_order"], form_key=form["key"],
            )
            t.fields = _field_rows(org_id, form)
            db.add(t)
        elif not _same_fields(t, form):
            t.fields.clear()
            db.flush()  # DELETEs before INSERTs, or the (type, key) unique index trips
            t.fields = _field_rows(org_id, form)
    db.flush()


def type_for_form(db: Session, org_id: str, form_key: str | None) -> IntakeRequestType | None:
    """The request type backing a wizard form, created on first use."""
    if not form_key or form_key not in {f["key"] for f in form_defs()}:
        return None
    ensure_agreement_types(db, org_id)
    return db.scalar(
        select(IntakeRequestType).where(
            IntakeRequestType.org_id == org_id, IntakeRequestType.form_key == form_key
        )
    )
