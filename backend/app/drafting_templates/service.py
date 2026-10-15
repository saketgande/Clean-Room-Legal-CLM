"""The drafting templates: what each one is, its text, its pinned playbook, and saving a new wording.

Drafting fills a template with Python ``str.format`` (``intake.drafting.
render_document``), so a saved template is checked here first: every
``{placeholder}`` must be one drafting fills, and every other brace doubled.
A template that wouldn't fill is refused at save time instead of breaking the
next request that drafts from it.

A template type also records the playbook that reviews it (the pin, spec 006
D-11): its checks and every contract drafted from it use that playbook, so a
later, better-named playbook never takes over. A shipped type has no row until
it is first used (``ensure_pinned``); reading never writes one.

Every change to the wording commits (with its audit row) and only then asks
``checks`` to review the new wording. ``checks`` is imported late because it
imports this module, and it takes the template lock and then the audit lock, so
nothing here may hold an audit write while calling it (CLAUDE.md, audit-lock freeze).
"""

from __future__ import annotations

import hashlib
import importlib
import logging
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.database import new_uuid, utcnow
from app.core.enums import PlaybookStatus
from app.drafting_templates.models import (
    DraftingTemplateCheck,
    DraftingTemplateType,
    DraftingTemplateVersion,
)
from app.drafting_templates.schemas import AppliedFindingIn
from app.intake.agreement_forms import form_def
from app.playbooks.library import CONTRACT_TYPE_OF_FORM, CONTRACT_TYPE_OF_KIND
from app.playbooks.models import Playbook, PlaybookVersion

logger = logging.getLogger(__name__)

_DEFAULTS = Path(__file__).parent / "defaults"


def contract_type_of(form_key: str, agreement_kind: str | None) -> str | None:
    """The contract type stored on a draft of this form and kind, from the playbook library's
    maps (the one place that says which playbook name a kind is reviewed by)."""
    if form_key == "new_agreement":
        return CONTRACT_TYPE_OF_KIND.get(agreement_kind or "")
    return CONTRACT_TYPE_OF_FORM.get(form_key)


def _shipped(name: str, label: str, form_key: str, agreement_kind: str | None = None) -> dict:
    return {"name": name, "label": label, "form_key": form_key, "agreement_kind": agreement_kind,
            "contract_type": contract_type_of(form_key, agreement_kind)}


# key -> what it drafts. contract_type is stored on the drafted Contract and is
# what picks the playbook that reviews it for a type that is not pinned yet, so each
# template is written to pass exactly that playbook (tests/test_drafting_templates.py
# checks it literally).
TEMPLATES: dict[str, dict] = {
    "nda": _shipped("Non-Disclosure Agreement", "Mutual NDA", "new_agreement", "NDA"),
    "msa": _shipped("Master Services Agreement", "Master Services Agreement", "new_agreement",
                    "Services (MSA)"),
    "consultancy": _shipped("Consultancy Agreement", "Consultancy Agreement", "new_agreement",
                            "Consultancy"),
    "sow": _shipped("Statement of Work", "Statement of Work", "sow"),
    "vendor": _shipped("Vendor Agreement", "Vendor Agreement", "new_agreement", "Buying from a vendor"),
    "saas": _shipped("Software / SaaS Agreement", "Software / SaaS Agreement", "new_agreement",
                     "Software or SaaS"),
    "dpa": _shipped("Data Processing Agreement", "Data Processing Agreement", "dpa"),
}

