"""Legal Intake service — the capture->triage->route->SLA->audit spine.

Status model (Part 0): status is one axis, sla_status another. There is no
'rejected' *status* — a rejected recommendation returns the request to
in_review (SLA still running). OPEN and TERMINAL are disjoint. closed_at is
stamped exactly once, inside _transition, so SLA evidence can't be rewritten by
updated_at. Every state change routes through _transition, which refuses edits
to a closed request and writes the immutable audit + timeline rows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.access import is_org_admin
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.intake.business_time import (
    business_deadline,
    business_ms_between,
    calendar_from_settings,
)

# --- status / stage constants (shared, see constants.py) -------------------
from app.intake.constants import (
    AT_RISK,
    OPEN_STATUSES,
    OVERDUE,
    SPINE_DEFAULT_MID,
    SPINE_HEAD,
    SPINE_TAIL,
    STAGE_LABELS,
    TERMINAL_STATUSES,
)
from app.intake.models import (
    IntakeHandoff,
    IntakeKbArticle,
    IntakeRequest,
    IntakeRequestField,
    IntakeRequestType,
    IntakeRoutingRule,
    IntakeTask,
    IntakeTeam,
)

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# --- small helpers ---------------------------------------------------------

logger = logging.getLogger(__name__)


def _next_ref(db: Session) -> str:
    n = db.execute(select(func.nextval("intake_ref_seq"))).scalar_one()
    return f"REQ-{n}"


def _user_label(db: Session, uid: str | None) -> str | None:
    if not uid:
        return None
    u = db.get(User, uid)
    return (u.full_name or u.email) if u else uid


def stages_for(rtype: IntakeRequestType | None) -> list[str]:
    mid = (rtype.stages if rtype and rtype.stages else None) or SPINE_DEFAULT_MID
    return SPINE_HEAD + list(mid) + SPINE_TAIL


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _elapsed_and_window_ms(r: IntakeRequest, now) -> tuple[float, float]:
    """Working time elapsed, and the working-time budget.

    Both sides are business time: `sla_hours` on a request means working hours,
    so the elapsed side has to be measured the same way or a Friday-evening
    request is overdue by Saturday lunchtime with nobody having been at a desk.
    """
    cal = calendar_from_settings()
    submitted = r.submitted_at or r.created_at or now
    end = r.closed_at if (r.status in TERMINAL_STATUSES and r.closed_at) else now
    paused = float(r.paused_ms_total or 0)
    if r.paused_at and not (r.status in TERMINAL_STATUSES):
        paused += business_ms_between(r.paused_at, now, cal)
    elapsed = max(0.0, business_ms_between(submitted, end, cal) - paused)
    window = max(0, r.sla_hours or 0) * 3_600_000.0
    return elapsed, window


def sla_pct(r: IntakeRequest, now=None) -> int:
    now = now or utcnow()
    elapsed, window = _elapsed_and_window_ms(r, now)
    if window <= 0:
        return 0
    return round(elapsed / window * 100)


def posture(r: IntakeRequest, now=None) -> str:
    if r.status in TERMINAL_STATUSES:
        return "on_track"
    frac = sla_pct(r, now) / 100.0
    if frac >= OVERDUE:
        return "overdue"
    if frac >= AT_RISK:
        return "at_risk"
    return "on_track"


# --- workflow derivation (serializer-only, never stored) -------------------

def _workflow(r: IntakeRequest, rtype: IntakeRequestType | None) -> list[dict]:
    stages = stages_for(rtype)
    cur = r.stage if r.stage in stages else stages[0]
    idx = stages.index(cur)
    steps = []
    for i, s in enumerate(stages):
        steps.append(
            {
                "label": STAGE_LABELS.get(s, s.replace("-", " ").replace("_", " ").title()),
                "stage": s,
                "done": i < idx or r.stage == "complete",
                "active": i == idx and r.stage != "complete",
            }
        )
    return steps


def _contract_title(db: Session, contract_id: str | None) -> str | None:
    if not contract_id:
        return None
    from app.contracts.models import Contract
    c = db.get(Contract, contract_id)
    return c.title if c else None


def serialize_request(db: Session, r: IntakeRequest) -> dict:
    from app.intake.screening import gather_parties
    rtype = db.get(IntakeRequestType, r.request_type_id) if r.request_type_id else None
    return {
        "id": r.id,
        "ref": r.ref,
        "source": r.source,
        "requester_user_id": r.requester_user_id,
        "requester_name": r.requester_name or _user_label(db, r.requester_user_id),
        "department": r.department,
        "request_type_id": r.request_type_id,
        "counterparty_id": r.counterparty_id,
        "legal_entity_id": r.legal_entity_id,
        "type_label": r.type_label,
        "subject": r.subject,
        "description": r.description,
        "field_values": r.field_values,
        "priority": r.priority,
        "status": r.status,
        "stage": r.stage,
        "work_status": r.work_status,
        "assigned_to_user_id": r.assigned_to_user_id,
        "assigned_to_label": _user_label(db, r.assigned_to_user_id),
        "approval_gate_user_id": r.approval_gate_user_id,
        "sla_hours": r.sla_hours,
        "sla_status": posture(r),
        "sla_pct": sla_pct(r),
        "submitted_at": _iso(r.submitted_at),
        "closed_at": _iso(r.closed_at),
        "triaged_by_user_id": r.triaged_by_user_id,
        "triage_action": r.triage_action,
        "ai_triage": r.ai_triage,
        "gates": _gates_summary(r),
        "fired_rules": r.fired_rules,
        "screening": r.screening,
        # Expose the EFFECTIVE parties screening actually uses (structured list,
        # else the legacy field_values.counterparty), so the Parties panel and
        # the Screening panel never contradict each other.
        "parties": gather_parties(r),
        "handoff_holder": r.handoff_holder,
        "handoff_user_id": r.handoff_user_id,
        "matter_id": r.matter_id,
        "contract_id": r.contract_id,
        "contract_title": _contract_title(db, r.contract_id),
        "workflow": _workflow(r, rtype),
        "created_at": _iso(r.created_at),
    }


def _gates_summary(r: IntakeRequest) -> dict:
    """Tier-0 gates for the UI: what the classifier detected, the human overrides,
    and the effective set that will force ladder rungs."""
    from app.intake import gates as gates_mod

    at = r.ai_triage or {}
    effective = gates_mod.effective_gate_keys(at)
    return {
        "detected": at.get("gates", []),
        "overrides": at.get("gate_overrides", []),
        "effective": [
            {"key": g.key, "label": g.label, "approver_group": g.approver_group}
            for g in gates_mod.effective_gates(at)
        ],
        "effective_keys": effective,
    }


# --- request-type CRUD -----------------------------------------------------

def serialize_type(t: IntakeRequestType) -> dict:
    return {
        "id": t.id,
        "key": t.key,
        "name": t.name,
        "workstream": t.workstream,
        "description": t.description,
        "active": t.active,
        "stages": t.stages,
        "sort_order": t.sort_order,
        "sla_hours": t.sla_hours,
        "form_key": t.form_key,
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "kind": f.kind,
                "required": f.required,
                "sort_order": f.sort_order,
                "options": f.options,
            }
            for f in sorted(t.fields, key=lambda f: f.sort_order)
        ],
    }


def list_types(db: Session, *, org_id: str, include_inactive: bool = False) -> list[dict]:
    # The agreement-wizard forms are request types too; make sure this org has
    # them before listing, so Operations shows them and filed requests link up.
    from app.intake.agreement_forms import ensure_agreement_types

    ensure_agreement_types(db, org_id)
    db.commit()
    q = select(IntakeRequestType).where(IntakeRequestType.org_id == org_id)
    if not include_inactive:
        q = q.where(IntakeRequestType.active.is_(True))
    rows = db.scalars(q.order_by(IntakeRequestType.sort_order, IntakeRequestType.name)).all()
    return [serialize_type(t) for t in rows]


def _apply_fields(db: Session, t: IntakeRequestType, org_id: str, fields) -> None:
    rows = []
    seen: set[str] = set()
    for f in fields:
        if not _KEY_RE.match(f.key):
            raise HTTPException(422, f"Invalid field key '{f.key}'")
        if f.key in seen:
            raise HTTPException(422, f"Duplicate field key '{f.key}'")
        seen.add(f.key)
        rows.append(
            IntakeRequestField(
                org_id=org_id, key=f.key, label=f.label, kind=f.kind,
                required=f.required, sort_order=f.sort_order, options=f.options,
            )
        )
    # Drop the old rows and flush the DELETEs *before* attaching the new ones.
    # Assigning straight over the collection leaves both sets pending in one
    # flush, where SQLAlchemy emits the INSERTs first and trips the
    # (request_type_id, key) unique constraint for any reused key — which is
    # every edit that isn't a wholesale rename.
    t.fields.clear()
    db.flush()
    t.fields = rows


def create_type(db: Session, *, actor: User, payload) -> dict:
    key = (payload.key or "").strip().lower()
    if not _KEY_RE.match(key):
        raise HTTPException(422, "Type key must be lowercase alphanumeric / dash / underscore")
    if db.scalar(
        select(IntakeRequestType.id).where(
            IntakeRequestType.org_id == actor.org_id, IntakeRequestType.key == key
        )
    ):
        raise HTTPException(409, f'A request type "{key}" already exists')
    t = IntakeRequestType(
        org_id=actor.org_id, key=key, name=payload.name.strip(),
        workstream=(payload.workstream or None), description=(payload.description or None),
        stages=payload.stages or None, sort_order=payload.sort_order,
        sla_hours=payload.sla_hours,
        created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    _apply_fields(db, t, actor.org_id, payload.fields)
    db.add(t)
    db.flush()
    write_audit_log(db, action="intake.request_type.created", resource_type="intake_request_type",
                    resource_id=t.id, org_id=actor.org_id, actor_user_id=actor.id,
                    after={"key": t.key, "name": t.name})
    db.commit()
    db.refresh(t)
    return serialize_type(t)


def _get_type(db: Session, org_id: str, type_id: str) -> IntakeRequestType:
    t = db.get(IntakeRequestType, type_id)
    if t is None or t.org_id != org_id:
        raise HTTPException(404, "Request type not found")
    return t


def update_type(db: Session, *, actor: User, type_id: str, payload) -> dict:
    t = _get_type(db, actor.org_id, type_id)
    for attr in ("name", "workstream", "description", "sort_order", "active", "stages",
                 "sla_hours"):
        val = getattr(payload, attr, None)
        if val is not None:
            setattr(t, attr, val)
    # An agreement form's fields belong to the wizard's code; editing them here
    # would be overwritten on the next sync, so they are left as they are.
    if payload.fields is not None and not t.form_key:
        _apply_fields(db, t, actor.org_id, payload.fields)
    t.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(db, action="intake.request_type.updated", resource_type="intake_request_type",
                    resource_id=t.id, org_id=actor.org_id, actor_user_id=actor.id,
                    after={"key": t.key})
    db.commit()
    db.refresh(t)
    return serialize_type(t)


def delete_type(db: Session, *, actor: User, type_id: str) -> None:
    t = _get_type(db, actor.org_id, type_id)
    if t.form_key:
        raise HTTPException(409, "Agreement forms can't be deleted — deactivate the type instead")
    write_audit_log(db, action="intake.request_type.deleted", resource_type="intake_request_type",
                    resource_id=t.id, org_id=actor.org_id, actor_user_id=actor.id,
                    before={"key": t.key})
    db.delete(t)  # fields cascade
    db.commit()


# --- transition choke-point ------------------------------------------------

def _stamp_stage(r: IntakeRequest, stage: str) -> None:
    hist = list(r.stage_timestamps or [])
    hist.append({"stage": stage, "at": utcnow().isoformat()})
    r.stage_timestamps = hist


def _transition(
    db: Session, *, request: IntakeRequest, actor: User | None,
    to_status: str | None = None, to_stage: str | None = None,
    audit_action: str, before: dict | None = None, after: dict | None = None,
    timeline_title: str | None = None, actor_type: str = "user", request_id: str | None = None,
) -> None:
    """The single seam every intake state change routes through. Refuses edits to
    a closed request; stamps closed_at once on entering terminal; appends stage
    history; writes the immutable audit + timeline rows. Commit stays with the
    caller (one commit per API call)."""
    if request.status == "closed":
        raise HTTPException(409, "Request is closed — file a follow-up")
    if to_stage and to_stage != request.stage:
        request.stage = to_stage
        _stamp_stage(request, to_stage)
    if to_status and to_status != request.status:
        request.status = to_status
        if to_status in TERMINAL_STATUSES and request.closed_at is None:
            request.closed_at = utcnow()
        if to_status == "closed":
            from app.workflows.service import cancel_runs_for_request

            cancel_runs_for_request(db, request_id=request.id, org_id=request.org_id,
                                    actor_user_id=actor.id if actor else None)
    if actor:
        request.updated_by_user_id = actor.id
    write_audit_log(
        db, action=audit_action, resource_type="intake_request", resource_id=request.id,
        org_id=request.org_id, actor_user_id=(actor.id if actor and actor_type == "user" else None),
        request_id=request_id, before=before, after=after,
        metadata={"actor_type": actor_type} if actor_type != "user" else None,
    )
    write_timeline_event(
        db, org_id=request.org_id, resource_type="intake_request", resource_id=request.id,
        event_type=audit_action, title=timeline_title or audit_action,
        actor_user_id=(actor.id if actor and actor_type == "user" else None),
        request_id=request_id, details=after,
    )


# --- field validation ------------------------------------------------------

_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}


def _coerce_field(f: IntakeRequestField, raw):
    """Return ``raw`` as the type the field declares, or raise 422.

    Forms post everything as strings, so a `number` arrives as "1500" and a
    `boolean` as "true". Those are coerced rather than rejected — the value is
    unambiguous and refusing it would break every HTML form. Anything that
    isn't the declared type is refused outright: the point of a typed field is
    that what comes back out is the type it claims.
    """
    kind = (f.kind or "text").lower()

    if kind == "number":
        if isinstance(raw, bool):  # bool is an int subclass; not a number here
            raise HTTPException(422, f'Field "{f.label}" must be a number')
        if isinstance(raw, int | float):
            return raw
        try:
            text = str(raw).strip().replace(",", "")
            return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f'Field "{f.label}" must be a number') from exc

    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise HTTPException(422, f'Field "{f.label}" must be true or false')

    if kind == "date":
        text = str(raw).strip()
        try:
            # Stored as an ISO date string: the column is JSON, and a date
            # object would not survive the round trip.
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError as exc:
            raise HTTPException(
                422, f'Field "{f.label}" must be a date in YYYY-MM-DD form'
            ) from exc

    if kind == "select":
        allowed = [
            str(o.get("value")) for o in (f.options or []) if isinstance(o, dict) and "value" in o
        ]
        text = str(raw).strip()
        if allowed and text not in allowed:
            raise HTTPException(
                422, f'Field "{f.label}" must be one of: {", ".join(allowed)}'
            )
        return text

    # text / textarea. Capped so a pasted contract cannot land in a label-sized
    # field and push the row past what the JSON column should carry.
    text = str(raw)
    if len(text) > _FIELD_TEXT_MAX:
        raise HTTPException(
            422, f'Field "{f.label}" is longer than {_FIELD_TEXT_MAX} characters'
        )
    return text


_FIELD_TEXT_MAX = 20_000


def _validate_field_values(rtype: IntakeRequestType | None, values: dict | None) -> dict | None:
    """Enforce what the request type declares, and return the cleaned values.

    Previously this checked required-ness and nothing else, so a `number` field
    accepted "banana" and a `select` accepted any string at all — the form
    builder promised typed fields and stored untyped JSON. Anything reading
    those values back (arithmetic, date comparison, filtering by option) was
    working on whatever the caller happened to send.

    Keys the type does not declare are passed through untouched: channel intake
    legitimately stores `channel_from`, `counterparty` and `gmail_thread_id`
    alongside the declared fields, and rejecting them would break email and
    Gmail ingestion.
    """
    if rtype is None:
        return values
    cleaned = dict(values or {})
    for f in rtype.fields:
        raw = cleaned.get(f.key)
        missing = raw is None or (isinstance(raw, str) and not raw.strip())
        if missing:
            if f.required:
                raise HTTPException(422, f'Field "{f.label}" is required')
            # An optional field left blank is absent, not an empty string —
            # otherwise "" would later fail the type check it skipped here.
            cleaned.pop(f.key, None)
            continue
        cleaned[f.key] = _coerce_field(f, raw)
    return cleaned


# --- request create / list / get -------------------------------------------

def _compute_intake_analysis(db: Session, request: IntakeRequest) -> None:
    """Populate ai_triage.flow_suggestion for every request; for litigation-
    category requests, run the Litigation Intake Agent instead — its richer
    assessment lands on ai_triage.litigation_assessment and its extracted facts
    pre-fill the litigation flow's branch answers. Mutates request; caller commits."""
    from app.intake.litigation_agent import assess_litigation, branch_fields, is_litigation

    at = dict(request.ai_triage or {})
    if is_litigation(request):
        assessment = assess_litigation(db, request)
        at["flow_suggestion"] = assessment.pop("flow_suggestion")
        at["litigation_assessment"] = assessment
        # Pre-fill the ladder's conditional-step answers without clobbering any
        # the requester supplied.
        fv = dict(request.field_values or {})
        for k, v in branch_fields(assessment).items():
            fv.setdefault(k, v)
        request.field_values = fv
    else:
        from app.intake import triage_agent

        # Re-run the full context-aware triage; merge so gates/overrides survive.
        at.update(triage_agent.triage(db, request))
        at.pop("litigation_assessment", None)
    request.ai_triage = at  # reassign so SQLAlchemy tracks the JSON mutation


