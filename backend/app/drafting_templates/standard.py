"""Rolling the 27 Oct 2026 standard (spec 014, revision "014") out to organisations that already exist.

New organisations get the new wording and rules from the code (version 0 is read live; the playbook
library seeds the new rules). Existing ones need three things this module does:

* a ``body=NULL`` marker version on each template that still follows the shipped wording, with a
  plain note of what changed (an organisation's own saved wording is never touched, FR-54);
* a new published playbook version holding the standard rules, but only when the organisation's rules
  still equal what the review read (``BASELINE_ROWS``), so nothing anyone edited is overwritten;
* "older than the standard" status for both, and a person-driven ``adopt`` for a playbook that was
  edited (the migration left it alone and flagged it).

``apply_standard_update`` / ``revert_standard_update`` are the bodies of migration 0077 and flush
only. ``publish_standard`` and ``adopt`` write their audit rows in the same transaction as the
change (CLAUDE.md audit-lock rule); ``adopt`` commits and only then asks for the template check.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.enums import PlaybookStatus
from app.drafting_templates import service as svc
from app.drafting_templates.models import DraftingTemplateVersion
from app.drafting_templates.service import Wording, get_type, reviewing_playbook
from app.playbooks import library
from app.playbooks.models import Playbook, PlaybookRule, PlaybookVersion

logger = logging.getLogger(__name__)

REVISION = "014"
UPDATED_ON = date(2026, 10, 27)
# ponytail: a version saved before this instant is "older than the standard"; per-org apply time if it matters
EFFECTIVE = datetime(2026, 10, 27, tzinfo=UTC)
CORE_FIELDS = ("clause_type", "rule_type", "risk_level", "preferred_position", "fallback_position",
               "prohibited_language", "required_language", "rationale")
# Everything a person can edit in the playbook editor. The review snapshot only read CORE_FIELDS, so a
# rule set that differs from the standard in only the other four must not pass as untouched.
FIELDS = CORE_FIELDS + ("approval_required", "escalation_role", "negotiation_guidance", "sample_clause")

_SYSTEM = {"actor": "system", "revision": REVISION}
_NO_PLAYBOOK = "No playbook reviews this template, so there are no rules to update."
_NO_STANDARD = "This template has no standard rules."


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split())


def _field(rule: Any, name: str) -> Any:
    return rule.get(name) if isinstance(rule, dict) else getattr(rule, name, None)


def row_hash(rule: Any, fields: tuple[str, ...] = FIELDS) -> str:
    """One rule on the editable fields; whitespace and None / empty / False are ignored."""
    return hashlib.sha256(json.dumps([_norm(_field(rule, f)) for f in fields]).encode()).hexdigest()[:16]


def fingerprint(rules, fields: tuple[str, ...] = FIELDS) -> str:
    """A rule set, whatever its order."""
    return hashlib.sha256(json.dumps(sorted(row_hash(r, fields) for r in rules)).encode()).hexdigest()


# The rule sets the review of 2026-10-27 read from the organisation's database (specs/014-template-
# playbook-alignment/review/playbooks.json), as one hash per rule. The specs folder is not in the
# container, so the code never reads it. The snapshot holds only CORE_FIELDS, so every set but the NDA is
# hashed on all of FIELDS from the starter library as it stood when the review ran (it matched the
# snapshot row for row). CUSTOMER is the Customer Agreement Playbook, which this
# feature leaves alone (AC-6).
# ponytail: the NDA playbook is the organisation's own, so its approval / escalation / guidance / sample text
# is not knowable here; it is compared on CORE_FIELDS only (an edit to just those four is not seen). Upgrade
# = a snapshot of those columns from the organisation's database.
BASELINE_FIELDS: dict[str, tuple[str, ...]] = {"nda": CORE_FIELDS}
_BASELINE_ROWS: dict[str, dict[str, str]] = {
    "nda": {
        "compelled_disclosure_and_legal_process": "ae95b62e49635476",
        "definition_of_confidential_information": "11d7128c42813fb2",
        "exclusions_from_confidential_information": "7c18b4658b96a919",
        "governing_law_and_jurisdiction": "f1fefa0ccff8100c",
        "permitted_use_and_standard_of_care": "06e99fc1245a9011",
        "remedies_and_injunctive_relief": "9ba3ad831c635caa",
        "return_or_destruction_of_information": "baaaa2198b2c5f74",
        "term_and_duration_of_confidentiality": "d52c2cfd7d3100db",
    },
    "msa": {
        "confidentiality": "b02c8a4111403f3c",
        "data_protection": "e8dbeb55e0beb0da",
        "indemnification": "0877ee75f3ce2cb9",
        "insurance": "e9ea40a8f082af0a",
        "intellectual_property": "ec12a5ea7ecf44f7",
        "limitation_of_liability": "8000b6f863cfd816",
        "payment_terms": "d4e53b1525bc28d7",
        "service_levels": "401e773106cd1677",
        "termination": "8cfff642b3170176",
    },
    "sow": {
        "acceptance": "83c53af0d37e6d31",
        "change_control": "1b97ec6e45c65ca8",
        "fees_and_pricing": "b12ad3831c70ad2b",
        "key_personnel": "629c813ffe6636ff",
        "milestone_payments": "eeab533c3d4838b9",
        "order_of_precedence": "e594e2d6e704d0e2",
        "scope_and_deliverables": "3e6dd57a0e9e6520",
    },
    "vendor": {
        "audit_rights": "6cc9167b58661eb1",
        "compliance": "2d03135b437974ce",
        "data_protection": "a3d3107544c5d6bc",
        "indemnification": "3f18fd7aad9cc3ea",
        "limitation_of_liability": "0a6938f60e6650ee",
        "payment_terms": "fe90eb1e3138985c",
        "subcontracting": "4b025737006943e9",
        "warranties": "f9cabd48fbb774bf",
    },
    "saas": {
        "data_location": "c9ebf0452a149b16",
        "data_security": "ba321500706d988b",
        "exit_and_data_return": "f863caa492a9efc0",
        "limitation_of_liability": "4f776306c0c23d05",
        "payment_terms": "39b324ced83de2ac",
        "price_increases": "3571c336ac6412cf",
        "renewal": "65201c330bc2c8c3",
        "service_levels": "e342ff7baa79961c",
    },
    "consultancy": {
        "confidentiality": "b02c8a4111403f3c",
        "conflicts_of_interest": "28d3ea734160b3b8",
        "independent_contractor": "2521f1e99e29e1c0",
        "intellectual_property": "6728a82733188554",
        "limitation_of_liability": "f5ee19d741d64337",
        "non_solicitation": "e81ed0b635c22a23",
        "payment_terms": "b7effac5fe435a9d",
        "termination": "3c95acd9ee6ec41b",
    },
    "dpa": {
        "audit": "5b35d0c470fc726d",
        "breach_notification": "373b356e4899c98a",
        "data_subject_rights": "c897cb50e050a4cb",
        "international_transfers": "4d784c086ba41352",
        "processing_instructions": "92fc74ba844762ab",
        "return_or_deletion": "3d4f55fe83558c1b",
        "security_measures": "c10c6205ac24b636",
        "sub_processors": "c1b4236fbbc26914",
    },
    "CUSTOMER": {
        "exclusivity_and_mfn": "c90edffc61f32fd6",
        "governing_law": "e5955026ccca5b38",
        "indemnification": "52ad93edfbada1e8",
        "intellectual_property": "bc681b422f52e9b3",
        "limitation_of_liability": "6caff078e833242c",
        "payment_terms": "4663c7d475b55944",
        "service_credits": "b321dcc7c00bd4c3",
        "warranties": "8cdd54e4322e17c2",
    },
}

def _set_fp(rows: dict[str, str]) -> str:
    """``fingerprint`` of a rule set given as its per-rule hashes."""
    return hashlib.sha256(json.dumps(sorted(rows.values())).encode()).hexdigest()


BASELINE_FINGERPRINTS: dict[str, frozenset[str]] = {
    k: frozenset({_set_fp(rows)}) for k, rows in _BASELINE_ROWS.items()
}
CUSTOMER_BASELINE = next(iter(BASELINE_FINGERPRINTS["CUSTOMER"]))

_LIBRARY_NAME = {
    "msa": "Master Services Agreement Playbook", "sow": "Statement of Work Playbook",
    "vendor": "Vendor Agreement Playbook", "saas": "Software / SaaS Agreement Playbook",
    "consultancy": "Consultancy Agreement Playbook", "dpa": "Data Processing Agreement Playbook",
}
STANDARD_RULES: dict[str, list[dict]] = {
    "nda": library.NDA_STANDARD["rules"],
    **{k: next(s["rules"] for s in library.PLAYBOOK_LIBRARY if s["name"] == n) for k, n in _LIBRARY_NAME.items()},
}
STANDARD_FINGERPRINT = {k: fingerprint(r) for k, r in STANDARD_RULES.items()}

NOTES: dict[str, str] = {
    "nda": "Standard updated: Indian law by default and courts at our registered city; market exclusions, care and "
           "representatives; 10-day return with backups; trade secrets protected while secret; DPDP sentence; AI "
           "tools exception; assignment, e-signature and stamp duty.",
    "msa": "Standard updated: Indian law with arbitration at our registered city; MSME and GST terms; default service "
           "credits; INR 5 crore insurance; 24-hour breach notice; security, continuity and audit; copyright for the "
           "full term; greater-of liability cap.",
    "sow": "Standard updated: names the master agreement; prints only the chosen pricing model; rates and payment "
           "period follow the master agreement; law follows the master agreement; service levels stated.",
    "vendor": "Standard updated: supply clauses follow goods or services; Indian law with arbitration; GST, MSME and "
              "statutory dues; delay damages; greater-of liability cap; insurance minimums; 24-hour breach notice.",
    "saas": "Standard updated: hosting follows the request with privacy approval; one renewal notice of at least 60 "
            "days; one data-breach cap; continuity and backups; suspension limits; sub-processor exit; quarterly "
            "billing; GST and MSME; insurance; Indian law with arbitration; 24-hour breach notice.",
    "consultancy": "Standard updated: one GST position; Indian law with the courts at our registered city; "
                   "re-performance and acceptance; copyright under Indian law with moral rights not asserted; "
                   "independent-contractor terms; day defined; invoicing by fee basis; insurance; 24-hour breach "
                   "notice; stamp duty.",
    "dpa": "Standard updated: names the principal agreement; term and law follow it; DPDP and EU/UK transfers treated "
           "separately; 24-hour breach notice with full details; children's data; records, authority requests and "
           "10-day erasure; one 3x liability limit.",
}


def _changes(key: str) -> list[dict]:
    """What the standard does to each rule against the reviewed baseline, for the version's record."""
    base = _BASELINE_ROWS[key]
    out = []
    for r in STANDARD_RULES[key]:
        before = base.get(r["clause_type"])
        change = "added" if before is None else "kept" if before == row_hash(r, BASELINE_FIELDS.get(key, FIELDS)) else "changed"
        items = [f"Approval by {r['escalation_role']}"] if r.get("approval_required") and change != "kept" else []
        out.append({"clause_type": r["clause_type"], "change": change, "review_items": items})
    kept = {r["clause_type"] for r in STANDARD_RULES[key]}
    out += [{"clause_type": c, "change": "removed", "review_items": []} for c in base if c not in kept]
    return out