# Everything drafting fills (intake.drafting.template_values), with an example
# so the editor can show what each one turns into, and a short plain name (FR-62).
PLACEHOLDERS: list[dict] = [
    {"name": "company", "label": "Our entity", "about": "Our contracting entity",
     "sample": "Aegis Pharma Ltd"},
    {"name": "counterparty", "label": "The other party", "about": "The other party",
     "sample": "Globex Corporation"},
    {"name": "effective_date", "label": "Effective date",
     "about": "Start date from the request (else the drafting date)", "sample": "2026-10-01"},
    {"name": "governing_law", "label": "Governing law", "about": "Governing law from the request",
     "sample": "the State of Delaware"},
    {"name": "agreement_term", "label": "Term", "about": "Completes “commences on the Effective Date and …”",
     "sample": "continues until 2027-09-30"},
    {"name": "payment_days", "label": "Payment days", "about": "Payment terms in days", "sample": "45"},
    {"name": "value_sentence", "label": "Total value sentence",
     "about": "A sentence on the total value, or nothing when no value was given",
     "sample": " The total value of this Agreement shall not exceed INR 4,500,000.00."},
    {"name": "nda_kind_title", "label": "NDA kind (title)",
     "about": "NDA: “MUTUAL ” or “ONE-WAY ” before the title", "sample": "MUTUAL "},
    {"name": "nda_kind_word", "label": "NDA kind", "about": "NDA: “Mutual” or “One-Way”",
     "sample": "Mutual"},
    {"name": "purpose_phrase", "label": "Purpose", "about": "NDA: completes “the Parties wish to …”",
     "sample": "explore a potential business relationship"},
    {"name": "direction_recital", "label": "Who discloses to whom", "about": "NDA: who discloses to whom",
     "sample": "Each Party may act as both Disclosing Party and Receiving Party."},
    {"name": "term_clause", "label": "NDA term", "about": "NDA: completes “This Agreement …”",
     "sample": "continues for two (2) years"},
    {"name": "survival_clause", "label": "Survival period", "about": "NDA: completes “obligations shall …”",
     "sample": "continue for three (3) years"},
]
SAMPLE_VALUES = {p["name"]: p["sample"] for p in PLACEHOLDERS}
_LABELS = {p["name"]: p["label"] for p in PLACEHOLDERS}


def _known(key: str) -> dict:
    spec = TEMPLATES.get(key)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such template")
    return spec


@cache
def default_body(key: str) -> str:
    _known(key)
    return (_DEFAULTS / f"{key}.txt").read_text(encoding="utf-8")


def body_sha256(body: str) -> str:
    """The hash recorded with a check, and compared with the current wording, to tell
    whether the wording a check read is the wording now."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# --- The wording's braces ---------------------------------------------------------------------


def _scan_braces(body: str) -> Iterator[tuple[str, str, int]]:
    """Walk the braces. Yields ``("field", text, pos)`` for each ``{text}`` group and
    ``("unpaired", brace, pos)`` for a stray or unclosed brace; ``{{`` and ``}}`` are literal.
    A group is a usable field only when ``text`` is a bare known name: a conversion or format spec
    (``{company!r}``, ``{company:>10}``) is not one, because str.format would put quotes or
    padding into the contract. The Word path (``docx_fields``) and the page (``wordingProblems``)
    follow the same rule, so a wording is accepted or refused alike wherever it came from."""
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c in "{}" and body[i : i + 2] == c * 2:
            i += 2
        elif c == "}":
            yield "unpaired", "}", i
            i += 1
        elif c == "{":
            close = body.find("}", i + 1)
            if close == -1 or "{" in body[i + 1 : close]:
                yield "unpaired", "{", i
                i += 1
            else:
                yield "field", body[i + 1 : close], i
                i = close + 1
        else:
            i += 1


def check_body(body: str, *, place: str | None = None) -> None:
    """Refuse a template drafting couldn't fill, naming what's wrong and where: the line for
    text, or the Word place (``place="footer"``) for a Word file's paragraph."""
    if not body.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The template is empty.")
    for kind, text, pos in _scan_braces(body):
        if kind == "field" and text in SAMPLE_VALUES:
            continue
        where = (f"(line {body.count(chr(10), 0, pos) + 1})" if place is None
                 else f"(in the {place.replace('_', ' ')})")
        if kind == "unpaired":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"The braces don't pair up {where}. Write a literal brace as {{{{ or }}}}.",
            )
        shown = " ".join(text.split())
        shown = shown[:40] + "…" if len(shown) > 40 else shown
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown placeholder {{{shown}}} {where}. Use one from the list, or keep it as "
            f"ordinary text by writing it as {{{{{shown}}}}}.",
        )