def _attach_flow_suggestion(db: Session, request: IntakeRequest) -> None:
    """Suggest — never start — the workflow this request should ride (+ litigation
    assessment for disputes). Best-effort; runs post-commit so it owns its own
    transaction and never breaks intake."""
    try:
        _compute_intake_analysis(db, request)
        db.commit()
    except Exception:
        db.rollback()
        import logging
        logging.getLogger(__name__).warning(
            "flow suggestion on create failed for %s", request.id, exc_info=True
        )


def resuggest_flow(db: Session, *, actor: User, request_id: str) -> dict:
    """Re-run the router / litigation agent on demand (ticket 'Re-suggest')."""
    r = get_request(db, user=actor, request_id=request_id)
    _require_staff(actor)
    _compute_intake_analysis(db, r)
    db.commit()
    db.refresh(r)
    return serialize_request(db, r)


def _derive_subject(subject: str | None, description: str | None, type_label: str) -> str:
    """A short human title for a request: the explicit subject if given, else the
    first non-empty line of the description, else the type label. Capped at 200."""
    if subject and subject.strip():
        return subject.strip()[:200]
    for line in (description or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return (type_label or "").strip()[:200]


def _pick_owner_team(db: Session, *, org_id: str, category: str | None,
                     department: str | None, complexity: str):
    """The team that should own this request: the one whose expertise covers the
    matter category, narrowed to the team that serves the request's department
    when there's a match. Falls back to the complexity→tier heuristic when
    nothing has expertise for the category (so orgs that haven't tagged teams
    still get an owner)."""
    from sqlalchemy import func
    from sqlalchemy import select as _select

    from app.intake.models import IntakeTeam

    teams = db.scalars(
        _select(IntakeTeam)
        .where(IntakeTeam.org_id == org_id, IntakeTeam.active.is_(True))
        .order_by(IntakeTeam.sort_order, IntakeTeam.name)
    ).all()

    # 1. expertise: teams that own this matter category
    cands = [t for t in teams if category and category in (t.expertise or [])]
    # 2. department: prefer a candidate team that serves the request's business
    #    unit; if none does, department stays a soft signal (keep the matches).
    if department:
        dept = department.strip().lower()
        dept_match = [t for t in cands
                      if any((d or "").strip().lower() == dept for d in (t.departments or []))]
        if dept_match:
            cands = dept_match
    if cands:
        return cands[0]  # sort_order wins; pick_from_pool balances members within

    # 3. fallback: complexity → tier
    key = "tier1" if complexity == "simple" else "tier2"
    return db.scalar(_select(IntakeTeam).where(
        IntakeTeam.org_id == org_id, IntakeTeam.active.is_(True), func.lower(IntakeTeam.key) == key))


def _assign_owner_from_triage(db: Session, request: IntakeRequest) -> None:
    """Auto-assign the request owner from the triage read — replaces the keyword
    routing rules. Routes by matter-type EXPERTISE and business unit (with
    complexity→tier as the fallback); pick_from_pool balances by load. Never
    overrides a human decision or an existing assignee; best-effort — no eligible
    pool means the request just waits in the queue."""
    if request.triaged_by_user_id or request.triage_action or request.assigned_to_user_id:
        return
    from app.intake import teams as teams_mod

    at = request.ai_triage or {}
    category = at.get("category")
    complexity = at.get("complexity") or "standard"
    department = request.department or (at.get("understanding") or {}).get("business_unit")
    team = _pick_owner_team(db, org_id=request.org_id, category=category,
                            department=department, complexity=complexity)
    if not team:
        return
    pick = teams_mod.pick_from_pool(db, team_id=team.id)
    if pick and pick.user_id:
        request.assigned_to_user_id = pick.user_id
        request.handoff_holder = "human"
        request.handoff_user_id = pick.user_id


def _maybe_autostart_workflow(db: Session, request: IntakeRequest, actor: User) -> None:
    """Confidence-gated auto-start: when the triage is confident about the
    workflow pick (and didn't flag it for a human), kick the workflow off
    automatically; otherwise it stays a one-click suggestion on the ticket.
    Best-effort and post-commit — never breaks the create path."""
    at = request.ai_triage or {}
    # Completeness gate: never auto-draft a request the triage flagged as missing
    # critical info — hold it for the requester to complete first.
    if at.get("needs_info"):
        return
    fs = at.get("flow_suggestion") or {}
    fid = fs.get("flow_id")
    if not fid or fs.get("needs_human") or (fs.get("confidence") or 0.0) < 0.75:
        return
    try:
        from app.integrations.claude import run_coro_blocking
        from app.workflows.models import Workflow
        from app.workflows.service import start_flow

        flow = db.get(Workflow, fid)
        if not flow or flow.org_id != request.org_id:
            return
        run_coro_blocking(lambda: start_flow(db, actor=actor, request=request, flow=flow))
        db.commit()
    except Exception:
        db.rollback()
        import logging
        logging.getLogger(__name__).warning("workflow auto-start failed for %s", request.id, exc_info=True)


def create_request(db: Session, *, actor: User, payload, request_id: str | None = None,
                   conversation: list | None = None, defer_triage: bool = False,
                   external_message_id: str | None = None) -> dict:
    """File a request.

    ``external_message_id`` is a channel's idempotency key and MUST be set here
    rather than stamped on afterwards: the row is committed inside this
    function, so a key applied later leaves a window in which the request
    exists with nothing to dedupe against. The partial unique index
    ``uq_intake_request_org_extmsg`` then does the real work — callers catch
    IntegrityError and treat it as "already filed".
    """
    rtype = None
    if payload.request_type_id:
        rtype = _get_type(db, actor.org_id, payload.request_type_id)
    else:
        # The agreement wizard identifies its form by `request_form`; resolve it
        # to the backing request type so its required fields are enforced here
        # and not only in the browser.
        from app.intake.agreement_forms import type_for_form

        rtype = type_for_form(db, actor.org_id, (payload.field_values or {}).get("request_form"))
    field_values = _validate_field_values(rtype, payload.field_values)
    counterparty_id, legal_entity_id = _resolve_party_records(db, actor.org_id, field_values)
    # Check every attached file before anything is saved: a file that is too
    # big or the wrong type must stop the filing, not leave a request behind
    # without its attachment (which is what made people retry and duplicate).
    raw = [(a, _decode_b64(a.content_b64, a.filename)) for a in (getattr(payload, "attachments", None) or [])]
    if sum(len(content) for _, content in raw) > _FILING_MAX_BYTES:
        raise HTTPException(413, "Attachments add up to more than 40 MB — file the largest ones separately")
    attachments = [_prepare_attachment(a.filename, a.mime_type, content) for a, content in raw]
    if defer_triage:
        duplicate = _recent_duplicate_request(db, actor=actor, payload=payload,
                                              field_values=field_values)
        if duplicate is not None:
            return serialize_request(db, duplicate)
    now = utcnow()
    r = IntakeRequest(
        org_id=actor.org_id, ref=_next_ref(db), source=payload.source,
        requester_user_id=actor.id, requester_name=payload.requester_name,
        department=payload.department, request_type_id=(rtype.id if rtype else None),
        type_label=payload.type_label.strip(),
        subject=_derive_subject(getattr(payload, "subject", None), payload.description, payload.type_label),
        description=payload.description or "",
        field_values=field_values, priority=payload.priority,
        status="open", stage="new",
        sla_hours=(rtype.sla_hours if rtype and rtype.sla_hours
                   else settings.intake_default_sla_hours),
        external_message_id=external_message_id,
        counterparty_id=counterparty_id, legal_entity_id=legal_entity_id,
        submitted_at=now, handoff_holder="queue", conversation=conversation,
        stage_timestamps=[{"stage": "new", "at": now.isoformat()}],
        created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    # Context-aware triage first: the AI reads the WHOLE request and decides
    # category/complexity/risk/urgency + the workflow pick, driving the gates,
    # priority and owner below. Falls back to the keyword classifier on failure.
    cp = (field_values or {}).get("counterparty")
    # Seed the parties list from the captured counterparty so it's editable and
    # conflict-screenable; adverse/related parties can be added later.
    if cp and str(cp).strip():
        party = {"name": str(cp).strip(), "role": "counterparty", "is_person": False}
        if counterparty_id:
            party["counterparty_id"] = counterparty_id
        r.parties = [party]
    if defer_triage:
        r.ai_triage = {"status": "pending"}  # run_intake_triage fills this in the background
    else:
        _apply_ai_triage(db, r)
    db.add(r)
    db.flush()
    # Saved in the same transaction as the request and before triage is queued,
    # so triage can read what was attached.
    for att in attachments:
        _attach(db, actor=actor, r=r, att=att)
    write_audit_log(db, action="intake.created", resource_type="intake_request", resource_id=r.id,
                    org_id=actor.org_id, actor_user_id=actor.id, request_id=request_id,
                    after={"ref": r.ref, "type": r.type_label, "priority": r.priority})
    write_timeline_event(db, org_id=actor.org_id, resource_type="intake_request", resource_id=r.id,
                         event_type="intake.created", title=f"Request filed — {r.type_label}",
                         actor_user_id=actor.id, request_id=request_id)
    # Conflicts/sanctions screening is queued HERE, inside the request's own
    # transaction: if the request exists, its screening job exists. Queued
    # after the enrichment that used to run it inline, it can no longer be
    # lost to a crash or swallowed by a bare except.
    r.screening = {"status": "pending", "note": "Screening queued."}
    screening_job = queue_screening(db, r, actor.id)
    if defer_triage:
        from app.jobs.service import create_job, dispatch_job

        job = create_job(
            db, org_id=actor.org_id, job_type="intake_triage", resource_type="intake_request",
            resource_id=r.id, created_by_user_id=actor.id, idempotency_key=f"intake_triage:{r.id}",
            metadata={"intake_request_id": r.id},
        )
        db.commit()
        db.refresh(r)
        try:
            dispatch_job(db, job=job)
            db.commit()
        except Exception:
            # The request is saved; the reclaim sweep re-dispatches the queued job.
            logger.warning("could not dispatch intake triage for request %s", r.id, exc_info=True)
        dispatch_screening(db, screening_job)
        return serialize_request(db, r)
    _assign_and_notify(db, r, actor)
    db.commit()
    db.refresh(r)
    dispatch_screening(db, screening_job)
    _enrich_after_commit(db, r, actor)
    return serialize_request(db, r)


def _apply_ai_triage(db: Session, r: IntakeRequest) -> None:
    """Context-aware triage: the AI reads the WHOLE request and decides category,
    complexity, risk, urgency and the workflow pick, which drive the gates,
    priority and owner. Tier-0 hard gates then force senior rungs into the
    approval ladder (litigation etc. escalate here, before routing runs)."""
    from app.intake import gates as gates_mod
    from app.intake import triage_agent

    r.ai_triage = triage_agent.triage(db, r)
    _urgency = (r.ai_triage.get("understanding") or {}).get("urgency")
    if _urgency in ("Low", "Medium", "High"):
        r.priority = _urgency
    r.ai_triage = {**r.ai_triage, "gates": gates_mod.classify_gates(db, r), "gate_overrides": []}
    gates_mod.apply_gate_side_effects(r)


def _assign_and_notify(db: Session, r: IntakeRequest, actor: User) -> None:
    """Auto-assign the owner from the triage read: complexity picks the tier pool,
    least-loaded within."""
    _assign_owner_from_triage(db, r)
    if r.assigned_to_user_id and r.assigned_to_user_id != actor.id:
        _notify(db, r, r.assigned_to_user_id, "intake.assigned",
                f"{r.ref} assigned to you", f"{r.type_label} — priority {r.priority}.")


def _enrich_after_commit(db: Session, r: IntakeRequest, actor: User) -> None:
    """Enrichment once the request is safely persisted: the deep litigation
    assessment and the confidence-gated workflow autostart.

    Screening is NOT here any more — it is a durable job queued in the
    request's own transaction and dispatched at creation, so it can no longer
    be lost to a crash on this path."""
    from app.intake.litigation_agent import is_litigation

    if is_litigation(r):
        _attach_flow_suggestion(db, r)
    _maybe_autostart_workflow(db, r, actor)


def run_intake_triage(db: Session, *, request_id: str, actor_id: str | None) -> None:
    """The background half of a web-form submission: AI triage, gates, owner
    assignment, screening and workflow autostart. A no-op once triage has run."""
    r = db.get(IntakeRequest, request_id)
    actor = db.get(User, actor_id) if actor_id else None
    if r is None or actor is None:
        raise RuntimeError("Intake request or requester not found for triage")
    if (r.ai_triage or {}).get("status") != "pending":
        return
    _apply_ai_triage(db, r)
    _assign_and_notify(db, r, actor)
    write_timeline_event(db, org_id=r.org_id, resource_type="intake_request", resource_id=r.id,
                         event_type="intake.triaged", title="AI triage complete", actor_user_id=actor.id)
    db.commit()
    db.refresh(r)
    _enrich_after_commit(db, r, actor)


# (id key, name key, record kind, label) for the wizard's party lookups.
_PARTY_REFS = (
    ("entity_id", "entity", "legal_entity", "legal entity"),
    ("entity_2_id", "entity_2", "legal_entity", "second legal entity"),
    ("counterparty_id", "counterparty", "counterparty", "counterparty"),
    ("counterparty_2_id", "counterparty_2", "counterparty", "second counterparty"),
)


def _resolve_party_records(db: Session, org_id: str, field_values: dict | None) -> tuple[str | None, str | None]:
    """Check every picked entity / counterparty exists in this org and is active,
    and store its registered name next to the id (the browser's copy of the
    name is not trusted). Returns (counterparty_id, legal_entity_id)."""
    from app.parties import service as parties

    fv = field_values if field_values is not None else {}
    for id_key, name_key, kind, label in _PARTY_REFS:
        rid = fv.get(id_key)
        if not rid:
            continue
        row = parties.get(db, org_id=org_id, kind=kind, record_id=str(rid))
        if row is None or not row.active:
            raise HTTPException(422, f"The selected {label} is no longer in the register — pick it again")
        fv[name_key] = row.name
    return (fv.get("counterparty_id") or None), (fv.get("entity_id") or None)


def _recent_duplicate_request(db: Session, *, actor: User, payload,
                              field_values: dict | None = None) -> IntakeRequest | None:
    """The same person filing the same request again within a couple of minutes is a
    retry (a cut connection, a double click), not a second ticket.

    "The same" means every answer matches, not just the type and the note: two
    different agreements filed with the same quick-phrase note used to be merged
    into the first, silently losing the second."""
    same_answers = func.coalesce(cast(IntakeRequest.field_values, JSONB), cast({}, JSONB)) == cast(
        field_values or {}, JSONB
    )
    return db.scalar(
        select(IntakeRequest)
        .where(
            IntakeRequest.org_id == actor.org_id,
            IntakeRequest.requester_user_id == actor.id,
            IntakeRequest.type_label == payload.type_label.strip(),
            IntakeRequest.description == (payload.description or ""),
            IntakeRequest.subject == _derive_subject(getattr(payload, "subject", None),
                                                     payload.description, payload.type_label),
            same_answers,
            IntakeRequest.submitted_at >= utcnow() - timedelta(minutes=2),
        )
        .order_by(IntakeRequest.submitted_at.desc())
        .limit(1)
    )


def _decode_b64(content_b64: str, filename: str) -> bytes:
    import base64
    import binascii

    try:
        return base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, f'"{filename}" could not be read — please attach it again') from exc


_PARTY_ROLES = {"counterparty", "adverse", "related", "our_side"}


def set_parties(db: Session, *, actor: User, request_id: str, parties: list) -> dict:
    """Replace a request's parties, then re-run screening so conflicts/relationship
    reflect the new party set (e.g. an adverse party added to a dispute)."""
    r = get_request(db, user=actor, request_id=request_id)
    clean = []
    for p in parties or []:
        name = (getattr(p, "name", None) or (p.get("name") if isinstance(p, dict) else "") or "").strip()
        if not name:
            continue
        role = (getattr(p, "role", None) or (p.get("role") if isinstance(p, dict) else None) or "counterparty")
        role = role if role in _PARTY_ROLES else "related"
        is_person = bool(getattr(p, "is_person", None) if not isinstance(p, dict) else p.get("is_person"))
        clean.append({"name": name[:255], "role": role, "is_person": is_person})
    # Persist the explicit list — even when empty. Storing None here would let
    # gather_parties fall back to field_values.counterparty, so a removed party
    # would keep getting re-screened. [] means "the reviewer cleared the parties".
    r.parties = clean
    write_audit_log(db, action="intake.parties.updated", resource_type="intake_request",
                    resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                    after={"count": len(clean)})
    # A changed party list must actually re-screen, so this job is deliberately
    # NOT idempotent against the original — folding into it would return the
    # verdict for the parties the reviewer just replaced.
    r.screening = {"status": "pending", "note": "Re-screening queued."}
    rescreen_job = queue_screening(db, r, actor.id, idempotent=False)
    db.commit()
    db.refresh(r)
    dispatch_screening(db, rescreen_job)
    _attach_flow_suggestion(db, r)
    return serialize_request(db, r)


def queue_screening(db: Session, r: IntakeRequest, actor_id: str | None,
                    *, idempotent: bool = True):
    """Enqueue conflicts/sanctions screening as a durable job.

    Screening used to run inline after the request had already been committed,
    in a try/except that wrote ``{"status": "error"}`` and stopped there. Two
    ways a request ended up permanently unscreened: the process dying between
    the commit and the enrichment, and the screen simply failing — with no
    retry, no queue and no way to find the requests it had happened to. An
    unscreened request looked exactly like one that had passed.

    As a job it is durable instead: the row is written in the SAME transaction
    as the request, so if the request exists the job exists. A dispatch that
    fails leaves it QUEUED for the reclaim sweep, and a run that fails leaves
    it FAILED and visible rather than silent.

    ``idempotent=False`` for a re-screen (the party list changed), where the
    point is to run again rather than fold into the original job.
    """
    from app.jobs.service import create_job

    return create_job(
        db, org_id=r.org_id, job_type="intake_screening",
        resource_type="intake_request", resource_id=r.id,
        created_by_user_id=actor_id,
        idempotency_key=f"intake_screening:{r.id}" if idempotent else None,
        metadata={"intake_request_id": r.id},
    )


def dispatch_screening(db: Session, job) -> None:
    """Hand a committed screening job to a worker.

    Best-effort on purpose: the row is already durable, so a broker that is
    down costs a few minutes rather than the screen — `reclaim_stale_jobs`
    re-dispatches anything left QUEUED.
    """
    from app.jobs.service import dispatch_job

    if job is None:
        return
    try:
        dispatch_job(db, job=job)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("could not dispatch screening job %s", getattr(job, "id", "?"),
                       exc_info=True)


def run_intake_screening(db: Session, *, request_id: str, actor_id: str | None) -> None:
    """Job body. Exceptions propagate on purpose — a failed screen must fail
    its job, so it is retried and then visible, not swallowed into a status
    field nobody queries."""
    from app.intake.screening import run_screening

    r = db.get(IntakeRequest, request_id)
    if r is None:
        return  # request deleted before the job ran; nothing to screen
    run_screening(db, r, actor_user_id=actor_id)


def _notify(db: Session, r: IntakeRequest, user_id: str | None, event_type: str,
            subject: str, body: str) -> None:
    """Best-effort in-app notification on intake events (never raises)."""
    if not user_id:
        return
    try:
        from app.notifications.models import Notification
        db.add(Notification(org_id=r.org_id, user_id=user_id, channel="in_app",
                            event_type=event_type, subject=subject, body=body, status="sent"))
    except Exception:
        import logging
        logging.getLogger(__name__).debug(
            "in-app notification failed for user %s", user_id, exc_info=True
        )


def _accessible(user: User):
    """Staff (intake:read) see the whole org queue; everyone else only their own."""
    from app.core.rbac import has_permission

    base = IntakeRequest.org_id == user.org_id
    if is_org_admin(user) or has_permission(user.permission_values, "intake:read"):
        return base
    return base & (IntakeRequest.requester_user_id == user.id)


def list_requests(db: Session, *, user: User, status_filter: str | None = None,
                  mine: bool = False) -> list[dict]:
    q = select(IntakeRequest).where(_accessible(user))
    if mine:
        q = q.where(IntakeRequest.requester_user_id == user.id)
    if status_filter:
        q = q.where(IntakeRequest.status == status_filter)
    rows = db.scalars(q.order_by(IntakeRequest.submitted_at.desc())).all()
    _prefetch_serialize_dependencies(db, rows)
    return [serialize_request(db, r) for r in rows]


def _prefetch_serialize_dependencies(db: Session, rows: list[IntakeRequest]) -> None:
    """Warm the session identity map so serialize_request's per-row db.get()
    calls (request type, requester/assignee labels, contract title) hit the
    map instead of issuing one query per row — this was an N+1 on the main
    Legal Intake queue. Behavior-preserving: serialize_request is unchanged,
    db.get() just becomes a cache hit for anything fetched here."""
    from app.contracts.models import Contract

    type_ids = {r.request_type_id for r in rows if r.request_type_id}
    user_ids = {r.requester_user_id for r in rows if r.requester_user_id} | {
        r.assigned_to_user_id for r in rows if r.assigned_to_user_id
    }
    contract_ids = {r.contract_id for r in rows if r.contract_id}
    if type_ids:
        db.scalars(select(IntakeRequestType).where(IntakeRequestType.id.in_(type_ids))).all()
    if user_ids:
        db.scalars(select(User).where(User.id.in_(user_ids))).all()
    if contract_ids:
        db.scalars(select(Contract).where(Contract.id.in_(contract_ids))).all()


def get_request(db: Session, *, user: User, request_id: str) -> IntakeRequest:
    r = db.get(IntakeRequest, request_id)
    if r is None or r.org_id != user.org_id:
        raise HTTPException(404, "Request not found")
    from app.core.rbac import has_permission

    can_read = is_org_admin(user) or has_permission(user.permission_values, "intake:read")
    if not can_read and r.requester_user_id != user.id:
        raise HTTPException(404, "Request not found")
    return r


# --- stage/field update (T10) ----------------------------------------------

HOLDERS = {"agent", "human", "queue"}


def _require_staff(user: User) -> None:
    from app.core.rbac import has_permission

    if not (is_org_admin(user) or has_permission(user.permission_values, "intake:read")):
        raise HTTPException(403, "Not authorized")


# --- handoff / custody ledger ----------------------------------------------

def _validate_handoff(r: IntakeRequest, to_holder: str, to_user_id: str | None) -> None:
    if to_holder not in HOLDERS:
        raise HTTPException(422, "Invalid holder")
    if to_holder == "human" and not to_user_id:
        raise HTTPException(422, "A human hand-off needs a target person")
    cur = r.handoff_holder
    if cur is None:
        return
    if to_holder == cur:
        if to_holder == "human" and to_user_id and to_user_id != r.handoff_user_id:
            return  # reassignment between people is a real pass
        raise HTTPException(422, f"Already held by {cur}")


def record_handoff(db: Session, *, actor: User | None, request: IntakeRequest, to_holder: str,
                   to_user_id: str | None = None, reason: str | None = None,
                   actor_type: str = "user", sync_assignee: bool = True) -> IntakeHandoff:
    if request.status == "closed":
        raise HTTPException(409, "Request is closed — file a follow-up")
    if actor_type == "user":
        _validate_handoff(request, to_holder, to_user_id)
    row = IntakeHandoff(
        org_id=request.org_id, request_id=request.id, from_holder=request.handoff_holder,
        to_holder=to_holder, to_user_id=(to_user_id if to_holder == "human" else None),
        reason=reason, actor_type=actor_type,
        created_by_user_id=(actor.id if actor and actor_type == "user" else None),
    )
    db.add(row)
    before = {"holder": request.handoff_holder, "user_id": request.handoff_user_id}
    request.handoff_holder = to_holder
    request.handoff_user_id = to_user_id if to_holder == "human" else None
    if to_holder == "human" and sync_assignee and to_user_id:
        request.assigned_to_user_id = to_user_id
    write_audit_log(db, action="intake.handoff", resource_type="intake_request",
                    resource_id=request.id, org_id=request.org_id,
                    actor_user_id=(actor.id if actor and actor_type == "user" else None),
                    before=before, after={"holder": to_holder, "user_id": to_user_id},
                    metadata={"actor_type": actor_type})
    return row


def serialize_handoff(db: Session, h: IntakeHandoff) -> dict:
    return {
        "id": h.id, "from_holder": h.from_holder, "to_holder": h.to_holder,
        "to_user_id": h.to_user_id, "to_label": _user_label(db, h.to_user_id),
        "reason": h.reason, "actor_type": h.actor_type, "created_at": _iso(h.created_at),
    }


def list_handoffs(db: Session, *, request: IntakeRequest) -> list[dict]:
    rows = db.scalars(
        select(IntakeHandoff).where(IntakeHandoff.request_id == request.id)
        .order_by(IntakeHandoff.created_at)
    ).all()
    return [serialize_handoff(db, h) for h in rows]


def handoff(db: Session, *, actor: User, request_id: str, payload) -> dict:
    r = get_request(db, user=actor, request_id=request_id)
    record_handoff(db, actor=actor, request=r, to_holder=payload.to_holder,
                   to_user_id=payload.to_user_id, reason=payload.reason,
                   sync_assignee=payload.sync_assignee)
    db.commit()
    db.refresh(r)
    return serialize_request(db, r)


# --- triage actions (Phase 0 subset: reassign / close / snooze / escalate) --

@dataclass
class _AuthorityShim:
    """The six attributes enforce_authority._grant_covers reads (Part 0.6).
    Intake approve grants use max_value=None + allowed_contract_types keyed to
    the intake type key/label."""
    contract_type: str | None
    value_amount: float | None = None
    currency: str | None = None
    jurisdiction: str | None = None
    risk_band: str | None = None
    risk_level: str | None = None


def _approval_gate_blocked(db: Session, *, actor: User, request: IntakeRequest,
                           attempted: str, http_request_id: str | None) -> None:
    """Part 0.5 — a 🔒 gate names the only user whose approve sticks; anyone else
    is refused and the refusal is logged on an isolated session (survives the
    403 rollback) plus an intake audit row."""
    gate = request.approval_gate_user_id
    if not gate or actor.id == gate or is_org_admin(actor):
        return
    from app.core.authz import record_decision

    record_decision(user=actor, action="intake:approve", outcome="denied",
                    resource_type="intake_request", resource_id=request.id,
                    reason="approval_gate", request_id=http_request_id)
    write_audit_log(db, action="intake.approval_blocked", resource_type="intake_request",
                    resource_id=request.id, org_id=actor.org_id, actor_user_id=actor.id,
                    before={"triage_action": request.triage_action},
                    after={"attempted_action": attempted, "required_approver_id": gate},
                    metadata={"source": "approval-gate"})
    db.commit()  # persist the intake audit row before the 403 rollback
    raise HTTPException(403, "Approval is gated to a specific person for this request")


def record_triage_action(db: Session, *, actor: User, request_id: str, payload,
                         http_request_id: str | None = None) -> dict:
    """Request-management actions on a ticket: reassign, escalate, snooze, close.
    (Triage verdicts removed — a request is resolved by its workflow / approval
    ladder, not by approving an AI recommendation.)"""
    r = get_request(db, user=actor, request_id=request_id)
    _require_staff(actor)
    if r.status == "closed":
        raise HTTPException(409, "Request is closed — file a follow-up")
    action = payload.action

    if action == "reassigned":
        if not payload.assignee_user_id:
            raise HTTPException(422, "Reassign needs a target person")
        target = db.get(User, payload.assignee_user_id)
        if target is None or target.org_id != actor.org_id:
            raise HTTPException(404, "Target user not found")
        record_handoff(db, actor=actor, request=r, to_holder="human",
                       to_user_id=target.id, reason="Manually reassigned")
        r.triaged_by_user_id = actor.id
        r.triaged_at = utcnow()
        r.triage_action = "reassigned"
        write_audit_log(db, action="intake.assigned", resource_type="intake_request",
                        resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                        request_id=http_request_id, after={"assignee": target.id})

    elif action == "manual_close":
        r.triaged_by_user_id = actor.id
        r.triaged_at = utcnow()
        r.triage_action = "manual_close"
        _transition(db, request=r, actor=actor, to_status="closed", to_stage="complete",
                    audit_action="intake.closed", after={"triage_action": "manual_close"},
                    timeline_title="Request closed", request_id=http_request_id)

    elif action == "snoozed":
        from datetime import datetime
        r.triage_action = "snoozed"
        if payload.snoozed_until:
            try:
                r.snoozed_until = datetime.fromisoformat(payload.snoozed_until)
            except ValueError:
                raise HTTPException(422, "Invalid snoozed_until") from None
        write_audit_log(db, action="intake.snoozed", resource_type="intake_request",
                        resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                        after={"snoozed_until": payload.snoozed_until})

    elif action == "escalate":
        r.priority = "Critical"
        _transition(db, request=r, actor=actor, to_status="escalated",
                    audit_action="intake.auto_escalated", after={"priority": "Critical"},
                    timeline_title="Escalated", request_id=http_request_id)

    db.commit()
    db.refresh(r)
    return serialize_request(db, r)


# --- approval ladder -------------------------------------------------------

def _rung_label(db: Session, *, user_id: str | None, group_id: str | None, role: str | None) -> str:
    from app.approvals.models import ApproverGroup

    if user_id:
        u = db.get(User, user_id)
        return (u.full_name or u.email) if u else user_id
    if group_id:
        g = db.get(ApproverGroup, group_id)
        return g.name if g else (role or "Approver")
    return role or "Approver"


def _serialize_chain(db: Session, rows: list) -> list[dict]:
    """One rung per ApprovalRequest, in order — the intake ladder strip."""
    from app.approvals.service import _quorum_needed

    return [{
        "approval_request_id": a.id,
        "step_order": a.step_order,
        "status": a.status,
        "mode": getattr(a, "mode", "any"),
        "approvals": (a.metadata_json or {}).get("approvals", 0),
        "needed": _quorum_needed(db, a),
        "approver_label": _rung_label(db, user_id=a.approver_user_id,
                                      group_id=a.approver_group_id, role=a.approver_role),
        "due_at": a.due_at.isoformat() if a.due_at else None,
    } for a in rows]


async def start_approval_ladder(db: Session, *, actor: User, request_id: str,
                                approver_user_id: str | None = None,
                                approver_role: str | None = None,
                                http_request_id: str | None = None) -> dict:
    """Submit an intake request into the shared approval ladder. The active
    value/type/risk routing rules decide the rungs; falls back to the passed
    approver when no rule matches."""
    r = get_request(db, user=actor, request_id=request_id)
    _require_staff(actor)
    if r.status == "closed":
        raise HTTPException(409, "Request is closed — file a follow-up")
    from app.intake.approval_bridge import submit_request_for_approval

    requests = await submit_request_for_approval(
        db, actor=actor, request=r, approver_user_id=approver_user_id,
        approver_role=approver_role, request_id=http_request_id,
    )
    db.commit()
    db.refresh(r)
    return {"request": serialize_request(db, r), "chain": _serialize_chain(db, requests)}


def override_gate(db: Session, *, actor: User, request_id: str, gate_key: str, action: str,
                  reason: str | None = None, http_request_id: str | None = None) -> dict:
    """Manually add or remove a Tier-0 gate. The override wins over the classifier
    and is audited; adding a gate applies its escalation side-effects immediately."""
    from app.intake import gates as gates_mod

    if action not in ("add", "remove"):
        raise HTTPException(422, "action must be 'add' or 'remove'")
    if gate_key not in gates_mod.GATE_BY_KEY:
        raise HTTPException(422, f"Unknown gate '{gate_key}'")
    r = get_request(db, user=actor, request_id=request_id)
    _require_staff(actor)
    if r.status == "closed":
        raise HTTPException(409, "Request is closed — file a follow-up")

    at = dict(r.ai_triage or {})
    overrides = list(at.get("gate_overrides", []))
    overrides.append({
        "action": action, "gate_key": gate_key,
        "by_user_id": actor.id, "by_name": (actor.full_name or actor.email),
        "reason": (reason or "").strip() or None, "at": utcnow().isoformat(),
    })
    at["gate_overrides"] = overrides
    r.ai_triage = at  # reassign so SQLAlchemy tracks the JSON mutation
    if action == "add":
        gates_mod.apply_gate_side_effects(r)
    write_audit_log(db, action="intake.gate_overridden", resource_type="intake_request",
                    resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                    request_id=http_request_id,
                    after={"gate": gate_key, "override": action, "reason": reason})
    r.updated_by_user_id = actor.id
    db.commit()
    db.refresh(r)
    return serialize_request(db, r)


def get_approval_chain(db: Session, *, actor: User, request_id: str) -> list[dict]:
    """The request's approval rungs. If a chain is live, its real rows (RAG). If
    not yet submitted, the PLANNED rungs (status 'planned') so the ladder is
    always visible — value/type routing rules + Tier-0 gates decide them."""
    from app.approvals.models import ApprovalRequest

    r = get_request(db, user=actor, request_id=request_id)
    rows = db.scalars(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.org_id == actor.org_id,
            ApprovalRequest.intake_request_id == r.id,
        )
        .order_by(ApprovalRequest.created_at, ApprovalRequest.step_order)
    ).all()
    if rows:
        return _serialize_chain(db, rows)
    if r.status in ("closed", "approved"):
        return []
    # Preview the planned ladder from the same planner submit uses.
    from app.approvals.service import plan_chain
    from app.intake.approval_bridge import build_intake_subject

    subject = build_intake_subject(db, r.id, org_id=actor.org_id)
    targets = plan_chain(db, subject=subject, org_id=actor.org_id)
    return [{
        "approval_request_id": None,
        "step_order": t["step_order"],
        "status": "planned",
        "approver_label": _rung_label(db, user_id=t.get("approver_user_id"),
                                      group_id=t.get("approver_group_id"), role=t.get("approver_role")),
        "due_at": None,
    } for t in targets]


