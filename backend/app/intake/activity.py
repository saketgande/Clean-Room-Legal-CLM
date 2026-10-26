"""A request's history in plain words (GET /intake/requests/{id}/activity).

Merges the request's and its contract's timeline events, the hand-offs and the workflow
comments into one newest-first list. The event codes, ``details`` JSON and ids never leave
this module: every row becomes a sentence from the ``EVENTS`` table, and a guard replaces any
sentence that still reads like a code. Read-only: no audit row, no timeline event.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.access import ContractAccessService, is_sealed_off
from app.contracts.models import Contract
from app.core import org_access
from app.core.access import is_org_admin
from app.core.models import ResourceTimelineEvent
from app.core.rbac import has_permission
from app.intake.models import IntakeHandoff, IntakeRequest

# ponytail: 500-item cap; switch to a `before=<at>` cursor if a request's history ever passes
# it (the response already says `truncated`).
CAP = 500
_TEXT_MAX = 240
_COMMENT_MAX = 200
_STAGES = {"intake", "drafting", "review", "approval", "signature", "active", "closed"}
_BANDS = {"low", "medium", "high", "critical"}

_RAW_CODE = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")
_UUID_HEAD = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")
_APPROVAL = re.compile(r"^Approval (approv|reject)ed \(step (\d+)\)$")

Sentence = str | Callable[[ResourceTimelineEvent], str | None] | None


@dataclass(frozen=True)
class _Spec:
    sentence: Sentence  # None = the stored title (code-authored prose)
    who: str  # person | ai | aegis | cp
    tone: str | Callable[[ResourceTimelineEvent], str]
    system: bool = False


def _d(row: ResourceTimelineEvent) -> dict[str, Any]:
    return row.details if isinstance(row.details, dict) else {}


def _skill(verb: str) -> Callable[[ResourceTimelineEvent], str]:
    def make(row: ResourceTimelineEvent) -> str:
        name = _d(row).get("skill_name")
        if not isinstance(name, str) or not name:
            m = re.search(r": ([^(]+?)(?: \(|$)", row.title or "")
            name = m.group(1) if m else ""
        name = name.strip().replace("_", " ")
        name = (name[:1].upper() + name[1:]) if name else ""
        if not name or _is_raw(name, ""):
            name = "a task"
        return f"{verb}: {name}"

    return make


def _approval_decided(row: ResourceTimelineEvent) -> str | None:
    m = _APPROVAL.match(row.title or "")
    return f"{m.group(1).capitalize()}ed (step {m.group(2)})" if m else None


def _approval_tone(row: ResourceTimelineEvent) -> str:
    m = _APPROVAL.match(row.title or "")
    return "done" if (m is None or m.group(1) == "approv") else "stage"


def _lifecycle(row: ResourceTimelineEvent) -> str | None:
    to = _d(row).get("to_stage")
    return f"Stage changed to {to.capitalize()}" if to in _STAGES else None


def _review_done(row: ResourceTimelineEvent) -> str:
    d = _d(row)
    if d.get("status") not in (None, "succeeded"):
        return "AI review did not finish"
    n = d.get("deviation_count")
    if isinstance(n, int) and not isinstance(n, bool):
        if n == 0:
            return "AI review complete · no issues found"
        return f"AI review complete · {n} issue{'' if n == 1 else 's'} found"
    return "AI review complete"


def _obligations(row: ResourceTimelineEvent) -> str:
    n = _d(row).get("obligation_count")
    if isinstance(n, int) and not isinstance(n, bool):
        return f"AI found {n} obligation{'' if n == 1 else 's'}"
    return "AI read the obligations"


def _risk(row: ResourceTimelineEvent) -> str:
    band = _d(row).get("band")
    return f"Risk recalculated: {band}" if band in _BANDS else "Risk recalculated"


def _renewal(row: ResourceTimelineEvent) -> str:
    dec = _d(row).get("decision")
    if isinstance(dec, str) and dec.strip() and not _is_raw(dec, ""):
        return f"Renewal decision: {dec.replace('_', ' ')}"
    return "Renewal decision recorded"


def _s(sentence: Sentence, who: str, tone: str | Callable = "plain", system: bool = False) -> _Spec:
    return _Spec(sentence, who, tone, system)


EVENTS: dict[str, _Spec] = {
    "intake.created": _s(None, "person"),
    "intake.triaged": _s("AI triage complete", "ai", "ai"),
    "intake.sender.unverified": _s(None, "aegis", "stage"),
    "intake.attachment.failed": _s(None, "aegis", "stage"),
    "intake.contract_drafted": _s(None, "person", "done"),
    "intake.contract_from_attachment": _s(None, "person", "done"),
    "intake.auto_escalated": _s(None, "aegis", "stage"),
    "intake.closed": _s("Request closed", "person", "stage"),
    "intake.completed": _s(None, "aegis", "done"),
    "intake.stage_advanced": _s("Request moved to its next stage", "person", "stage"),
    "intake.submitted_for_approval": _s("Sent for approval", "person", "stage"),
    "intake.approved": _s(None, "person", "done"),
    "intake.approval_rejected": _s(None, "person", "stage"),
    "intake.approval_gate_passed": _s(None, "aegis", "done"),
    "intake.promoted": _s(None, "person"),
    "intake.deliverables_changed": _s(None, "person"),
    "intake.answers_changed": _s(None, "person"),
    "flow.started": _s(None, "person"),
    "flow.step_completed": _s(None, "person", "done"),
    "flow.completed": _s(None, "aegis", "done"),
    "flow.failed": _s(None, "aegis", "stage"),
    "flow.cancelled": _s(None, "aegis", "stage"),
    "flow.returned": _s(None, "person", "stage"),
    "flow.notify": _s(None, "aegis"),
    "flow.draft_skipped": _s(None, "aegis"),
    "flow.review_retried": _s("AI review restarted", "person", "ai"),
    "approval.decided": _s(_approval_decided, "person", _approval_tone),
    "approval.partial": _s(None, "person", "done"),
    "approval.delegate": _s(None, "person"),
    "approval.escalate": _s(None, "person"),
    "ai.skill_started": _s(_skill("AI started"), "ai", "ai", True),
    "ai.skill_succeeded": _s(_skill("AI finished"), "ai", "ai", True),
    "ai.skill_failed": _s(_skill("AI could not finish"), "ai", "ai", True),
    "contract.uploaded": _s("Contract document stored", "person", system=True),
    "contract.text_extracted": _s("Document text read", "aegis", system=True),
    "contract.ai_jobs_dispatched": _s("AI analysis queued", "aegis", "ai", True),
    "contract.ai_metadata_extracted": _s("AI read the contract's key details", "ai", "ai", True),
    "contract.brain_ingested": _s("Contract Brain updated", "aegis", system=True),
    "contract.drafted_from_template": _s("Drafted from our template", "aegis", system=True),
    "contract.auto_review": _s(None, "ai", "ai", True),
    "contract.renewal_extracted": _s("AI read the renewal terms", "ai", "ai", True),
    "contract.obligations_extracted": _s(_obligations, "ai", "ai", True),
    "playbook.redline_created": _s("AI marked its suggested changes in the document", "ai", "ai", True),
    "assistant.tracked_change_created": _s("AI prepared a tracked-change version", "ai", "ai", True),
    "assistant.contract_version_replicated": _s("AI copied a version of the contract", "ai", "ai", True),
    "renewal.dates_recomputed": _s(None, "aegis", system=True),
    "contract.lifecycle_changed": _s(_lifecycle, "person", "stage"),
    "playbook.run_completed": _s(_review_done, "ai", "ai"),
    "playbook.deviation_decided": _s("Decided a review issue", "person", "done"),
    "contract.issue_fixes_drafted": _s(None, "ai", "ai"),
    "contract.auto_review_retried": _s("AI review restarted", "person", "ai"),
    "assistant.contract_redrafted": _s("AI redrafted the contract into a new version", "ai", "ai"),
    "assistant.contract_generated": _s("AI generated the contract", "ai", "ai"),
    "contract.edit_accepted": _s("Accepted a suggested change", "person", "done"),
    "contract.edit_rejected": _s("Rejected a suggested change", "person"),
    "contract.redline_applied": _s(None, "person", "done"),
    "contract.redline_not_applied": _s(None, "aegis"),
    "contract.manual_edit_proposed": _s("Proposed a change by hand", "person"),
    "contract.text_manually_edited": _s("Edited the document by hand", "person"),
    "contract.edited_in_word": _s(None, "person"),
    "contract.version_uploaded": _s("New version uploaded", "person"),
    "contract.version_restored": _s("Restored an earlier version", "person"),
    "contract.counterparty_revision": _s(None, "person"),
    "contract.revision_countered": _s(None, "person"),
    "contract.revision_agreed": _s(None, "person", "done"),
    "contract.negotiation_revision": _s(None, "person"),
    "contract.share_created": _s("Shared the contract", "person"),
    "contract.changed_by_request": _s(None, "person"),
    "contract.metadata_updated": _s("Contract details updated", "person"),
    "contract.risk_recomputed": _s(_risk, "person", "ai"),
    "workflow.counterparty_sent": _s(None, "person"),
    "workflow.counterparty_submitted": _s(None, "cp"),
    "signature.completed": _s(None, "aegis", "done"),
    "renewal.decided": _s(_renewal, "person", "done"),
    "renewal.dates_confirmed": _s(None, "person", "done"),
    # Found by the source scan, not in the plan's table: these are written as notification or
    # audit event types, never as timeline rows today, but a literal exists, so each gets a row
    # (hidden system events) rather than letting the scan test pass by exclusion.
    "approval.fast_laned": _s("Fast-laned past approval", "aegis", "stage", True),
    "contract.auto_closed": _s("Closed automatically", "aegis", "stage", True),
    "contract.comment.mention": _s("Mentioned in a comment", "person", system=True),
    "contract.comment.counterparty": _s("Counterparty comment", "cp", system=True),
    "contract.negotiation_review": _s("Negotiation review sent", "person", system=True),
    "workflow.step_assigned": _s("Workflow step assigned", "aegis", system=True),
    "workflow.step_escalated": _s("Workflow step escalated", "aegis", system=True),
}


def _is_raw(sentence: str, event_type: str) -> bool:
    s = (sentence or "").strip()
    return (
        not s
        or s == event_type
        or _RAW_CODE.fullmatch(s) is not None
        or _UUID_HEAD.search(s) is not None
        or s[0] in "{["
    )


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _fallback(source: str) -> str:
    return "Contract updated" if source == "contract" else "Request updated"


def _event_item(row: ResourceTimelineEvent, source: str, names: dict[str, str]) -> dict:
    spec = EVENTS.get(row.event_type)
    if spec is None:
        sentence, who, tone, system = _fallback(source), "person", "plain", True
    else:
        sentence = spec.sentence(row) if callable(spec.sentence) else spec.sentence
        if sentence is None:
            sentence = row.title or ""
        if _is_raw(sentence, row.event_type):
            sentence = spec.sentence if isinstance(spec.sentence, str) else _fallback(source)
        who, system = spec.who, spec.system
        tone = spec.tone(row) if callable(spec.tone) else spec.tone
    return {
        "id": f"t:{row.id}", "at": row.created_at, "sentence": _cut(sentence, _TEXT_MAX),
        "who": _who(who, row.created_by_user_id, names), "source": source, "tone": tone,
        "system": system,
    }


def _who(kind: str, user_id: str | None, names: dict[str, str]) -> str:
    return {"ai": "AI", "aegis": "Aegis", "cp": "Counterparty"}.get(kind) or names.get(
        user_id or "", "Aegis"
    )


def _handoff_item(h: IntakeHandoff, names: dict[str, str]) -> dict:
    if h.to_holder == "human":
        text = f"Handed to {names.get(h.to_user_id or '') or 'a person'}"
    elif h.to_holder == "agent":
        text = "Handed to the AI agent"
    elif h.to_holder == "queue":
        text = "Returned to the queue"
    else:
        text = "Handed on"
    if h.reason:
        text += f": {h.reason}"
    who = names.get(h.created_by_user_id or "", "Aegis") if h.actor_type == "user" else "Aegis"
    return {
        "id": f"h:{h.id}", "at": h.created_at, "sentence": _cut(text, _TEXT_MAX), "who": who,
        "source": "request", "tone": "plain", "system": False,
    }


def _comment_items(run, request_id: str) -> list[dict]:
    items = []
    for n, c in enumerate((run.context or {}).get("comments") or []):
        if not isinstance(c, dict) or not c.get("text"):
            continue
        try:
            at = datetime.fromisoformat(str(c.get("at")))
        except ValueError:
            continue
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
        text = _cut(str(c["text"]), _COMMENT_MAX)
        kind = c.get("kind")
        tone = "plain"
        if kind == "return":
            sentence, tone = f"Sent back for rework: “{text}”", "stage"
        elif kind == "reply":
            sentence = f"Replied to the rejection: “{text}”"
        else:
            sentence = f"Commented: “{text}”"
        who = str(c.get("actor_name") or "Aegis")
        items.append({
            "id": f"c:{run.id}:{n}", "at": at, "sentence": _cut(sentence, _TEXT_MAX + 20),
            "who": "Aegis" if who == "system" else who, "source": "request", "tone": tone,
            "system": False,
        })
    return items


def _contract_visible_silently(db: Session, user: User, contract_id: str) -> bool:
    """The contract page's own rule, minus its ``access.denied`` audit row: the ``is_sealed_off``
    pre-check excludes the only denials ``user_can_access_contract`` writes a row for."""
    if not has_permission(org_access.effective_permission_values(db, user=user), "contract:read"):
        return False
    c = db.get(Contract, contract_id)
    if c is None or c.org_id != user.org_id or c.deleted_at is not None:
        return False
    return not is_sealed_off(db, user=user, contract=c) and ContractAccessService(
        db
    ).user_can_access_contract(contract=c, user=user)


def request_activity(db: Session, *, user: User, request: IntakeRequest) -> dict:
    from app.workflows.service import get_run_for_request

    org = user.org_id
    cid = request.contract_id
    contract_ok = bool(cid) and _contract_visible_silently(db, user, cid)
    scope = and_(
        ResourceTimelineEvent.resource_type == "intake_request",
        ResourceTimelineEvent.resource_id == request.id,
    )
    if contract_ok:
        scope = or_(scope, and_(
            ResourceTimelineEvent.resource_type == "contract",
            ResourceTimelineEvent.resource_id == cid,
        ))
    events = db.scalars(
        select(ResourceTimelineEvent)
        .where(ResourceTimelineEvent.org_id == org, scope)
        .order_by(ResourceTimelineEvent.created_at.desc())
        .limit(CAP + 1)
    ).all()
    handoffs = db.scalars(
        select(IntakeHandoff).where(IntakeHandoff.org_id == org, IntakeHandoff.request_id == request.id)
    ).all()

    # Run comments follow today's run read gate: staff only, and a contract-bound run only
    # when its contract passes the same silent check.
    comments: list[dict] = []
    staff = is_org_admin(user) or has_permission(user.permission_values, "intake:read")
    run = get_run_for_request(db, request_id=request.id, org_id=org) if staff else None
    if run is not None and (
        not run.contract_id
        or (run.contract_id == cid and contract_ok)
        or (run.contract_id != cid and _contract_visible_silently(db, user, run.contract_id))
    ):
        comments = _comment_items(run, request.id)

    ids = {e.created_by_user_id for e in events} | {h.created_by_user_id for h in handoffs}
    ids |= {h.to_user_id for h in handoffs}
    ids.discard(None)
    names: dict[str, str] = {}
    if ids:
        names = {
            u.id: (u.full_name or u.email)
            for u in db.scalars(select(User).where(User.org_id == org, User.id.in_(ids)))
        }

    items = [
        _event_item(e, "request" if e.resource_type == "intake_request" else "contract", names)
        for e in events
    ]
    items += [_handoff_item(h, names) for h in handoffs] + comments
    items.sort(key=lambda i: i["at"], reverse=True)  # stable: ties keep timeline, hand-offs, comments
    return {"items": items[:CAP], "truncated": len(items) > CAP}