def field_uses(body: str) -> list[dict]:
    """The fields a text wording uses, in order of first use, with how often (FR-36)."""
    counts: dict[str, int] = {}
    for kind, text, _ in _scan_braces(body):
        if kind == "field" and text in SAMPLE_VALUES:
            counts[text] = counts.get(text, 0) + 1
    return [{"name": n, "label": _LABELS[n], "place": "body", "count": c} for n, c in counts.items()]


def missing_parties(body: str) -> list[str]:
    """Which of ``company`` and ``counterparty`` the wording has no field for (FR-35)."""
    used = {text for kind, text, _ in _scan_braces(body) if kind == "field"}
    return [n for n in ("company", "counterparty") if n not in used]


def _missing_parties_message(missing: list[str]) -> str:
    who = " or ".join({"company": "our entity", "counterparty": "the other party"}[n] for n in missing)
    return (f"This wording has no field for {who}, so drafts will not name them. "
            "Confirm that this is intended to save it.")


def render_sample(body: str) -> str:
    check_body(body)
    return body.format(**SAMPLE_VALUES)


# --- Types: the catalog overlaid with the org's rows ------------------------------------------


def binding_of(form_key: str, agreement_kind: str | None) -> dict:
    """The request form (and kind of agreement) a type drafts, as the API shows it."""
    form_name = (form_def(form_key) or {}).get("name", form_key)
    return {"form_key": form_key, "form_name": form_name, "agreement_kind": agreement_kind,
            "label": f"{form_name} · {agreement_kind}" if agreement_kind else form_name}


@dataclass(frozen=True)
class TemplateType:
    key: str
    name: str
    label: str
    form_key: str
    agreement_kind: str | None
    shipped: bool
    active: bool
    row_id: str | None  # the drafting_template_type row; None until a shipped type is first pinned
    playbook_id: str | None  # the pin (D-11); None = not pinned yet

    @property
    def contract_type(self) -> str:
        return contract_type_of(self.form_key, self.agreement_kind) or self.name

    @property
    def binding(self) -> dict:
        return binding_of(self.form_key, self.agreement_kind)


def _type_of(key: str, row: DraftingTemplateType | None) -> TemplateType:
    spec = TEMPLATES.get(key)
    if spec is not None:
        # The code is the authority on a shipped type's facts; a row only adds the org's choices.
        return TemplateType(key, spec["name"], spec["label"], spec["form_key"], spec["agreement_kind"],
                            True, row is None or row.status == "active",
                            row.id if row else None, row.playbook_id if row else None)
    assert row is not None  # callers only pass a key that is shipped or has a row
    return TemplateType(key, row.name, row.label, row.form_key, row.agreement_kind, False,
                        row.status == "active", row.id, row.playbook_id)


def _type_rows(db: Session, org_id: str) -> dict[str, DraftingTemplateType]:
    # populate_existing: the pin is written with a Core upsert, which the identity map can't see.
    rows = db.scalars(
        select(DraftingTemplateType)
        .where(DraftingTemplateType.org_id == org_id)
        .order_by(DraftingTemplateType.created_at)
        .execution_options(populate_existing=True)
    ).all()
    return {r.key: r for r in rows}


def list_types(db: Session, org_id: str, *, include_retired: bool = False) -> list[TemplateType]:
    """The shipped catalog overlaid with this org's rows, then the org's own types. Reads only."""
    rows = _type_rows(db, org_id)
    types = [_type_of(k, rows.get(k)) for k in TEMPLATES]
    types += [_type_of(k, r) for k, r in rows.items() if k not in TEMPLATES]
    return [t for t in types if include_retired or t.active]


def get_type(db: Session, org_id: str, key: str) -> TemplateType:
    """One type, any status. 404 for a key that is neither shipped nor this org's."""
    row = db.scalar(
        select(DraftingTemplateType)
        .where(DraftingTemplateType.org_id == org_id, DraftingTemplateType.key == key)
        .execution_options(populate_existing=True)
    )
    if row is None and key not in TEMPLATES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such template")
    return _type_of(key, row)