# --- tasks -----------------------------------------------------------------

def serialize_task(db: Session, t: IntakeTask) -> dict:
    return {
        "id": t.id, "request_id": t.request_id, "title": t.title, "description": t.description,
        "assignee_user_id": t.assignee_user_id, "assignee_label": _user_label(db, t.assignee_user_id),
        "status": t.status, "sort_order": t.sort_order, "effort_minutes": t.effort_minutes,
    }


def list_tasks(db: Session, *, actor: User, request_id: str) -> list[dict]:
    r = get_request(db, user=actor, request_id=request_id)
    rows = db.scalars(
        select(IntakeTask).where(IntakeTask.request_id == r.id)
        .order_by(IntakeTask.sort_order, IntakeTask.created_at)
    ).all()
    return [serialize_task(db, t) for t in rows]


def create_task(db: Session, *, actor: User, request_id: str, payload) -> dict:
    r = get_request(db, user=actor, request_id=request_id)
    t = IntakeTask(
        org_id=actor.org_id, request_id=r.id, title=payload.title.strip(),
        description=payload.description, assignee_user_id=payload.assignee_user_id,
        sort_order=payload.sort_order, created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    db.add(t)
    db.flush()
    write_audit_log(db, action="intake.task.created", resource_type="intake_request",
                    resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                    after={"task": t.title})
    db.commit()
    db.refresh(t)
    return serialize_task(db, t)


def _get_task(db: Session, actor: User, task_id: str) -> IntakeTask:
    t = db.get(IntakeTask, task_id)
    if t is None or t.org_id != actor.org_id:
        raise HTTPException(404, "Task not found")
    get_request(db, user=actor, request_id=t.request_id)  # access gate
    return t


def update_task(db: Session, *, actor: User, task_id: str, payload) -> dict:
    t = _get_task(db, actor, task_id)
    for attr in ("title", "description", "assignee_user_id", "status", "sort_order"):
        val = getattr(payload, attr, None)
        if val is not None:
            setattr(t, attr, val)
    t.updated_by_user_id = actor.id
    db.commit()
    db.refresh(t)
    return serialize_task(db, t)


def delete_task(db: Session, *, actor: User, task_id: str) -> None:
    t = _get_task(db, actor, task_id)
    db.delete(t)
    db.commit()


def log_effort(db: Session, *, actor: User, task_id: str, minutes: int) -> dict:
    if minutes <= 0:
        raise HTTPException(422, "minutes must be positive")
    t = _get_task(db, actor, task_id)
    t.effort_minutes = (t.effort_minutes or 0) + minutes
    t.updated_by_user_id = actor.id
    write_audit_log(db, action="intake.task.effort_logged", resource_type="intake_request",
                    resource_id=t.request_id, org_id=actor.org_id, actor_user_id=actor.id,
                    after={"task_id": t.id, "minutes": minutes, "total": t.effort_minutes})
    db.commit()
    db.refresh(t)
    return serialize_task(db, t)


# --- my-work + assignees ---------------------------------------------------

def _sla_order_key(r: IntakeRequest, now):
    # overdue first, then most-elapsed
    return -sla_pct(r, now)


def my_work(db: Session, *, user: User) -> dict:
    now = utcnow()
    # My tickets — assigned to me + open
    tickets = db.scalars(
        select(IntakeRequest).where(
            IntakeRequest.org_id == user.org_id,
            IntakeRequest.assigned_to_user_id == user.id,
            IntakeRequest.status.in_(OPEN_STATUSES),
        )
    ).all()
    tickets = sorted(tickets, key=lambda r: _sla_order_key(r, now))
    # My tasks — open tasks assigned to me
    tasks = db.scalars(
        select(IntakeTask).where(
            IntakeTask.org_id == user.org_id,
            IntakeTask.assignee_user_id == user.id,
            IntakeTask.status != "done",
        ).order_by(IntakeTask.sort_order)
    ).all()
    return {
        "awaiting_review": [],  # triage removed — no AI recommendations to review
        "my_tickets": [serialize_request(db, r) for r in tickets],
        "my_tasks": [serialize_task(db, t) for t in tasks],
    }


# --- promote ---------------------------------------------------------------

def promote(db: Session, *, actor: User, request_id: str, payload,
            http_request_id: str | None = None) -> dict:
    r = get_request(db, user=actor, request_id=request_id)
    _require_staff(actor)
    if payload.target == "contract":
        from app.contracts.models import Contract
        c = db.get(Contract, payload.target_id)
        if c is None or c.org_id != actor.org_id:
            raise HTTPException(404, "Contract not found")
        r.contract_id = c.id
    else:
        from app.matters.models import Matter
        p = db.get(Matter, payload.target_id)
        if p is None or p.org_id != actor.org_id:
            raise HTTPException(404, "Matter not found")
        r.matter_id = p.id
    r.updated_by_user_id = actor.id
    write_audit_log(db, action="intake.promoted", resource_type="intake_request", resource_id=r.id,
                    org_id=actor.org_id, actor_user_id=actor.id, request_id=http_request_id,
                    after={"target": payload.target, "id": payload.target_id})
    write_timeline_event(db, org_id=actor.org_id, resource_type="intake_request", resource_id=r.id,
                         event_type="intake.promoted", title=f"Promoted to {payload.target}",
                         actor_user_id=actor.id)
    db.commit()
    db.refresh(r)
    return serialize_request(db, r)


# --- SLA engine (legs / pause / sweep / ops) -------------------------------

def _ms(dt) -> float:
    return dt.timestamp() * 1000.0


def build_sla_legs(db: Session, *, request: IntakeRequest, now=None) -> dict:
    now = now or utcnow()
    now_ms = _ms(now)
    submitted = _ms(request.submitted_at or request.created_at or now)
    closed_ts = _ms(request.closed_at) if (request.status in TERMINAL_STATUSES and request.closed_at) else None
    end = max(closed_ts or now_ms, submitted)
    cal = calendar_from_settings()
    sla_ms = max(0, request.sla_hours or 0) * 3_600_000.0
    pause = float(request.paused_ms_total or 0)
    if request.paused_at and not closed_ts:
        pause += business_ms_between(request.paused_at, now, cal)
    # The breach is a point on the WALL clock, so the working-time budget has
    # to be projected onto it — `submitted + sla_hours` is only that point for
    # a team that works round the clock.
    breach_ts = _ms(business_deadline(
        request.submitted_at or request.created_at or now, sla_ms + pause, cal
    ))
    handoffs = db.scalars(
        select(IntakeHandoff).where(IntakeHandoff.request_id == request.id)
        .order_by(IntakeHandoff.created_at)
    ).all()

    def label_for(holder, uid):
        if holder == "agent":
            return "AI agent"
        if holder == "human":
            return _user_label(db, uid) or "Reviewer"
        return "Intake queue"

    passes = [(max(submitted, min(end, _ms(h.created_at))), h.to_holder, h.to_user_id) for h in handoffs]
    cursor = {"holder": "queue", "uid": None, "start": submitted, "label": "Intake queue"}
    legs = []

    def seg(cur, end_ts):
        elapsed = max(0.0, end_ts - cur["start"])
        return {
            "holder": cur["holder"], "holder_user_id": cur["uid"], "holder_label": cur["label"],
            "start_ts": cur["start"], "end_ts": end_ts, "elapsed_ms": elapsed,
            "pct_of_sla": round(elapsed / sla_ms * 100) if sla_ms else 0,
            "breached_during_leg": bool(sla_ms > 0 and cur["start"] <= breach_ts < end_ts),
        }

    for at, holder, uid in passes:
        if at > cursor["start"]:
            legs.append(seg(cursor, at))
        cursor = {"holder": holder, "uid": uid if holder == "human" else None,
                  "start": at, "label": label_for(holder, uid)}
    last = seg(cursor, end)
    last["active"] = closed_ts is None
    legs.append(last)

    return {
        "legs": legs, "sla_ms": sla_ms, "breach_ts": breach_ts,
        "total_elapsed_ms": end - submitted,
        "breached": bool(sla_ms > 0 and end >= breach_ts),
        "closed": bool(closed_ts), "paused": bool(request.paused_at),
    }


def set_pause(db: Session, *, actor: User, request_id: str, paused: bool,
              http_request_id: str | None = None) -> dict:
    r = get_request(db, user=actor, request_id=request_id)
    if r.status == "closed":
        raise HTTPException(409, "Request is closed — file a follow-up")
    now = utcnow()
    if paused:
        if r.paused_at is None:  # idempotent (Part 0.16)
            r.paused_at = now
            write_audit_log(db, action="intake.paused", resource_type="intake_request",
                            resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id)
    else:
        if r.paused_at is not None:
            # Business ms, because this is subtracted from a business-ms
            # elapsed. Counting a pause over a weekend in wall-clock time
            # would credit hours the clock never charged.
            r.paused_ms_total = int(
                (r.paused_ms_total or 0) + business_ms_between(r.paused_at, now)
            )
            r.paused_at = None
            write_audit_log(db, action="intake.resumed", resource_type="intake_request",
                            resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id)
    r.updated_by_user_id = actor.id
    db.commit()
    db.refresh(r)
    return serialize_request(db, r)


def sla_ops_summary(db: Session, *, org_id: str) -> dict:
    now = utcnow()
    rows = db.scalars(
        select(IntakeRequest).where(IntakeRequest.org_id == org_id,
                                    IntakeRequest.status.in_(OPEN_STATUSES))
    ).all()
    on_track = at_risk = overdue = paused = 0
    awaiting = escalated = 0
    workload: dict[str, int] = {}
    workload_over: dict[str, int] = {}
    oldest = None
    pct_sum = 0
    for r in rows:
        p = posture(r, now)
        if p == "overdue":
            overdue += 1
        elif p == "at_risk":
            at_risk += 1
        else:
            on_track += 1
        if r.paused_at:
            paused += 1
        if r.status == "open":
            awaiting += 1
        if r.status == "escalated":
            escalated += 1
        pct_sum += sla_pct(r, now)
        if r.assigned_to_user_id:
            workload[r.assigned_to_user_id] = workload.get(r.assigned_to_user_id, 0) + 1
            if p == "overdue":
                workload_over[r.assigned_to_user_id] = workload_over.get(r.assigned_to_user_id, 0) + 1
        if oldest is None or (r.submitted_at and r.submitted_at < oldest):
            oldest = r.submitted_at
    # breaches in the last 7d from the audit log
    from datetime import timedelta

    from app.core.models import AuditLog
    since = now - timedelta(days=7)
    breaches_7d = db.scalar(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.action == "intake.sla_breached",
               AuditLog.created_at >= since)
    ) or 0
    # top 5 rules by firings
    rules = db.scalars(
        select(IntakeRoutingRule).where(IntakeRoutingRule.org_id == org_id)
        .order_by(IntakeRoutingRule.times_fired.desc()).limit(5)
    ).all()
    return {
        "generated_at": now.isoformat(),
        "open_total": len(rows), "open": awaiting, "escalated": escalated,
        "on_track": on_track, "at_risk": at_risk, "overdue": overdue, "paused": paused,
        "avg_elapsed_pct": int(pct_sum / len(rows)) if rows else 0,
        "breaches_7d": int(breaches_7d),
        "by_holder": {"agent": sum(1 for r in rows if r.handoff_holder == "agent"),
                      "human": sum(1 for r in rows if r.handoff_holder == "human"),
                      "queue": sum(1 for r in rows if r.handoff_holder == "queue")},
        "oldest_open": oldest.isoformat() if oldest else None,
        "workload": [{"user_id": uid, "name": _user_label(db, uid), "open": n,
                      "overdue": workload_over.get(uid, 0)} for uid, n in sorted(workload.items(), key=lambda x: -x[1])],
        "rule_effectiveness": [{"id": r.id, "name": r.name, "times_fired": r.times_fired,
                                "last_fired_at": r.last_fired_at.isoformat() if r.last_fired_at else None}
                               for r in rules],
    }