CHANGES: dict[str, list[dict]] = {k: _changes(k) for k in STANDARD_RULES}


# --- Reading ------------------------------------------------------------------------------------


def _rules_of(db: Session, org_id: str, version_id: str) -> list[PlaybookRule]:
    return list(db.scalars(select(PlaybookRule).where(
        PlaybookRule.org_id == org_id, PlaybookRule.playbook_version_id == version_id)).all())


def status_for(db: Session, org_id: str, ttype: svc.TemplateType, wording: Wording) -> dict | None:
    """Whether the wording and the reviewing playbook predate the standard. None for a type that was
    not shipped. Reads only."""
    if not ttype.shipped or ttype.key not in STANDARD_RULES:
        return None
    row = wording.row
    wording_behind = row is not None and row.body is not None and row.created_at < EFFECTIVE
    playbook_behind = False
    version = reviewing_playbook(db, org_id, ttype).version
    if version is not None and version.created_at < EFFECTIVE:
        playbook_behind = fingerprint(_rules_of(db, org_id, version.id)) != STANDARD_FINGERPRINT[ttype.key]
    return {"revision": REVISION, "updated_on": UPDATED_ON, "wording_behind": wording_behind,
            "playbook_behind": playbook_behind}


# --- Writing ------------------------------------------------------------------------------------


