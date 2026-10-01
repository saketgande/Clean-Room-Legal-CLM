"""The drafting templates: what each one is, its text, and saving a new wording.

Drafting fills a template with Python ``str.format`` (``intake.drafting.
render_document``), so a saved template is checked here first: every
``{placeholder}`` must be one drafting fills, and every other brace doubled.
A template that wouldn't fill is refused at save time instead of breaking the
next request that drafts from it.
"""

from __future__ import annotations

import string
from functools import cache
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log
from app.drafting_templates.models import DraftingTemplateVersion

_DEFAULTS = Path(__file__).parent / "defaults"

# key -> what it drafts. contract_type is stored on the drafted Contract and is
# what picks the playbook that reviews it, so each template is written to pass
# exactly that playbook (tests/test_drafting_templates.py checks it literally).
TEMPLATES: dict[str, dict] = {
    "nda": {"name": "Non-Disclosure Agreement", "contract_type": "NDA", "label": "Mutual NDA"},
    "msa": {"name": "Master Services Agreement", "contract_type": "MSA", "label": "Master Services Agreement"},
    "consultancy": {"name": "Consultancy Agreement", "contract_type": "Consultancy Agreement",
                    "label": "Consultancy Agreement"},
    "sow": {"name": "Statement of Work", "contract_type": "Statement of Work", "label": "Statement of Work"},
    "vendor": {"name": "Vendor Agreement", "contract_type": "Vendor Agreement", "label": "Vendor Agreement"},
    "saas": {"name": "Software / SaaS Agreement", "contract_type": "Software / SaaS Agreement",
             "label": "Software / SaaS Agreement"},
    "dpa": {"name": "Data Processing Agreement", "contract_type": "DPA", "label": "Data Processing Agreement"},
}

# Everything drafting fills (intake.drafting.template_values), with an example
# so the editor can show what each one turns into.
PLACEHOLDERS: list[dict] = [
    {"name": "company", "about": "Our contracting entity", "sample": "Aegis Pharma Ltd"},
    {"name": "counterparty", "about": "The other party", "sample": "Globex Corporation"},
    {"name": "effective_date", "about": "Start date from the request (else the drafting date)",
     "sample": "2026-10-01"},
    {"name": "governing_law", "about": "Governing law from the request", "sample": "the State of Delaware"},
    {"name": "agreement_term", "about": "Completes “commences on the Effective Date and …”",
     "sample": "continues until 2027-09-30"},
    {"name": "payment_days", "about": "Payment terms in days", "sample": "45"},
    {"name": "value_sentence", "about": "A sentence on the total value, or nothing when no value was given",
     "sample": " The total value of this Agreement shall not exceed INR 4,500,000.00."},
    {"name": "nda_kind_title", "about": "NDA: “MUTUAL ” or “ONE-WAY ” before the title", "sample": "MUTUAL "},
    {"name": "nda_kind_word", "about": "NDA: “Mutual” or “One-Way”", "sample": "Mutual"},
    {"name": "purpose_phrase", "about": "NDA: completes “the Parties wish to …”",
     "sample": "explore a potential business relationship"},
    {"name": "direction_recital", "about": "NDA: who discloses to whom",
     "sample": "Each Party may act as both Disclosing Party and Receiving Party."},
    {"name": "term_clause", "about": "NDA: completes “This Agreement …”", "sample": "continues for two (2) years"},
    {"name": "survival_clause", "about": "NDA: completes “obligations shall …”",
     "sample": "continue for three (3) years"},
]
SAMPLE_VALUES = {p["name"]: p["sample"] for p in PLACEHOLDERS}


def _known(key: str) -> dict:
    spec = TEMPLATES.get(key)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such template")
    return spec


@cache
def default_body(key: str) -> str:
    _known(key)
    return (_DEFAULTS / f"{key}.txt").read_text(encoding="utf-8")


def check_body(body: str) -> None:
    """Refuse a template drafting couldn't fill, naming what's wrong."""
    if not body.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The template is empty.")
    try:
        names = {f for _, f, _, _ in string.Formatter().parse(body) if f is not None}
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The braces don't pair up ({exc}). Write a literal brace as {{{{ or }}}}.",
        ) from exc
    unknown = sorted(n for n in names if n not in SAMPLE_VALUES)
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Unknown placeholder" + ("s " if len(unknown) > 1 else " ")
            + ", ".join("{" + n + "}" for n in unknown)
            + ". Use one from the list, or write a literal brace as {{ or }}.",
        )


def render_sample(body: str) -> str:
    check_body(body)
    return body.format(**SAMPLE_VALUES)