def run_sla_sweep(db: Session, *, org_id: str | None = None) -> dict:
    """Recompute posture both directions; escalate on the upward edge only
    (Part 0.11/0.16). Idempotent. Returns a counter dict."""
    now = utcnow()
    q = select(IntakeRequest).where(IntakeRequest.status.in_(OPEN_STATUSES))
    if org_id:
        q = q.where(IntakeRequest.org_id == org_id)
    escalated = breached = downgraded = 0
    for r in db.scalars(q).all():
        new = posture(r, now)
        old = r.sla_status
        if new != old:
            r.sla_status = new
            if new == "overdue" and old != "overdue":
                breached += 1
                write_audit_log(db, action="intake.sla_breached", resource_type="intake_request",
                                resource_id=r.id, org_id=r.org_id, actor_user_id=None,
                                after={"sla_hours": r.sla_hours, "elapsed_pct": sla_pct(r, now)})
                if r.status != "escalated":
                    r.status = "escalated"
                    _stamp_stage(r, r.stage)
                    escalated += 1
                    write_audit_log(db, action="intake.auto_escalated", resource_type="intake_request",
                                    resource_id=r.id, org_id=r.org_id, actor_user_id=None)
                    write_timeline_event(db, org_id=r.org_id, resource_type="intake_request",
                                         resource_id=r.id, event_type="intake.auto_escalated",
                                         title="Escalated — SLA breached")
                _notify(db, r, r.assigned_to_user_id, "intake.sla_breached",
                        f"{r.ref} breached its SLA",
                        f"{r.type_label} is overdue ({r.sla_hours}h window) and was escalated.")
            elif new != "overdue":
                downgraded += 1
    db.commit()
    return {"escalated": escalated, "breached": breached, "downgraded": downgraded}