def publish_standard(db: Session, *, org_id: str, playbook: Playbook, key: str, actor_user_id: str | None,
                     note: str | None, action: str, request_id: str | None = None) -> PlaybookVersion | None:
    """Publish the standard rules as the playbook's next version, with its two audit rows. Flushes
    only. None when the latest published version already holds exactly the standard rules."""
    previous = svc.latest_published_version(db, org_id, playbook.id)
    if previous is not None and fingerprint(_rules_of(db, org_id, previous.id)) == STANDARD_FINGERPRINT[key]:
        return None
    number = int(db.scalar(select(func.max(PlaybookVersion.version_number))
                           .where(PlaybookVersion.playbook_id == playbook.id)) or 0) + 1
    changes = CHANGES[key]
    version = PlaybookVersion(
        org_id=org_id, playbook_id=playbook.id, version_number=number, status=PlaybookStatus.PUBLISHED,
        summary=NOTES[key],
        source_metadata={"standard": REVISION, "source_version_id": previous.id if previous else None,
                         "template_key": key, "changes": changes},
        created_by_user_id=actor_user_id, updated_by_user_id=actor_user_id)
    db.add(version)
    db.flush()
    # Distinct, rising timestamps: the review numbers rules by created_at, so the order must be the standard's.
    start = datetime.now(UTC)
    for i, r in enumerate(STANDARD_RULES[key]):
        stamp = start + timedelta(microseconds=i)
        db.add(PlaybookRule(org_id=org_id, playbook_version_id=version.id, created_by_user_id=actor_user_id,
                            updated_by_user_id=actor_user_id, created_at=stamp, updated_at=stamp, **r))
    playbook.status = PlaybookStatus.PUBLISHED
    playbook.current_version_id = version.id
    playbook.updated_by_user_id = actor_user_id
    db.flush()
    system = {"metadata": _SYSTEM} if actor_user_id is None else {}
    write_audit_log(
        db, action=action, resource_type="playbook", resource_id=playbook.id, org_id=org_id,
        actor_user_id=actor_user_id, request_id=request_id, **system,
        before={"version_id": previous.id if previous else None,
                "version_number": previous.version_number if previous else None},
        after={"version_id": version.id, "version_number": number, "revision": REVISION, "template_key": key,
               "note": note, "changes": changes})
    write_audit_log(
        db, action="playbook.published", resource_type="playbook", resource_id=playbook.id, org_id=org_id,
        actor_user_id=actor_user_id, request_id=request_id, **system,
        after={"playbook_version_id": version.id, "version_number": number})
    return version


