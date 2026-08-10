import difflib
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import (
    ContractEdit,
    ContractShare,
    ContractTextSnapshot,
    ContractVersion,
)
from app.contracts.access import accessible_contract_filter, user_can_access_contract
from app.contracts.models import Contract, ContractParty, ContractStageHistory
from app.core.audit import write_audit_log, write_timeline_event
from app.core.enums import (
    ContractLifecycleStage,
    UserStatus,
)
from app.core.models import ResourceTimelineEvent
from app.playbooks.models import PlaybookDeviation, PlaybookRun


def get_contract_for_user(db: Session, *, contract_id: str, user: User) -> Contract:
    contract = db.get(Contract, contract_id)
    if (
        contract is None
        or contract.org_id != user.org_id
        or contract.deleted_at is not None
        or not user_can_access_contract(db, contract=contract, user=user)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    return contract


def list_contracts_for_user(
    db: Session,
    *,
    user: User,
    limit: int = 100,
    offset: int = 0,
) -> list[Contract]:
    # Per-row access is enforced inside the SQL via accessible_contract_filter
    # (no Python post-filtering), so LIMIT/OFFSET page over already-authorized
    # rows. The ContractResponse serializer only reads scalar columns on
    # Contract — there is no relationship traversal here, so no eager-load is
    # needed to avoid N+1. Bounds are validated at the route layer; clamp here
    # too so direct service callers can't request an unbounded page.
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return db.scalars(
        select(Contract)
        .where(
            Contract.org_id == user.org_id,
            Contract.deleted_at.is_(None),
            accessible_contract_filter(user),
        )
        .order_by(Contract.updated_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()


def update_contract_metadata(
    db: Session,
    *,
    contract: Contract,
    user: User,
    updates: dict,
    request_id: str | None = None,
) -> Contract:
    """Apply partial metadata updates to a contract.

    Semantics: ``None`` values in ``updates`` are skipped, not cleared. To
    clear a previously-set field, callers must pass a sentinel handled at
    the schema layer (e.g. an explicit empty string for string fields) —
    `null` over the wire is interpreted as "leave this field alone". This is
    intentional to match Pydantic's ``exclude_unset`` semantics used by the
    PATCH route. Callers needing true field clearing should add an explicit
    "clear_X" flag to the schema rather than relying on this function.
    """
    before = {
        "title": contract.title,
        "contract_type": contract.contract_type,
        "counterparty_name": contract.counterparty_name,
        "jurisdiction": contract.jurisdiction,
        "confidentiality": contract.confidentiality,
        "risk_level": contract.risk_level,
        "value_amount": contract.value_amount,
        "currency": contract.currency,
        "effective_date": contract.effective_date,
        "expiration_date": contract.expiration_date,
        "metadata_json": contract.metadata_json,
    }
    for key, value in updates.items():
        if value is not None and hasattr(contract, key):
            setattr(contract, key, value)
    contract.updated_by_user_id = user.id
    write_audit_log(
        db,
        action="contract.metadata_updated",
        resource_type="contract",
        resource_id=contract.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        request_id=request_id,
        before=before,
        after=updates,
    )
    write_timeline_event(
        db,
        org_id=contract.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.metadata_updated",
        title="Contract metadata updated",
        actor_user_id=user.id,
        request_id=request_id,
        details={"updated_fields": sorted(updates.keys())},
    )
    db.commit()
    db.refresh(contract)
    return contract


def list_contract_stage_history(db: Session, *, contract: Contract) -> list[ContractStageHistory]:
    return db.scalars(
        select(ContractStageHistory)
        .where(ContractStageHistory.org_id == contract.org_id, ContractStageHistory.contract_id == contract.id)
        .order_by(ContractStageHistory.changed_at.asc())
    ).all()


def list_contract_activity(db: Session, *, contract: Contract, limit: int = 100) -> list[ResourceTimelineEvent]:
    return db.scalars(
        select(ResourceTimelineEvent)
        .where(
            ResourceTimelineEvent.org_id == contract.org_id,
            ResourceTimelineEvent.resource_type == "contract",
            ResourceTimelineEvent.resource_id == contract.id,
        )
        .order_by(ResourceTimelineEvent.created_at.desc())
        .limit(min(limit, 200))
    ).all()


# Pre-approval stages where a guided "what's next" review makes sense.
_PRE_APPROVAL_STAGES = {
    ContractLifecycleStage.INTAKE,
    ContractLifecycleStage.DRAFTING,
    ContractLifecycleStage.REVIEW,
}
_UNRESOLVED_DEVIATION = ("open", "needs_review")


def compute_review_status(db: Session, *, contract: Contract) -> dict:
    """Derive a guided "what to do next" view of a contract's review from signals
    the app already produces — playbook deviations, proposed redlines, open
    comments, and counterparty shares. Pure read; no state of its own."""
    cid = contract.id
    stage = contract.lifecycle_stage

    # "AI reviewed" is satisfied by EITHER a structured clause/risk analysis OR a
    # playbook review — so the one-click analysis clears this step, and a playbook
    # run (which also yields deviations) counts as the deeper check.
    playbook_reviewed = (
        db.scalar(
            select(func.count(PlaybookRun.id)).where(
                PlaybookRun.contract_id == cid, PlaybookRun.status == "succeeded"
            )
        )
        or 0
    ) > 0
    clause_analyzed = (
        db.scalar(
            select(func.count(ClauseExtraction.id)).where(ClauseExtraction.contract_id == cid)
        )
        or 0
    ) > 0
    ai_reviewed = playbook_reviewed or clause_analyzed
    open_issues = (
        db.scalar(
            select(func.count(PlaybookDeviation.id)).where(
                PlaybookDeviation.contract_id == cid,
                PlaybookDeviation.status.in_(_UNRESOLVED_DEVIATION),
            )
        )
        or 0
    )
    high_issues = (
        db.scalar(
            select(func.count(PlaybookDeviation.id)).where(
                PlaybookDeviation.contract_id == cid,
                PlaybookDeviation.status.in_(_UNRESOLVED_DEVIATION),
                func.lower(PlaybookDeviation.severity).in_(("high", "critical")),
            )
        )
        or 0
    )
    pending_redlines = (
        db.scalar(
            select(func.count(ContractEdit.id)).where(
                ContractEdit.contract_id == cid, ContractEdit.status == "proposed"
            )
        )
        or 0
    )
    open_comments = _open_comment_count(db, contract_id=cid)

    now = datetime.now(UTC)
    counterparty_active = (
        db.scalar(
            select(func.count(ContractShare.id)).where(
                ContractShare.contract_id == cid,
                ContractShare.revoked_at.is_(None),
                ContractShare.deleted_at.is_(None),
                or_(ContractShare.expires_at.is_(None), ContractShare.expires_at > now),
            )
        )
        or 0
    ) > 0

    # ONE gate to approval: the document must be final, i.e. every proposed
    # redline is accepted or rejected. Approvers sign off on settled text, so
    # undecided changes are the only true blocker. High-severity issues and open
    # comments are surfaced as ADVISORY signals (see checklist) but don't block —
    # high risk is handled by routing to a senior approver, not by a wall. This
    # matches how Ironclad / Juro gate the move into approval.
    ready_for_approval = stage in _PRE_APPROVAL_STAGES and pending_redlines == 0

    def _item(key, label, status, count=0, detail=None):
        return {"key": key, "label": label, "status": status, "count": count, "detail": detail}

    checklist = [
        _item(
            "ai_review", "AI review",
            "done" if ai_reviewed else "todo",
            detail=(
                "Analyzes risks & clauses in one click"
                if not ai_reviewed
                else (
                    "Analysis done — run a playbook for a deeper standards check"
                    if not playbook_reviewed
                    else None
                )
            ),
        ),
        _item(
            "issues", "Review flagged issues",
            # Advisory — never blocks approval; high risk routes to a senior
            # approver instead. "todo" (not "blocked") keeps the one-click submit
            # enabled while still surfacing the count.
            "done" if open_issues == 0 else "todo",
            count=open_issues,
            detail=f"{high_issues} high/critical · advisory" if high_issues else None,
        ),
        _item(
            "redlines", "Resolve redlines",
            "done" if pending_redlines == 0 else "todo",
            count=pending_redlines,
        ),
        _item(
            "comments", "Resolve comments",
            "done" if open_comments == 0 else "todo",
            count=open_comments,
        ),
        _item(
            "counterparty", "Counterparty round",
            "in_progress" if counterparty_active else "todo",
            detail="Shared — awaiting response" if counterparty_active else "Optional — send for negotiation",
        ),
    ]
    if stage in _PRE_APPROVAL_STAGES:
        checklist.append(
            _item(
                "approval", "Submit for approval",
                "todo" if ready_for_approval else "blocked",
                detail=(
                    "After review"
                    if stage != ContractLifecycleStage.REVIEW
                    else ("Ready" if ready_for_approval else "Decide open redlines first")
                ),
            )
        )
    else:
        checklist.append(_item("approval", "Submit for approval", "done"))

    # Stage-aware guidance: intake and drafting move the contract FORWARD in
    # the lifecycle (issues/redlines/comments are review-stage work); only the
    # review stage may recommend submitting for approval — never skipping it.
    if stage not in _PRE_APPROVAL_STAGES:
        next_action, next_step = None, f"Contract is in '{stage}' — review complete."
    elif not ai_reviewed:
        next_action, next_step = "run_ai", "Run an AI review to surface risks and missing clauses."
    elif stage == ContractLifecycleStage.INTAKE:
        next_action, next_step = "move_to_drafting", "Intake checks passed — move into drafting."
    elif stage == ContractLifecycleStage.DRAFTING:
        next_action, next_step = "move_to_review", "Draft ready? Send it for review."
    elif pending_redlines:
        # The one true gate: finalise the text before approval.
        next_action, next_step = (
            "resolve_redlines",
            f"Accept or reject {pending_redlines} pending redline(s) to finalise the text.",
        )
    else:
        # Redlines are decided → ready to submit. Any open high issues / comments
        # are advisory only, noted so the user can weigh them (but not blocked).
        advisories = []
        if high_issues:
            advisories.append(f"{high_issues} high-severity issue(s)")
        if open_comments:
            advisories.append(f"{open_comments} open comment(s)")
        note = (
            f" Heads up: {', '.join(advisories)} still open — review advised."
            if advisories
            else ""
        )
        next_action, next_step = (
            "submit_approval",
            f"Redlines resolved — ready to submit for approval.{note}",
        )

    return {
        "contract_id": cid,
        "lifecycle_stage": stage,
        "ai_reviewed": ai_reviewed,
        "open_issues": open_issues,
        "high_severity_issues": high_issues,
        "pending_redlines": pending_redlines,
        "open_comments": open_comments,
        "counterparty_active": counterparty_active,
        "ready_for_approval": ready_for_approval,
        "next_step": next_step,
        "next_action": next_action,
        "checklist": checklist,
    }


def _open_comment_count(db: Session, *, contract_id: str) -> int:
    """Unresolved comments on the contract. Returns 0 until the comments feature
    (Phase 2) adds the table — guarded so this works before that migration."""
    try:
        from app.contracts.comments_models import ContractComment
    except Exception:
        return 0
    return (
        db.scalar(
            select(func.count(ContractComment.id)).where(
                ContractComment.contract_id == contract_id,
                ContractComment.resolved_at.is_(None),
                ContractComment.deleted_at.is_(None),
            )
        )
        or 0
    )


_DIFF_MAX_LINES = 4000


def compute_version_diff(
    db: Session, *, contract: Contract, base_version_id: str, target_version_id: str
) -> dict:
    """Line-level diff between two versions' extracted text — used to show what
    the counterparty changed when they return a revision."""

    def _resolve(version_id: str) -> tuple[ContractVersion, str]:
        version = db.get(ContractVersion, version_id)
        if (
            version is None
            or version.org_id != contract.org_id
            or version.contract_id != contract.id
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract version not found")
        snapshot = (
            db.get(ContractTextSnapshot, version.text_snapshot_id)
            if version.text_snapshot_id
            else None
        )
        return version, (snapshot.text if snapshot and snapshot.text else "")

    base_version, base_text = _resolve(base_version_id)
    target_version, target_text = _resolve(target_version_id)

    base_lines = base_text.splitlines()
    target_lines = target_text.splitlines()
    lines: list[dict] = []
    added = removed = 0
    truncated = False
    matcher = difflib.SequenceMatcher(a=base_lines, b=target_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            for text in base_lines[i1:i2]:
                lines.append({"type": "remove", "text": text})
                removed += 1
        if tag in ("insert", "replace"):
            for text in target_lines[j1:j2]:
                lines.append({"type": "add", "text": text})
                added += 1
        if tag == "equal":
            for text in base_lines[i1:i2]:
                lines.append({"type": "context", "text": text})
        if len(lines) > _DIFF_MAX_LINES:
            truncated = True
            lines = lines[:_DIFF_MAX_LINES]
            break

    return {
        "base_version_id": base_version_id,
        "base_version_number": base_version.version_number,
        "target_version_id": target_version_id,
        "target_version_number": target_version.version_number,
        "added": added,
        "removed": removed,
        "truncated": truncated,
        "lines": lines,
    }


# --- Contract parties (signers) ------------------------------------------
def list_contract_parties(db: Session, *, contract: Contract) -> list[ContractParty]:
    return list(
        db.scalars(
            select(ContractParty)
            .where(
                ContractParty.org_id == contract.org_id,
                ContractParty.contract_id == contract.id,
            )
            .order_by(ContractParty.name)
        ).all()
    )


def add_contract_party(
    db: Session,
    *,
    contract: Contract,
    user: User,
    name: str,
    contact_email: str | None = None,
    party_type: str | None = None,
) -> ContractParty:
    if not name or not name.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Party name is required")
    party = ContractParty(
        org_id=contract.org_id,
        contract_id=contract.id,
        name=name.strip(),
        party_type=(party_type.strip() if party_type else None),
        contact_email=(contact_email.strip().lower() if contact_email else None),
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    db.add(party)
    db.commit()
    db.refresh(party)
    return party


def delete_contract_party(db: Session, *, contract: Contract, party_id: str) -> None:
    party = db.get(ContractParty, party_id)
    if party is None or party.org_id != contract.org_id or party.contract_id != contract.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    db.delete(party)
    db.commit()


def list_signer_options(db: Session, *, contract: Contract) -> list[dict]:
    """Valid signature recipients for the contract — its parties (with an email)
    plus active org users. Mirrors the signature allowlist, so the picker can't
    offer anyone the send would reject."""
    options: dict[str, dict] = {}
    for party in list_contract_parties(db, contract=contract):
        if party.contact_email:
            options[party.contact_email.lower()] = {
                "name": party.name,
                "email": party.contact_email,
                "kind": "party",
            }
    for member in db.scalars(
        select(User).where(
            User.org_id == contract.org_id, User.status == UserStatus.ACTIVE
        )
    ):
        options.setdefault(
            member.email.lower(),
            {"name": member.full_name or member.email, "email": member.email, "kind": "user"},
        )
    return list(options.values())