# --- copilot filing --------------------------------------------------------

def file_from_copilot(db: Session, *, actor: User, payload, request_id: str | None = None) -> dict:
    from app.intake.schemas import RequestCreate

    convo = [{"role": m.role, "content": m.content} for m in payload.messages]
    rc = RequestCreate(type_label=payload.type_label, description=payload.description,
                       field_values=payload.field_values, source="copilot")
    return create_request(db, actor=actor, payload=rc, request_id=request_id, conversation=convo)


# --- knowledge base --------------------------------------------------------

def serialize_kb(a: IntakeKbArticle) -> dict:
    return {"id": a.id, "source_ref": a.source_ref, "title": a.title, "body": a.body,
            "tags": a.tags or [], "active": a.active}


def list_kb(db: Session, *, org_id: str, include_inactive: bool = False) -> list[dict]:
    q = select(IntakeKbArticle).where(IntakeKbArticle.org_id == org_id)
    if not include_inactive:
        q = q.where(IntakeKbArticle.active.is_(True))
    rows = db.scalars(q.order_by(IntakeKbArticle.title)).all()
    return [serialize_kb(a) for a in rows]


def create_kb(db: Session, *, actor: User, payload) -> dict:
    ref = (payload.source_ref or "").strip()
    if not ref:
        raise HTTPException(422, "source_ref is required")
    if db.scalar(select(IntakeKbArticle.id).where(
            IntakeKbArticle.org_id == actor.org_id, IntakeKbArticle.source_ref == ref)):
        raise HTTPException(409, f'An article "{ref}" already exists')
    a = IntakeKbArticle(org_id=actor.org_id, source_ref=ref, title=payload.title.strip(),
                        body=payload.body, tags=payload.tags or None, active=True,
                        created_by_user_id=actor.id, updated_by_user_id=actor.id)
    db.add(a)
    db.flush()
    write_audit_log(db, action="intake.kb.created", resource_type="intake_kb_article",
                    resource_id=a.id, org_id=actor.org_id, actor_user_id=actor.id, after={"ref": ref})
    db.commit()
    db.refresh(a)
    return serialize_kb(a)