def _shipped_keys(db: Session, org_id: str) -> list[svc.TemplateType]:
    return [t for t in svc.list_types(db, org_id) if t.shipped and t.key in STANDARD_RULES]


def apply_standard_update(db: Session) -> None:
    """Migration 0077's body, for every organisation. Flushes only; the caller commits. Safe to
    replay: a marker is added once, and a playbook already on the standard is skipped."""
    org_ids = db.execute(text("SELECT id FROM organization")).scalars().all()
    for org_id in org_ids:  # none on a fresh database (CI): nothing below runs
        for ttype in _shipped_keys(db, org_id):
            _mark_wording(db, org_id, ttype.key)
            rev = reviewing_playbook(db, org_id, ttype)
            if rev.playbook is None or rev.playbook.deleted_at is not None or rev.version is None:
                continue
            rules = _rules_of(db, org_id, rev.version.id)
            if fingerprint(rules) == STANDARD_FINGERPRINT[ttype.key]:
                continue
            if fingerprint(rules, BASELINE_FIELDS.get(ttype.key, FIELDS)) in BASELINE_FINGERPRINTS[ttype.key]:
                publish_standard(db, org_id=org_id, playbook=rev.playbook, key=ttype.key, actor_user_id=None,
                                 note=NOTES[ttype.key], action="playbook.standard_updated")
            else:
                write_audit_log(
                    db, action="playbook.standard_skipped", resource_type="playbook", resource_id=rev.playbook.id,
                    org_id=org_id, actor_user_id=None, metadata=_SYSTEM,
                    after={"template_key": ttype.key, "reason": "edited by the organisation"})
    db.flush()