# --- The wording in effect --------------------------------------------------------------------


@dataclass(frozen=True)
class Wording:
    version: int  # 0 = the shipped wording, never saved
    sha256: str
    body: str
    row: DraftingTemplateVersion | None


def _latest_row(db: Session, org_id: str, key: str) -> DraftingTemplateVersion | None:
    return db.scalar(
        select(DraftingTemplateVersion)
        .where(DraftingTemplateVersion.org_id == org_id, DraftingTemplateVersion.key == key)
        .order_by(DraftingTemplateVersion.version.desc())
        .limit(1)
    )


def current_wording(db: Session, org_id: str, ttype: TemplateType) -> Wording:
    """What drafting uses now: the newest version's body, else the shipped default. The
    shipped default is read (and hashed) live, so a release that edits ``defaults/<key>.txt``
    changes version 0's hash and turns its last check Out of date."""
    row = _latest_row(db, org_id, ttype.key)
    body = row.body if row is not None and row.body is not None else default_body(ttype.key)
    return Wording(row.version if row is not None else 0, body_sha256(body), body, row)


# --- The pin (D-11) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reviewing:
    playbook: Playbook | None
    pinned: bool  # false = today's name match, not locked yet
    available: bool  # false = none, deleted, or without a published version
    version: PlaybookVersion | None = None  # the playbook's latest published version


def latest_published_version(db: Session, org_id: str, playbook_id: str) -> PlaybookVersion | None:
    return db.scalar(
        select(PlaybookVersion)
        .where(
            PlaybookVersion.org_id == org_id,
            PlaybookVersion.playbook_id == playbook_id,
            PlaybookVersion.status == PlaybookStatus.PUBLISHED,
        )
        .order_by(PlaybookVersion.version_number.desc())
        .limit(1)
    )


def _name_match(db: Session, org_id: str, contract_type: str | None) -> Playbook | None:
    """Today's name match: the playbook whose name carries the contract type's words."""
    from app.playbooks.service import PlaybooksService

    return PlaybooksService(db).pick_playbook_for_contract(org_id=org_id, contract_type=contract_type)


def reviewing_playbook(db: Session, org_id: str, ttype: TemplateType) -> Reviewing:
    """The playbook that reviews this type. Read only: list, detail and status call it and
    must never write a row."""
    if ttype.playbook_id:
        pb = db.get(Playbook, ttype.playbook_id)
        if pb is None or pb.org_id != org_id:
            return Reviewing(None, True, False)
        version = latest_published_version(db, org_id, pb.id) if pb.deleted_at is None else None
        return Reviewing(pb, True, version is not None, version)
    pb = _name_match(db, org_id, ttype.contract_type)
    if pb is None:
        return Reviewing(None, False, False)
    version = latest_published_version(db, org_id, pb.id)
    return Reviewing(pb, False, version is not None, version)


def _upsert_pin(db: Session, *, org_id: str, ttype: TemplateType, playbook_id: str,
                actor_user_id: str | None, only_if_unpinned: bool) -> str | None:
    """Write the type's pin, creating the row of a shipped type that has none. With
    ``only_if_unpinned`` it never overwrites a pin, so of two first uses at once one wins; the
    row id comes back only when this call wrote it."""
    now = utcnow()
    stmt = pg_insert(DraftingTemplateType).values(
        id=new_uuid(), org_id=org_id, key=ttype.key, name=ttype.name, label=ttype.label,
        form_key=ttype.form_key, agreement_kind=ttype.agreement_kind, shipped=ttype.shipped,
        status="active", playbook_id=playbook_id, created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id, created_at=now, updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["org_id", "key"],
        set_={"playbook_id": stmt.excluded.playbook_id, "updated_by_user_id": actor_user_id,
              "updated_at": now},
        where=DraftingTemplateType.playbook_id.is_(None) if only_if_unpinned else None,
    ).returning(DraftingTemplateType.id)
    return db.execute(stmt).scalar_one_or_none()


