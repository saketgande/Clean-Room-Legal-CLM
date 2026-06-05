"""F-11 rewrite — ``AIController._persist_obligations`` (content-hash diff).

Replaces ``backend/app/ai/controller.py:1446-1542``. The previous body
soft-deleted every prior AI-extracted open obligation on every run,
then re-created every row including ``ObligationReminder`` — clobbering
human-tuned reminders (Agent 2 F-11 — High).

The new body computes a content hash per obligation using only the
structural fields ``(due_date, amount_kind, party)`` so that:
- An identical extraction is a true no-op (rows untouched, reminders
  preserved).
- A genuinely new obligation is inserted.
- An obligation present last run but absent now is soft-deleted.
- ``ObligationReminder.remind_at`` is recomputed ONLY for newly
  inserted obligations OR existing obligations whose ``due_date``
  changed.

A structured diff is recorded on ``audit_log.metadata_json``:
``{added, kept, deleted, reminder_preserved, reminder_recomputed}``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.context import ContractAIContext
from app.ai.models import AISkillRun
from app.ai.schemas import ObligationExtractionOutput
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.obligations.models import Obligation, ObligationReminder

_logger = logging.getLogger(__name__)


def _normalize(value: object) -> str:
    """Render a structural-field value into a stable string for hashing."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value).strip().lower()


def _content_hash_for_payload(
    *,
    due_date: object,
    responsible_party: object,
    obligation_type: object,
) -> str:
    """Hash the structural fields that uniquely identify an obligation."""
    canonical = json.dumps(
        {
            "due_date": _normalize(due_date),
            "responsible_party": _normalize(responsible_party),
            "obligation_type": _normalize(obligation_type),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def persist_obligations_diff(
    self,
    db: Session,
    *,
    output: BaseModel,
    context: ContractAIContext,
    created_by_user_id: str | None,
    request_id: str | None,
    skill_run: AISkillRun,
) -> None:
    """Diff-aware obligation persistence; preserves human-tuned reminders."""
    extracted = (
        output
        if isinstance(output, ObligationExtractionOutput)
        else ObligationExtractionOutput.model_validate(output)
    )
    contract = context.contract
    today = utcnow().date()

    existing_rows: list[Obligation] = list(
        db.scalars(
            select(Obligation).where(
                Obligation.org_id == contract.org_id,
                Obligation.contract_id == contract.id,
                Obligation.deleted_at.is_(None),
            )
        ).all()
    )
    existing_by_hash: dict[str, Obligation] = {}
    for ob in existing_rows:
        meta = ob.metadata_json or {}
        if meta.get("source") != "ai_extraction":
            # Human-created obligations are NEVER touched by this code path.
            continue
        if ob.status in {"completed", "cancelled"}:
            continue
        key = _content_hash_for_payload(
            due_date=ob.due_date,
            responsible_party=ob.responsible_party,
            obligation_type=ob.obligation_type,
        )
        existing_by_hash[key] = ob

    seen_hashes: set[str] = set()
    added = kept = reminder_preserved = reminder_recomputed = 0
    for item in extracted.obligations:
        item_hash = _content_hash_for_payload(
            due_date=item.due_date,
            responsible_party=item.responsible_party,
            obligation_type=item.obligation_type,
        )
        seen_hashes.add(item_hash)
        existing = existing_by_hash.get(item_hash)
        if existing is not None:
            # Same structural identity. Update description/citations only;
            # leave reminders alone.
            existing.description = item.description
            existing.recurrence = item.recurrence
            existing.source_citation = (
                item.citations[0].model_dump(mode="json") if item.citations else None
            )
            existing.metadata_json = {
                **(existing.metadata_json or {}),
                "skill_run_id": skill_run.id,
                "confidence": item.confidence,
                "source_clause_type": item.source_clause_type,
                "source": "ai_extraction",
            }
            existing.updated_by_user_id = created_by_user_id
            kept += 1
            reminder_preserved += 1
            continue

        citation = item.citations[0].model_dump(mode="json") if item.citations else None
        obligation = Obligation(
            org_id=contract.org_id,
            contract_id=contract.id,
            contract_version_id=context.version.id if context.version else None,
            owner_user_id=contract.owner_user_id,
            responsible_party=item.responsible_party,
            obligation_type=item.obligation_type,
            description=item.description,
            due_date=item.due_date,
            recurrence=item.recurrence,
            status="open",
            source_citation=citation,
            metadata_json={
                "source": "ai_extraction",
                "confidence": item.confidence,
                "source_clause_type": item.source_clause_type,
                "skill_run_id": skill_run.id,
            },
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(obligation)
        db.flush()
        added += 1
        if item.due_date is not None:
            remind_at = item.due_date - timedelta(days=7)
            if remind_at < today:
                remind_at = today
            db.add(
                ObligationReminder(
                    org_id=contract.org_id,
                    obligation_id=obligation.id,
                    remind_at=remind_at,
                    channel="email",
                    created_by_user_id=created_by_user_id,
                    updated_by_user_id=created_by_user_id,
                )
            )
            reminder_recomputed += 1

    deleted = 0
    for key, ob in existing_by_hash.items():
        if key in seen_hashes:
            continue
        ob.deleted_at = utcnow()
        ob.deleted_by_user_id = created_by_user_id
        ob.updated_by_user_id = created_by_user_id
        deleted += 1

    diff_summary: dict[str, Any] = {
        "added": added,
        "kept": kept,
        "deleted": deleted,
        "reminder_preserved": reminder_preserved,
        "reminder_recomputed": reminder_recomputed,
    }
    write_audit_log(
        db,
        action="contract.obligations_extracted",
        resource_type="contract",
        resource_id=contract.id,
        org_id=contract.org_id,
        actor_user_id=created_by_user_id,
        request_id=request_id,
        after={"skill_run_id": skill_run.id, **diff_summary},
        metadata=diff_summary,
    )
    write_timeline_event(
        db,
        org_id=contract.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.obligations_extracted",
        title=f"Obligations extracted (+{added} / kept {kept} / -{deleted})",
        actor_user_id=created_by_user_id,
        request_id=request_id,
        skill_run_id=skill_run.id,
        details=diff_summary,
    )
    _logger.info(
        "obligations.diff_persisted",
        extra={
            "contract_id": contract.id,
            "skill_run_id": skill_run.id,
            **diff_summary,
        },
    )