def _mark_wording(db: Session, org_id: str, key: str) -> None:
    latest = svc._latest_row(db, org_id, key)
    if latest is not None and (latest.body is not None or latest.note == NOTES[key]):
        return  # its own wording stays (FR-54), or the marker is already there
    row = DraftingTemplateVersion(org_id=org_id, key=key, version=(latest.version if latest else 0) + 1,
                                  body=None, note=NOTES[key])
    db.add(row)
    db.flush()
    write_audit_log(
        db, action="drafting_template.standard_updated", resource_type="drafting_template", resource_id=row.id,
        org_id=org_id, actor_user_id=None, metadata=_SYSTEM,
        after={"key": key, "version": row.version, "revision": REVISION, "note": row.note, "default": True})


def revert_standard_update(db: Session) -> None:
    """Archive every playbook version this revision published and point each playbook back at the one it
    came from. Nothing is deleted; the template markers hold no text and stay."""
    versions = db.scalars(select(PlaybookVersion).where(
        PlaybookVersion.status == PlaybookStatus.PUBLISHED, PlaybookVersion.source_metadata.is_not(None))).all()
    for v in versions:
        meta = v.source_metadata or {}
        if meta.get("standard") != REVISION:
            continue
        v.status = PlaybookStatus.ARCHIVED
        pb = db.get(Playbook, v.playbook_id)
        if pb is not None and pb.current_version_id == v.id:
            pb.current_version_id = meta.get("source_version_id")
        write_audit_log(
            db, action="playbook.standard_reverted", resource_type="playbook", resource_id=v.playbook_id,
            org_id=v.org_id, actor_user_id=None, metadata=_SYSTEM,
            after={"version_id": v.id, "version_number": v.version_number,
                   "restored_version_id": meta.get("source_version_id")})
    db.flush()


def adopt(db: Session, *, actor: User, key: str, note: str | None, request_id: str | None) -> dict:
    """The person's "Use the standard rules": publish them as the reviewing playbook's next version,
    commit, and only then ask for the template check (the check takes the audit lock itself)."""
    org_id = actor.org_id
    ttype = get_type(db, org_id, key)  # 404 unknown or another organisation's
    if not ttype.shipped or key not in STANDARD_RULES:
        raise HTTPException(status.HTTP_409_CONFLICT, _NO_STANDARD)
    playbook = reviewing_playbook(db, org_id, ttype).playbook
    if playbook is None or playbook.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, _NO_PLAYBOOK)
    clean = svc._clean_note(note)
    version = publish_standard(db, org_id=org_id, playbook=playbook, key=key, actor_user_id=actor.id,
                               note=clean, action="playbook.standard_adopted", request_id=request_id)
    service = svc.DraftingTemplateService(db)
    if version is None:
        return service._result(org_id, key, changed=False, check=None)
    db.commit()
    check = None
    try:
        check = svc._checks_module().request_check(
            db, org_id=org_id, key=key, trigger="playbook_published", actor_user_id=actor.id,
            trigger_detail={"standard": REVISION, "playbook_id": playbook.id, "playbook_version_id": version.id})
    except Exception:
        logger.exception("could not request the template check after adopting the standard key=%s", key)
        db.rollback()
    return service._result(org_id, key, changed=True, check=check)