def ensure_pinned(db: Session, *, org_id: str, ttype: TemplateType, actor_user_id: str | None,
                  commit: bool) -> Playbook | None:
    """The only lazy pin. A pinned type returns its playbook (possibly deleted or unpublished:
    the caller decides). An unpinned one is pinned to today's name match, audited as the
    system on behalf of ``actor_user_id``; with no match nothing is pinned and None comes back.
    ``commit=False`` leaves the commit to a caller that holds a lock (``checks.request_check``)."""
    if ttype.playbook_id:
        return reviewing_playbook(db, org_id, ttype).playbook
    match = _name_match(db, org_id, ttype.contract_type)
    if match is None:
        return None
    row_id = _upsert_pin(db, org_id=org_id, ttype=ttype, playbook_id=match.id,
                         actor_user_id=actor_user_id, only_if_unpinned=True)
    if row_id is not None:
        write_audit_log(
            db, action="drafting_template.playbook_pinned", resource_type="drafting_template",
            resource_id=row_id, org_id=org_id, actor_user_id=None,
            after={"key": ttype.key, "playbook_id": match.id, "playbook_name": match.name,
                   "how": "matched by name at first use"},
            metadata={"actor": "system", "on_behalf_of_user_id": actor_user_id},
        )
        if commit:
            db.commit()
    # Re-read: if a concurrent first use pinned another playbook first, that one stands.
    pinned_id = db.scalar(
        select(DraftingTemplateType.playbook_id)
        .where(DraftingTemplateType.org_id == org_id, DraftingTemplateType.key == ttype.key)
    )
    return db.get(Playbook, pinned_id) if pinned_id else None


def _checks_module():
    """``checks`` is imported late: it imports this module. Looked up by name each call so a
    test can stand in for it."""
    return importlib.import_module("app.drafting_templates.checks")


def _clean_note(note: str | None) -> str | None:
    return (note or "").strip()[:300] or None


def _stamp(moment: datetime) -> str:
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _playbook_ref(rev: Reviewing) -> dict | None:
    if rev.playbook is None:
        return None
    v = rev.version
    return {"id": rev.playbook.id, "name": rev.playbook.name, "version_id": v.id if v else None,
            "version_number": v.version_number if v else None, "pinned": rev.pinned,
            "available": rev.available}