def update_kb(db: Session, *, actor: User, kb_id: str, payload) -> dict:
    a = db.get(IntakeKbArticle, kb_id)
    if a is None or a.org_id != actor.org_id:
        raise HTTPException(404, "Article not found")
    for attr in ("title", "body", "tags", "active"):
        val = getattr(payload, attr, None)
        if val is not None:
            setattr(a, attr, val)
    a.updated_by_user_id = actor.id
    db.commit()
    db.refresh(a)
    return serialize_kb(a)


def delete_kb(db: Session, *, actor: User, kb_id: str) -> None:
    a = db.get(IntakeKbArticle, kb_id)
    if a is None or a.org_id != actor.org_id:
        raise HTTPException(404, "Article not found")
    db.delete(a)
    db.commit()


# --- pool ops analytics ----------------------------------------------------

def pool_ops_summary(db: Session, *, org_id: str, days: int = 30) -> dict:
    from datetime import timedelta

    now = utcnow()
    since = now - timedelta(days=days)
    since7 = now - timedelta(days=7)
    teams = db.scalars(
        select(IntakeTeam).where(IntakeTeam.org_id == org_id)
        .order_by(IntakeTeam.sort_order, IntakeTeam.name)
    ).all()
    reqs = db.scalars(select(IntakeRequest).where(IntakeRequest.org_id == org_id)).all()
    by_assignee: dict[str, list[IntakeRequest]] = {}
    for r in reqs:
        if r.assigned_to_user_id:
            by_assignee.setdefault(r.assigned_to_user_id, []).append(r)
    # effort per user (via tasks)
    effort_rows = db.execute(
        select(IntakeTask.assignee_user_id, func.sum(IntakeTask.effort_minutes))
        .where(IntakeTask.org_id == org_id).group_by(IntakeTask.assignee_user_id)
    ).all()
    effort_by_user = {uid: int(m or 0) for uid, m in effort_rows}

    def member_stats(uid: str):
        rs = by_assignee.get(uid, [])
        open_rs = [r for r in rs if r.status in OPEN_STATUSES]
        return {
            "open": len(open_rs),
            "overdue": sum(1 for r in open_rs if posture(r, now) == "overdue"),
            "at_risk": sum(1 for r in open_rs if posture(r, now) == "at_risk"),
            "closed_7d": sum(1 for r in rs if r.closed_at and r.closed_at >= since7),
            "closed_30d": sum(1 for r in rs if r.closed_at and r.closed_at >= since),
            "effort": effort_by_user.get(uid, 0),
        }

    tiers = []
    tot_open = tot_over = tot_closed30 = tot_effort = tot_overflow = 0
    mix = {"simple": 0, "standard": 0, "complex": 0}
    for t in teams:
        members = []
        for m in t.members:
            s = member_stats(m.user_id)
            util = int(s["open"] / m.capacity * 100) if m.capacity > 0 else None
            members.append({"user_id": m.user_id, "name": _user_label(db, m.user_id),
                            "capacity": m.capacity, "utilization": util, **s})
            tot_open += s["open"]; tot_over += s["overdue"]; tot_closed30 += s["closed_30d"]
            tot_effort += s["effort"]
            for r in by_assignee.get(m.user_id, []):
                if r.status in OPEN_STATUSES:
                    cx = (r.ai_triage or {}).get("complexity", "standard")
                    if cx in mix:
                        mix[cx] += 1
                if (r.fired_rules or {}) and any("overflow" in str(a).lower()
                        for s2 in (r.fired_rules or {}).get("summaries", []) for a in s2.get("actions", [])):
                    tot_overflow += 1
        tiers.append({
            "id": t.id, "name": t.name, "strategy": t.strategy,
            "overflow_team_name": (db.get(IntakeTeam, t.overflow_team_id).name
                                   if t.overflow_team_id else None),
            "members": members,
            "open": sum(x["open"] for x in members),
            "overdue": sum(x["overdue"] for x in members),
            "closed_30d": sum(x["closed_30d"] for x in members),
            "effort": sum(x["effort"] for x in members),
        })
    return {
        "generated_at": now.isoformat(), "days": days, "tiers": tiers,
        "totals": {"open": tot_open, "overdue": tot_over, "closed_30d": tot_closed30,
                   "effort_minutes": tot_effort, "overflow_events": tot_overflow},
        "complexity_mix": mix,
    }