class DraftingTemplateService:
    def __init__(self, db: Session):
        self.db = db

    def _latest(self, org_id: str, key: str) -> DraftingTemplateVersion | None:
        return self.db.scalar(
            select(DraftingTemplateVersion)
            .where(DraftingTemplateVersion.org_id == org_id, DraftingTemplateVersion.key == key)
            .order_by(DraftingTemplateVersion.version.desc())
            .limit(1)
        )

    def body_for(self, *, org_id: str, key: str) -> str:
        """The text drafting uses: the org's newest version, else the default."""
        row = self._latest(org_id, key)
        return row.body if row is not None and row.body is not None else default_body(key)

    def _playbook_name(self, org_id: str, contract_type: str) -> str | None:
        from app.playbooks.service import PlaybooksService

        pb = PlaybooksService(self.db).pick_playbook_for_contract(org_id=org_id, contract_type=contract_type)
        return pb.name if pb is not None else None

    def _summary(self, org_id: str, key: str) -> dict:
        spec = TEMPLATES[key]
        row = self._latest(org_id, key)
        return {
            "key": key, "name": spec["name"], "contract_type": spec["contract_type"],
            "playbook": self._playbook_name(org_id, spec["contract_type"]),
            "customized": row is not None and row.body is not None,
            "version": row.version if row is not None else 0,
            "updated_at": row.created_at.isoformat() if row is not None else None,
            "updated_by": self._user_name(row.created_by_user_id) if row is not None else None,
        }

    def _user_name(self, user_id: str | None) -> str | None:
        u = self.db.get(User, user_id) if user_id else None
        return (u.full_name or u.email) if u is not None else None

    def list(self, *, org_id: str) -> list[dict]:
        return [self._summary(org_id, key) for key in TEMPLATES]

    def get(self, *, org_id: str, key: str) -> dict:
        _known(key)
        return {
            **self._summary(org_id, key),
            "body": self.body_for(org_id=org_id, key=key),
            "default_body": default_body(key),
            "placeholders": PLACEHOLDERS,
        }

    def versions(self, *, org_id: str, key: str) -> list[dict]:
        _known(key)
        rows = self.db.scalars(
            select(DraftingTemplateVersion)
            .where(DraftingTemplateVersion.org_id == org_id, DraftingTemplateVersion.key == key)
            .order_by(DraftingTemplateVersion.version.desc())
        ).all()
        return [{
            "version": r.version, "note": r.note, "is_default": r.body is None,
            "body": r.body if r.body is not None else default_body(key),
            "created_at": r.created_at.isoformat(), "created_by": self._user_name(r.created_by_user_id),
        } for r in rows]

    def _add(self, *, actor: User, key: str, body: str | None, note: str | None, action: str,
             request_id: str | None) -> dict:
        db = self.db
        version = (db.scalar(
            select(func.max(DraftingTemplateVersion.version))
            .where(DraftingTemplateVersion.org_id == actor.org_id, DraftingTemplateVersion.key == key)
        ) or 0) + 1
        row = DraftingTemplateVersion(
            org_id=actor.org_id, key=key, version=version, body=body,
            note=(note or "").strip()[:300] or None,
            created_by_user_id=actor.id, updated_by_user_id=actor.id,
        )
        db.add(row)
        db.flush()
        write_audit_log(
            db, action=action, resource_type="drafting_template", resource_id=row.id,
            org_id=actor.org_id, actor_user_id=actor.id, request_id=request_id,
            after={"key": key, "version": version, "note": row.note, "default": body is None},
        )
        db.commit()
        return self.get(org_id=actor.org_id, key=key)

    def save(self, *, actor: User, key: str, body: str, note: str | None,
             request_id: str | None = None) -> dict:
        _known(key)
        check_body(body)
        if body == self.body_for(org_id=actor.org_id, key=key):
            return self.get(org_id=actor.org_id, key=key)  # nothing changed: no new version
        # Saving the shipped text verbatim is a reset, so it keeps following the default.
        stored = None if body == default_body(key) else body
        return self._add(actor=actor, key=key, body=stored, note=note,
                         action="drafting_template.saved", request_id=request_id)

    def reset(self, *, actor: User, key: str, note: str | None, request_id: str | None = None) -> dict:
        _known(key)
        row = self._latest(actor.org_id, key)
        if row is None or row.body is None:
            return self.get(org_id=actor.org_id, key=key)
        return self._add(actor=actor, key=key, body=None, note=note or "Back to the default",
                         action="drafting_template.reset", request_id=request_id)