class DraftingTemplateService:
    def __init__(self, db: Session):
        self.db = db

    def _latest(self, org_id: str, key: str) -> DraftingTemplateVersion | None:
        return _latest_row(self.db, org_id, key)

    def body_for(self, *, org_id: str, key: str) -> str:
        """The text drafting uses: the org's newest version, else the default."""
        row = self._latest(org_id, key)
        return row.body if row is not None and row.body is not None else default_body(key)

    def _user_name(self, user_id: str | None) -> str | None:
        u = self.db.get(User, user_id) if user_id else None
        return (u.full_name or u.email) if u is not None else None

    def _summary(self, org_id: str, ttype: TemplateType, wording: Wording) -> dict:
        row = wording.row
        return {
            "key": ttype.key, "name": ttype.name, "label": ttype.label,
            "contract_type": ttype.contract_type, "binding": ttype.binding,
            "playbook": _playbook_ref(reviewing_playbook(self.db, org_id, ttype)),
            "shipped": ttype.shipped, "active": ttype.active,
            "source": "default" if row is None or row.body is None else "text",
            "version": wording.version,
            "original_filename": None,
            "updated_at": row.created_at.isoformat() if row is not None else None,
            "updated_by": self._user_name(row.created_by_user_id) if row is not None else None,
            "editable": True,
            "status": _checks_module().template_status(self.db, org_id=org_id, ttype=ttype),
        }

    def list(self, *, org_id: str, include_retired: bool = False) -> list[dict]:
        return [self._summary(org_id, t, current_wording(self.db, org_id, t))
                for t in list_types(self.db, org_id, include_retired=include_retired)]

    def get(self, *, org_id: str, key: str) -> dict:
        ttype = get_type(self.db, org_id, key)
        wording = current_wording(self.db, org_id, ttype)
        return {
            **self._summary(org_id, ttype, wording),
            "body": wording.body,
            "default_body": default_body(key) if ttype.shipped else None,
            "placeholders": PLACEHOLDERS,
            "fields": field_uses(wording.body),
            "original_file": None,
        }

    def versions(self, *, org_id: str, key: str) -> list[dict]:
        """Every version, newest first, each with the saved-wording checks recorded against it.
        A shipped type ends with a synthetic version 0: the shipped wording."""
        ttype = get_type(self.db, org_id, key)
        rows = self.db.scalars(
            select(DraftingTemplateVersion)
            .where(DraftingTemplateVersion.org_id == org_id, DraftingTemplateVersion.key == key)
            .order_by(DraftingTemplateVersion.version.desc())
        ).all()
        checks = _checks_module()
        by_version: dict[int, list[dict]] = defaultdict(list)
        for c in self.db.scalars(
            select(DraftingTemplateCheck)
            .where(DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
                   DraftingTemplateCheck.unsaved.is_(False))
            .order_by(DraftingTemplateCheck.created_at.desc())
        ):
            by_version[c.template_version].append(checks.serialize_check(self.db, c, full=False))
        out = [{
            "version": r.version, "note": r.note,
            "source": "default" if r.body is None else "text", "is_default": r.body is None,
            "body": r.body if r.body is not None else default_body(key),
            "created_at": r.created_at.isoformat(), "created_by": self._user_name(r.created_by_user_id),
            "original_file": None, "applied_findings": r.applied_findings or [],
            "restored_from_version": r.restored_from_version,
            "ocr_confirmed_by": None, "ocr_confirmed_at": None,
            "checks": by_version[r.version],
        } for r in rows]
        if ttype.shipped:
            out.append({
                "version": 0, "note": None, "source": "default", "is_default": True,
                "body": default_body(key), "created_at": None, "created_by": None,
                "original_file": None, "applied_findings": [], "restored_from_version": None,
                "ocr_confirmed_by": None, "ocr_confirmed_at": None, "checks": by_version[0],
            })
        return out

    def options(self, *, org_id: str) -> dict:
        """What the pickers offer: the published playbooks of this org (Change playbook, create
        form), and each request form or kind a type can draft, with the type that holds it."""
        db = self.db
        held = {(t.form_key, t.agreement_kind): t for t in list_types(db, org_id)}
        bindings = []
        for form_key, kind in [("new_agreement", k) for k in CONTRACT_TYPE_OF_KIND] + [
            (f, None) for f in CONTRACT_TYPE_OF_FORM
        ]:
            holder = held.get((form_key, kind))
            contract_type = contract_type_of(form_key, kind)
            match = _name_match(db, org_id, contract_type)
            bindings.append({
                "form_key": form_key, "agreement_kind": kind,
                "label": binding_of(form_key, kind)["label"],
                "contract_type": contract_type,
                "held_by": {"key": holder.key, "name": holder.name} if holder else None,
                "suggested_playbook": {"id": match.id, "name": match.name} if match else None,
            })
        rows = db.execute(
            select(Playbook, func.max(PlaybookVersion.version_number))
            .join(PlaybookVersion, PlaybookVersion.playbook_id == Playbook.id)
            .where(
                Playbook.org_id == org_id, Playbook.deleted_at.is_(None),
                PlaybookVersion.org_id == org_id, PlaybookVersion.status == PlaybookStatus.PUBLISHED,
            )
            .group_by(Playbook.id)
            .order_by(func.lower(Playbook.name), Playbook.id)
        ).all()
        return {
            "bindings": bindings,
            "playbooks": [{"id": p.id, "name": p.name, "version_number": n} for p, n in rows],
        }

    # --- Changing the wording ---------------------------------------------------------------

    def _stale(self, latest: DraftingTemplateVersion) -> HTTPException:
        return HTTPException(
            status.HTTP_409_CONFLICT,
            f"{self._user_name(latest.created_by_user_id) or 'Someone'} saved version {latest.version} "
            f"of this template at {_stamp(latest.created_at)} UTC after you opened it. "
            "Your text is still in the editor: compare it with theirs, then save again.",
        )

    def _check_version(self, org_id: str, key: str, expected_version: int | None) -> None:
        """FR-59: refuse a change made on top of a version that is no longer the newest.
        ``None`` skips it (service-level callers); the routes always pass it."""
        latest = self._latest(org_id, key)
        if expected_version is not None and latest is not None and expected_version != latest.version:
            raise self._stale(latest)

    def _next_version(self, org_id: str, key: str) -> int:
        return (self.db.scalar(
            select(func.max(DraftingTemplateVersion.version))
            .where(DraftingTemplateVersion.org_id == org_id, DraftingTemplateVersion.key == key)
        ) or 0) + 1

    def _applied_findings(self, org_id: str, key: str,
                          items: Sequence[Mapping[str, Any] | AppliedFindingIn]) -> list[dict]:
        """The suggestions this save takes in (FR-25), each looked up in a check of this
        template in this org: a made-up or foreign pair is refused, not stored."""
        out: list[dict] = []
        for item in items:
            pair = item if isinstance(item, Mapping) else item.model_dump()
            check = self.db.get(DraftingTemplateCheck, pair["check_id"])
            finding = None
            if check is not None and check.org_id == org_id and check.template_key == key:
                finding = next((f for f in check.findings or [] if f.get("index") == pair["finding_index"]), None)
            if finding is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                    "That suggestion is not from a check of this template.")
            if not any(o["check_id"] == check.id and o["finding_index"] == pair["finding_index"] for o in out):
                out.append({"check_id": check.id, "finding_index": pair["finding_index"],
                            "clause_type": finding.get("clause_type"), "severity": finding.get("severity")})
        return out

    def _add(self, *, actor: User, key: str, body: str | None, note: str | None, action: str,
             request_id: str | None, applied_findings: list[dict] | None = None,
             restored_from_version: int | None = None) -> DraftingTemplateVersion:
        """Write the next version and its audit row, and commit. The version number is unique per
        template in the database, so of two saves made on the same version the second is refused
        here (409) instead of overwriting the first."""
        db = self.db
        org_id = actor.org_id
        row = DraftingTemplateVersion(
            org_id=org_id, key=key, version=self._next_version(org_id, key), body=body,
            note=_clean_note(note), applied_findings=applied_findings or None,
            restored_from_version=restored_from_version,
            created_by_user_id=actor.id, updated_by_user_id=actor.id,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            raise self._stale(self._latest(org_id, key)) from None
        after: dict[str, Any] = {
            "key": key, "version": row.version, "note": row.note,
            "source": "default" if body is None else "text",
            "wording_sha256": body_sha256(body if body is not None else default_body(key)),
            "applied_findings": applied_findings or [], "default": body is None,
        }
        if restored_from_version is not None:
            after["restored_from_version"] = restored_from_version
        write_audit_log(
            db, action=action, resource_type="drafting_template", resource_id=row.id,
            org_id=org_id, actor_user_id=actor.id, request_id=request_id, after=after,
        )
        db.commit()
        return row

    def _request_check(self, org_id: str, key: str, trigger: str, actor_user_id: str):
        """Ask for the check of the wording just committed (FR-13). The change is already saved
        and audited, so a failure here is logged and the answer says no check started; it never
        turns a saved wording into an error."""
        try:
            return _checks_module().request_check(
                self.db, org_id=org_id, key=key, trigger=trigger, actor_user_id=actor_user_id)
        except Exception:
            logger.exception("could not request the template check key=%s trigger=%s", key, trigger)
            self.db.rollback()
            return None

    def _result(self, org_id: str, key: str, *, changed: bool, check) -> dict:
        return {
            "template": self.get(org_id=org_id, key=key),
            "changed": changed,
            "check": _checks_module().serialize_check(self.db, check, full=False) if check is not None else None,
        }

    def save(self, *, actor: User, key: str, body: str, note: str | None,
             expected_version: int | None = None,
             applied_findings: Sequence[Mapping[str, Any] | AppliedFindingIn] = (),
             acknowledge_missing_parties: bool = False, request_id: str | None = None) -> dict:
        org_id = actor.org_id
        ttype = get_type(self.db, org_id, key)
        self._check_version(org_id, key, expected_version)
        check_body(body)
        missing = missing_parties(body)
        if missing and not acknowledge_missing_parties:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _missing_parties_message(missing))
        applied = self._applied_findings(org_id, key, applied_findings)
        if body == current_wording(self.db, org_id, ttype).body:
            return self._result(org_id, key, changed=False, check=None)  # nothing changed: no new version
        # Saving the shipped text verbatim is a reset, so it keeps following the default.
        stored = None if body == default_body(key) else body
        self._add(actor=actor, key=key, body=stored, note=note, action="drafting_template.saved",
                  request_id=request_id, applied_findings=applied)
        check = self._request_check(org_id, key, "saved", actor.id)
        return self._result(org_id, key, changed=True, check=check)

    def reset(self, *, actor: User, key: str, note: str | None, expected_version: int | None = None,
              request_id: str | None = None) -> dict:
        org_id = actor.org_id
        get_type(self.db, org_id, key)
        self._check_version(org_id, key, expected_version)
        row = self._latest(org_id, key)
        if row is None or row.body is None:
            return self._result(org_id, key, changed=False, check=None)  # already on the shipped wording
        self._add(actor=actor, key=key, body=None, note=note or "Back to the default",
                  action="drafting_template.reset", request_id=request_id)
        check = self._request_check(org_id, key, "reset", actor.id)
        return self._result(org_id, key, changed=True, check=check)

    def restore(self, *, actor: User, key: str, version: int, note: str | None = None,
                expected_version: int | None = None, request_id: str | None = None) -> dict:
        """Load an earlier version back as a new latest version; the earlier ones stay as they were.
        Version 0 is the shipped wording, which is a reset."""
        org_id = actor.org_id
        if version == 0:
            return self.reset(actor=actor, key=key, note=note, expected_version=expected_version,
                              request_id=request_id)
        get_type(self.db, org_id, key)
        source = self.db.scalar(
            select(DraftingTemplateVersion).where(
                DraftingTemplateVersion.org_id == org_id, DraftingTemplateVersion.key == key,
                DraftingTemplateVersion.version == version)
        )
        if source is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version of this template.")
        self._check_version(org_id, key, expected_version)
        self._add(actor=actor, key=key, body=source.body, note=note or f"Restored version {version}",
                  action="drafting_template.restored", request_id=request_id,
                  restored_from_version=version)
        check = self._request_check(org_id, key, "restored", actor.id)
        return self._result(org_id, key, changed=True, check=check)

    def set_playbook(self, *, actor: User, key: str, playbook_id: str, note: str | None = None,
                     request_id: str | None = None) -> dict:
        """Pin another playbook to this type (D-11). Contracts already drafted keep the one they
        were drafted with (FR-45); a check against the new pin starts."""
        db = self.db
        org_id = actor.org_id
        ttype = get_type(db, org_id, key)
        chosen = db.get(Playbook, playbook_id)
        if (chosen is None or chosen.org_id != org_id or chosen.deleted_at is not None
                or latest_published_version(db, org_id, chosen.id) is None):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "Choose one of this organisation's published playbooks.")
        if ttype.playbook_id == chosen.id:
            return self._result(org_id, key, changed=False, check=None)
        before = reviewing_playbook(db, org_id, ttype).playbook  # what reviewed it until now
        row_id = _upsert_pin(db, org_id=org_id, ttype=ttype, playbook_id=chosen.id,
                             actor_user_id=actor.id, only_if_unpinned=False)
        write_audit_log(
            db, action="drafting_template.playbook_changed", resource_type="drafting_template",
            resource_id=row_id, org_id=org_id, actor_user_id=actor.id, request_id=request_id,
            before={"playbook_id": before.id if before else None,
                    "playbook_name": before.name if before else None},
            after={"playbook_id": chosen.id, "playbook_name": chosen.name, "note": _clean_note(note)},
        )
        db.commit()
        check = self._request_check(org_id, key, "playbook_changed", actor.id)
        return self._result(org_id, key, changed=True, check=check)