def list_assignees(db: Session, *, org_id: str) -> list[dict]:
    from app.core.enums import UserStatus

    rows = db.scalars(
        select(User).where(User.org_id == org_id, User.status == UserStatus.ACTIVE)
        .order_by(User.full_name)
    ).all()
    return [{"id": u.id, "name": u.full_name or u.email, "email": u.email} for u in rows]


_STAGE_STATUS = {"new": "open", "complete": "closed"}


def update_request(db: Session, *, actor: User, request_id: str, payload,
                   http_request_id: str | None = None) -> dict:
    r = get_request(db, user=actor, request_id=request_id)
    if r.status == "closed":
        raise HTTPException(409, "Request is closed — file a follow-up")
    before = {"stage": r.stage, "priority": r.priority, "work_status": r.work_status}
    for attr in ("priority", "department", "description", "work_status"):
        val = getattr(payload, attr, None)
        if val is not None:
            setattr(r, attr, val)
    if payload.field_values is not None:
        # Validated here too: the update path wrote straight through, so a
        # required field could be emptied and a typed one replaced with
        # anything after the request was filed.
        rtype = db.get(IntakeRequestType, r.request_type_id) if r.request_type_id else None
        r.field_values = _validate_field_values(rtype, payload.field_values)
    if payload.stage is not None and payload.stage != r.stage:
        rtype = db.get(IntakeRequestType, r.request_type_id) if r.request_type_id else None
        valid = stages_for(rtype)
        if payload.stage not in valid:
            raise HTTPException(422, f"Unknown stage '{payload.stage}'")
        # Only the head/tail stages carry a canonical status (new→open,
        # complete→closed). Mid-stage moves must NOT change status — a default of
        # "open" here silently de-escalated an `escalated` request on any advance.
        mapped = _STAGE_STATUS.get(payload.stage)  # None ⇒ _transition preserves status
        _transition(
            db, request=r, actor=actor, to_status=mapped, to_stage=payload.stage,
            audit_action="intake.stage_advanced", before=before,
            after={"stage": payload.stage, "status": mapped or r.status},
            timeline_title=f"Stage → {STAGE_LABELS.get(payload.stage, payload.stage)}",
            request_id=http_request_id,
        )
    else:
        r.updated_by_user_id = actor.id
        write_audit_log(db, action="intake.updated", resource_type="intake_request",
                        resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                        before=before, after={"priority": r.priority, "work_status": r.work_status})
    db.commit()
    db.refresh(r)
    return serialize_request(db, r)


# ---- gap-fill: agent observability + documents (reference parity) ------------

# Per file, and per filing. Files travel base64-encoded inside the JSON filing
# (a third bigger on the wire), and nginx accepts request bodies up to 60 MB
# (deploy/nginx/aegis.conf), so 40 MB of files per filing stays under it.
_DOC_MAX_BYTES = 25 * 1024 * 1024
_FILING_MAX_BYTES = 40 * 1024 * 1024
_DOC_MAX_LABEL = "25 MB"


_SUPPORTED_ATTACHMENTS = "PDF, Word (DOC or DOCX), plain text, PNG or JPEG"


@dataclass
class _PreparedAttachment:
    filename: str
    mime_type: str
    size_bytes: int
    extracted_text: str | None
    extraction_quality: float | None


def _prepare_attachment(filename: str, mime_type: str, content: bytes) -> _PreparedAttachment:
    """Check one attachment and read its text, or raise an error that names the file.

    Runs before anything is saved, so a bad file stops the filing instead of
    leaving a request behind with its attachment missing."""
    from app.contract_files.service import _scan_for_malware, validate_upload_mime
    from app.contract_files.text_extraction import extract_text

    name = (filename or "attachment")[:300]
    if not content:
        raise HTTPException(422, f'"{name}" is empty')
    if len(content) > _DOC_MAX_BYTES:
        raise HTTPException(413, f'"{name}" is larger than the {_DOC_MAX_LABEL} limit for request attachments')
    # Enforce the shared MIME allowlist + magic-byte check on every attachment —
    # the caller's declared content-type (form upload or email) is untrusted.
    try:
        mime_type = validate_upload_mime(content, mime_type)
    except HTTPException as exc:
        raise HTTPException(
            exc.status_code, f'"{name}" is not a supported file type — use {_SUPPORTED_ATTACHMENTS}'
        ) from exc
    _scan_for_malware(content)  # no-op unless ClamAV is switched on
    extracted, quality = "", None
    try:
        result = extract_text(content, mime_type=mime_type, filename=name)
        # Postgres TEXT/VARCHAR columns can never store a NUL byte — some PDF
        # extractors emit them for certain font encodings, which would
        # otherwise fail this insert.
        extracted = (result.text or "").replace("\x00", "")[:20000]
        quality = result.quality_score
    except Exception:
        extracted = ""
    return _PreparedAttachment(name, mime_type[:120], len(content), extracted or None, quality)


def _attach(db: Session, *, actor: User, r: IntakeRequest, att: _PreparedAttachment):
    from app.intake.models import IntakeDocument

    doc = IntakeDocument(org_id=actor.org_id, request_id=r.id, filename=att.filename,
                         mime_type=att.mime_type, size_bytes=att.size_bytes,
                         extracted_text=att.extracted_text, extraction_quality=att.extraction_quality,
                         created_by_user_id=actor.id, updated_by_user_id=actor.id)
    db.add(doc)
    # The extracted text stays on the document (surfaced in the Attachments panel).
    # We deliberately do NOT fold it into r.description — that permanently rewrote
    # the requester's short ask with a wall of document text.
    write_audit_log(db, action="intake.document.added", resource_type="intake_request",
                    resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                    after={"filename": att.filename, "bytes": att.size_bytes,
                           "extracted_chars": len(att.extracted_text or "")})
    return doc


def add_document(db: Session, *, actor: User, request_id: str, filename: str,
                 mime_type: str, content: bytes) -> dict:
    """Attach a document to a request that is already filed."""
    att = _prepare_attachment(filename, mime_type, content)
    r = get_request(db, user=actor, request_id=request_id)
    doc = _attach(db, actor=actor, r=r, att=att)
    db.commit()
    db.refresh(doc)
    return serialize_document(doc)


def list_documents(db: Session, *, actor: User, request_id: str) -> list[dict]:
    from app.intake.models import IntakeDocument
    r = get_request(db, user=actor, request_id=request_id)
    docs = db.scalars(select(IntakeDocument).where(IntakeDocument.request_id == r.id)
                      .order_by(IntakeDocument.created_at)).all()
    return [serialize_document(d) for d in docs]


def serialize_document(d) -> dict:
    return {"id": d.id, "filename": d.filename, "mime_type": d.mime_type,
            "size_bytes": d.size_bytes,
            "extracted_chars": len(d.extracted_text or ""),
            "extracted_text": (d.extracted_text or "")[:8000] or None,
            "extraction_quality": d.extraction_quality,
            "created_at": d.created_at.isoformat() if d.created_at else None}
