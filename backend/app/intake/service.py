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
from sqlalchemy import text as sql_text
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
    IntakeRequest,
    IntakeTask,
    IntakeTeam,
    IntakeTeamMember,
)

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# --- small helpers ---------------------------------------------------------

logger = logging.getLogger(__name__)


def stages_for() -> list[str]:
    return SPINE_HEAD + list(SPINE_DEFAULT_MID) + SPINE_TAIL


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

_LIFECYCLE = ("intake", "drafting", "review", "approval", "signature", "active", "closed")


def _workflow(r: IntakeRequest, run) -> list[dict]:
    """Where the request really is: the lifecycle stages its workflow run passes
    through, with the current step's stage active. It used to be a request-status
    spine (new → assigned → review → complete) that nothing advanced once the
    workflow engine ran, so every request showed "New · 0/4" for its whole life.
    No run yet → no stages (the list shows the recommended workflow instead)."""
    if run is None:
        return []
    steps = list(run.steps or [])
    used = {s.get("stage") for s in steps if s.get("stage")}
    stages = [st for st in _LIFECYCLE if st == "intake" or st in used]
    finished = run.status == "complete"
    current = steps[min(run.current_index, len(steps) - 1)] if steps and not finished else None
    cur_stage = (current or {}).get("stage") or "intake"
    idx = len(stages) if finished else stages.index(cur_stage) if cur_stage in stages else 0
    out = []
    for i, st in enumerate(stages):
        label = st.title()
        if i == idx and current:
            label = f"{label} · {current.get('name')}"
        out.append({"label": label, "stage": st, "done": i < idx, "active": i == idx and not finished})
    return out


# --- transition choke-point ------------------------------------------------

def _stamp_stage(r: IntakeRequest, stage: str) -> None:
    hist = list(r.stage_timestamps or [])
    hist.append({"stage": stage, "at": utcnow().isoformat()})
    r.stage_timestamps = hist


# --- field validation ------------------------------------------------------

_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}


def _coerce_field(f, raw):
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

    if kind == "multiselect":
        allowed = [str(o.get("value")) for o in (f.options or []) if isinstance(o, dict)]
        picked = raw if isinstance(raw, list) else [x.strip() for x in str(raw).split(",") if x.strip()]
        bad = [p for p in picked if allowed and str(p) not in allowed]
        if bad:
            raise HTTPException(422, f'Field "{f.label}" must be from: {", ".join(allowed)}')
        return [str(p) for p in picked]

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


def _validate_field_values(fields: list, values: dict | None) -> dict | None:
    """Enforce what the request's form declares, and return the cleaned values.

    Previously this checked required-ness and nothing else, so a `number` field
    accepted "banana" and a `select` accepted any string at all — the form
    builder promised typed fields and stored untyped JSON. Anything reading
    those values back (arithmetic, date comparison, filtering by option) was
    working on whatever the caller happened to send.

    Keys the form does not declare are passed through untouched: channel intake
    legitimately stores `channel_from`, `counterparty` and `gmail_thread_id`
    alongside the declared fields, and rejecting them would break email and
    Gmail ingestion.
    """
    from app.intake.agreement_forms import shown

    if not fields:
        return values
    cleaned = dict(values or {})
    for f in fields:
        if not shown(f, cleaned):
            # A question that doesn't apply (an NDA has no value) is neither
            # required nor kept: a stale answer from an earlier choice would
            # otherwise reach the contract.
            cleaned.pop(f.key, None)
            continue
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

def _draftable(r: IntakeRequest) -> dict:
    from app.drafting_templates.service import TEMPLATES
    from app.intake.drafting import resolve_doc_type

    code = resolve_doc_type(r)
    label = (TEMPLATES.get(code) or {}).get("label") if code else None
    return {"draftable_doc_type": code, "draftable_doc_label": label}


def _derive_subject(subject: str | None, description: str | None, type_label: str) -> str:
    """A short human title for a request: the explicit subject if given, else the
    first non-empty line of the description, else the type label. Capped at 200."""
    if subject and subject.strip():
        return subject.strip()[:200]
    for line in (description or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return (type_label or "").strip()[:200]


# (id key, name key, record kind, label) for the wizard's party lookups.
_PARTY_REFS = (
    ("entity_id", "entity", "legal_entity", "legal entity"),
    ("entity_2_id", "entity_2", "legal_entity", "second legal entity"),
    ("counterparty_id", "counterparty", "counterparty", "counterparty"),
    ("counterparty_2_id", "counterparty_2", "counterparty", "second counterparty"),
)


# (name key, id key, role). Novation's outgoing and remaining parties may be
# us or them depending on who transfers, so they are recorded as related.
_FORM_PARTIES = (
    ("counterparty", "counterparty_id", "counterparty"),
    ("counterparty_2", "counterparty_2_id", "counterparty"),
    ("incoming_party", None, "counterparty"),
    ("outgoing_party", None, "related"),
    ("remaining_party", None, "related"),
)


def _initial_parties(field_values: dict | None) -> list[dict]:
    """Every party a filing names, deduped by name (the first counterparty is
    the primary)."""
    from app.intake.screening import normalize_party_name

    fv = field_values or {}
    out, seen = [], set()
    for name_key, id_key, role in _FORM_PARTIES:
        name = str(fv.get(name_key) or "").strip()
        key = normalize_party_name(name) if name else ""
        if not key or key in seen:
            continue
        seen.add(key)
        party = {"name": name[:255], "role": role, "is_person": False}
        if id_key and fv.get(id_key):
            party["counterparty_id"] = fv[id_key]
        out.append(party)
    return out


def _decode_b64(content_b64: str, filename: str) -> bytes:
    import base64
    import binascii

    try:
        return base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, f'"{filename}" could not be read — please attach it again') from exc


_PARTY_ROLES = {"counterparty", "adverse", "related", "our_side"}


def _accessible(user: User):
    """Staff (intake:read) see the whole org queue; everyone else only their own."""
    from app.core.rbac import has_permission

    base = IntakeRequest.org_id == user.org_id
    if is_org_admin(user) or has_permission(user.permission_values, "intake:read"):
        return base
    return base & (IntakeRequest.requester_user_id == user.id)


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


# --- approval ladder -------------------------------------------------------

# --- tasks -----------------------------------------------------------------

# --- team pool + self-assign ------------------------------------------------

def _in_pool_scope(r: IntakeRequest, scope: tuple[set[str], set[str]] | None) -> bool:
    """Shared by list_pool_requests (what shows) and claim_request (what you're
    allowed to self-assign) so the two can never disagree."""
    if scope is None:
        return True
    expertise, depts = scope
    if r.department is None:
        return True
    if (r.department or "").strip().lower() in depts:
        return True
    category = ((r.ai_triage or {}).get("category") or "").strip().lower()
    return bool(category) and category in expertise


# --- my-work + assignees ---------------------------------------------------

def _sla_order_key(r: IntakeRequest, now):
    # overdue first, then most-elapsed
    return -sla_pct(r, now)


# --- promote ---------------------------------------------------------------

# --- SLA engine (legs / pause / sweep / ops) -------------------------------

def _ms(dt) -> float:
    return dt.timestamp() * 1000.0


_STAGE_STATUS = {"new": "open", "complete": "closed"}


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


def serialize_document(d) -> dict:
    return {"id": d.id, "filename": d.filename, "mime_type": d.mime_type,
            "size_bytes": d.size_bytes,
            "extracted_chars": len(d.extracted_text or ""),
            "extracted_text": (d.extracted_text or "")[:8000] or None,
            "extraction_quality": d.extraction_quality,
            "created_at": d.created_at.isoformat() if d.created_at else None}


class IntakeService:
    """Legal Intake — capture, triage, route, SLA, audit.

    Part of the DI migration (see backend/DI_MIGRATION.md). Constructed with
    a ``db`` session; every function that took ``db`` first is now a method
    reading ``self.db``. Pure helpers (stage/SLA math, serializers that don't
    touch ``db``, validation on already-loaded objects) stay module-level above.
    """

    def __init__(self, db: Session):
        self.db = db

    def _next_ref(self) -> str:
        db = self.db
        n = db.execute(select(func.nextval("intake_ref_seq"))).scalar_one()
        return f"REQ-{n}"

    def _user_label(self, uid: str | None) -> str | None:
        db = self.db
        if not uid:
            return None
        u = db.get(User, uid)
        return (u.full_name or u.email) if u else uid

    def _latest_run(self, r: IntakeRequest):
        """The request's most recent workflow run (prefetched for lists)."""
        db = self.db
        if hasattr(r, "_latest_run"):
            return r._latest_run
        from app.workflows.models import WorkflowRun

        return db.scalars(select(WorkflowRun).where(WorkflowRun.request_id == r.id)
                          .order_by(WorkflowRun.created_at.desc())).first()

    def _contract_title(self, contract_id: str | None) -> str | None:
        db = self.db
        if not contract_id:
            return None
        from app.contracts.models import Contract
        c = db.get(Contract, contract_id)
        return c.title if c else None

    def serialize_request(self, r: IntakeRequest) -> dict:
        from app.intake.screening import gather_parties
        return {
            "id": r.id,
            "ref": r.ref,
            "source": r.source,
            "requester_user_id": r.requester_user_id,
            "requester_name": r.requester_name or self._user_label(r.requester_user_id),
            "department": r.department,
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
            "assigned_to_label": self._user_label(r.assigned_to_user_id),
            "approval_gate_user_id": r.approval_gate_user_id,
            "sla_hours": r.sla_hours,
            "sla_status": posture(r),
            "sla_pct": sla_pct(r),
            "submitted_at": _iso(r.submitted_at),
            "closed_at": _iso(r.closed_at),
            "triaged_by_user_id": r.triaged_by_user_id,
            "triage_action": r.triage_action,
            "ai_triage": r.ai_triage,
            "screening": r.screening,
            # The EFFECTIVE parties (structured list, else the legacy
            # field_values.counterparty), the same set the relationship note uses.
            "parties": gather_parties(r),
            "handoff_holder": r.handoff_holder,
            "handoff_user_id": r.handoff_user_id,
            "contract_id": r.contract_id,
            "contract_title": self._contract_title(r.contract_id),
            "workflow": _workflow(r, self._latest_run(r)),
            "created_at": _iso(r.created_at),
            # Which document "Approve & draft" would produce — decided here, by the
            # same rule the draft endpoint uses, so the UI never guesses on its own.
            **_draftable(r),
        }

    def _transition(self, *, request: IntakeRequest, actor: User | None,
        to_status: str | None = None, to_stage: str | None = None,
        audit_action: str, before: dict | None = None, after: dict | None = None,
        timeline_title: str | None = None, actor_type: str = "user", request_id: str | None = None,
    ) -> None:
        """The single seam every intake state change routes through. Refuses edits to
        a closed request; stamps closed_at once on entering terminal; appends stage
        history; writes the immutable audit + timeline rows. Commit stays with the
        caller (one commit per API call)."""
        db = self.db
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

    def _compute_intake_analysis(self, request: IntakeRequest) -> None:
        """Populate ai_triage.flow_suggestion for every request; for litigation-
        category requests, run the Litigation Intake Agent instead — its richer
        assessment lands on ai_triage.litigation_assessment and its extracted facts
        pre-fill the litigation flow's branch answers. Mutates request; caller commits."""
        from app.intake.litigation_agent import assess_litigation, branch_fields, is_litigation

        db = self.db
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

            # Re-run the full context-aware triage; merge so earlier keys survive.
            at.update(triage_agent.triage(db, request))
            at.pop("litigation_assessment", None)
        from app.intake.flow_agent import used_for_suggestion

        set_up = used_for_suggestion(db, request)
        if set_up:  # the admin's "Used for" choice beats any agent's pick
            at["flow_suggestion"] = set_up
        request.ai_triage = at  # reassign so SQLAlchemy tracks the JSON mutation

    def _attach_flow_suggestion(self, request: IntakeRequest) -> None:
        """Suggest — never start — the workflow this request should ride (+ litigation
        assessment for disputes). Best-effort; runs post-commit so it owns its own
        transaction and never breaks intake."""
        db = self.db
        try:
            self._compute_intake_analysis(request)
            db.commit()
        except Exception:
            db.rollback()
            import logging
            logging.getLogger(__name__).warning(
                "flow suggestion on create failed for %s", request.id, exc_info=True
            )

    def resuggest_flow(self, *, actor: User, request_id: str) -> dict:
        """Re-run the router / litigation agent on demand (ticket 'Re-suggest')."""
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
        _require_staff(actor)
        self._compute_intake_analysis(r)
        db.commit()
        db.refresh(r)
        return self.serialize_request(r)

    def _pick_owner_team_candidates(self, *, org_id: str, category: str | None,
                                    department: str | None) -> list:
        """Ordered candidate teams that could own this request: every team whose
        expertise covers the matter category, with a team that also serves the
        request's department sorted first (department stays a soft, non-exclusive
        signal). Falls back to the default intake team (Admin → Teams) when nothing
        has expertise for the category.

        Returns every matching candidate, not just the best one — a tie on
        sort_order can otherwise pin the pick to a single-person, fully-loaded
        team while an equally-valid team sits idle (REQ-4037). The caller tries
        each in order until one actually has capacity."""
        from sqlalchemy import select as _select

        from app.intake.teams import default_intake_team

        db = self.db
        teams = db.scalars(
            _select(IntakeTeam)
            .where(IntakeTeam.org_id == org_id, IntakeTeam.active.is_(True))
            .order_by(IntakeTeam.sort_order, IntakeTeam.name)
        ).all()

        # 1. expertise: teams that own this matter category
        cands = [t for t in teams if category and category in (t.expertise or [])]
        if cands:
            # 2. department: teams that also serve the request's business unit sort
            #    first, but a category match is never dropped outright.
            dept = (department or "").strip().lower()
            if dept:
                cands = sorted(
                    cands,
                    key=lambda t: 0 if any((d or "").strip().lower() == dept
                                           for d in (t.departments or [])) else 1,
                )
            return cands

        # 3. fallback: the default intake team
        fallback = default_intake_team(db, org_id=org_id)
        return [fallback] if fallback else []

    def _pick_owner_team(self, *, org_id: str, category: str | None, department: str | None):
        """The best candidate owner team (see _pick_owner_team_candidates)."""
        cands = self._pick_owner_team_candidates(org_id=org_id, category=category, department=department)
        return cands[0] if cands else None

    def _assign_owner_from_triage(self, request: IntakeRequest) -> None:
        """Auto-assign the request owner from the triage read. Routes by matter-type
        EXPERTISE and business unit (with the default intake team as the fallback);
        pick_from_pool balances by load. Tries every candidate team in priority order
        before giving up, so one fully-loaded team doesn't block an equally-valid team
        with open capacity. Never overrides a human decision or an existing assignee;
        best-effort — no eligible team means the request just waits in the queue."""
        db = self.db
        if request.triaged_by_user_id or request.triage_action or request.assigned_to_user_id:
            return
        from app.intake import teams as teams_mod

        at = request.ai_triage or {}
        category = at.get("category")
        department = request.department or (at.get("understanding") or {}).get("business_unit")
        candidates = self._pick_owner_team_candidates(org_id=request.org_id, category=category,
                                                 department=department)
        for team in candidates:
            pick = teams_mod.pick_from_pool(db, team_id=team.id, exclude_user_id=request.requester_user_id)
            if pick and pick.user_id:
                request.assigned_to_user_id = pick.user_id
                request.handoff_holder = "human"
                request.handoff_user_id = pick.user_id
                return

    def _maybe_autostart_workflow(self, request: IntakeRequest, actor: User) -> None:
        """Confidence-gated auto-start: when the triage is confident about the
        workflow pick (and didn't flag it for a human), kick the workflow off
        automatically; otherwise it stays a one-click suggestion on the ticket.
        Best-effort and post-commit — never breaks the create path."""
        db = self.db
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

    def file_request_for_contract(self, *, actor: User, contract) -> IntakeRequest:
        """The request behind a contract uploaded straight into the CLM. Workflows run
        on requests, so without one an uploaded contract never got a lifecycle, an owner
        queue or an approval. Reuses the contract's request if it already has one."""
        db = self.db
        existing = db.scalars(select(IntakeRequest).where(
            IntakeRequest.org_id == contract.org_id, IntakeRequest.contract_id == contract.id,
        ).order_by(IntakeRequest.submitted_at.desc())).first()
        if existing is not None:
            return existing
        now = utcnow()
        label = (contract.contract_type or "Uploaded contract").strip()
        r = IntakeRequest(
            org_id=contract.org_id, ref=self._next_ref(), source="upload",
            requester_user_id=actor.id, requester_name=actor.full_name or actor.email,
            type_label=label[:120], subject=(contract.title or label)[:200],
            description=f"Uploaded straight into the CLM: {contract.title}",
            field_values={}, priority="Medium", status="open", stage="new",
            sla_hours=settings.intake_default_sla_hours,
            assigned_to_user_id=contract.owner_user_id or actor.id, contract_id=contract.id,
            parties=[{"name": contract.counterparty_name, "role": "counterparty", "is_person": False}]
            if contract.counterparty_name else None,
            submitted_at=now, handoff_holder="human",
            stage_timestamps=[{"stage": "new", "at": now.isoformat()}],
            created_by_user_id=actor.id, updated_by_user_id=actor.id,
        )
        db.add(r)
        db.flush()
        write_audit_log(db, action="intake.created", resource_type="intake_request", resource_id=r.id,
                        org_id=r.org_id, actor_user_id=actor.id,
                        after={"ref": r.ref, "type": r.type_label, "contract_id": contract.id, "source": "upload"})
        write_timeline_event(db, org_id=r.org_id, resource_type="intake_request", resource_id=r.id,
                             event_type="intake.created", title=f"Filed for uploaded contract “{contract.title}”",
                             actor_user_id=actor.id, details={"contract_id": contract.id})
        return r

    def create_request(self, *, actor: User, payload, request_id: str | None = None,
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
        db = self.db
        # The agreement wizard names its form in `request_form`; its required and
        # typed fields are enforced here, not only in the browser.
        from app.intake.agreement_forms import form_fields

        field_values = _validate_field_values(form_fields((payload.field_values or {}).get("request_form")),
                                              payload.field_values)
        counterparty_id, legal_entity_id = self._resolve_party_records(actor.org_id, field_values)
        # Check every attached file before anything is saved: a file that is too
        # big or the wrong type must stop the filing, not leave a request behind
        # without its attachment (which is what made people retry and duplicate).
        raw = [(a, _decode_b64(a.content_b64, a.filename)) for a in (getattr(payload, "attachments", None) or [])]
        if sum(len(content) for _, content in raw) > _FILING_MAX_BYTES:
            raise HTTPException(413, "Attachments add up to more than 40 MB — file the largest ones separately")
        attachments = [_prepare_attachment(a.filename, a.mime_type, content) for a, content in raw]
        if defer_triage:
            duplicate = self._recent_duplicate_request(actor=actor, payload=payload,
                                                  field_values=field_values)
            if duplicate is not None:
                return self.serialize_request(duplicate)
        now = utcnow()
        r = IntakeRequest(
            org_id=actor.org_id, ref=self._next_ref(), source=payload.source,
            requester_user_id=actor.id, requester_name=payload.requester_name,
            department=payload.department,
            type_label=payload.type_label.strip(),
            subject=_derive_subject(getattr(payload, "subject", None), payload.description, payload.type_label),
            description=payload.description or "",
            field_values=field_values, priority=payload.priority,
            status="open", stage="new",
            sla_hours=settings.intake_default_sla_hours,
            external_message_id=external_message_id,
            counterparty_id=counterparty_id, legal_entity_id=legal_entity_id,
            submitted_at=now, handoff_holder="queue", conversation=conversation,
            stage_timestamps=[{"stage": "new", "at": now.isoformat()}],
            created_by_user_id=actor.id, updated_by_user_id=actor.id,
        )
        # Context-aware triage first: the AI reads the WHOLE request and decides
        # category/complexity/risk/urgency + the workflow pick, driving the gates,
        # priority and owner below. Falls back to the keyword classifier on failure.
        # Seed the parties list with EVERY party the form captured. Only the first
        # counterparty used to be added, so a second counterparty or a novation's
        # incoming party never appeared on the request's parties.
        seeded = _initial_parties(field_values)
        if seeded:
            r.parties = seeded
        if defer_triage:
            r.ai_triage = {"status": "pending"}  # run_intake_triage fills this in the background
        else:
            self._apply_ai_triage(r)
        db.add(r)
        db.flush()
        # Saved in the same transaction as the request and before triage is queued,
        # so triage can read what was attached.
        for att in attachments:
            self._attach(actor=actor, r=r, att=att)
        write_audit_log(db, action="intake.created", resource_type="intake_request", resource_id=r.id,
                        org_id=actor.org_id, actor_user_id=actor.id, request_id=request_id,
                        after={"ref": r.ref, "type": r.type_label, "priority": r.priority})
        write_timeline_event(db, org_id=actor.org_id, resource_type="intake_request", resource_id=r.id,
                             event_type="intake.created", title=f"Request filed — {r.type_label}",
                             actor_user_id=actor.id, request_id=request_id)
        # The relationship-note job is queued HERE, inside the request's own
        # transaction: if the request exists, its job exists, so it can no longer
        # be lost to a crash or swallowed by a bare except.
        r.screening = {"status": "pending", "note": "Relationship check queued."}
        screening_job = self.queue_screening(r, actor.id)
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
            self.dispatch_screening(screening_job)
            return self.serialize_request(r)
        self._assign_and_notify(r, actor)
        db.commit()
        db.refresh(r)
        self.dispatch_screening(screening_job)
        self._enrich_after_commit(r, actor)
        return self.serialize_request(r)

    def _apply_ai_triage(self, r: IntakeRequest) -> None:
        """Context-aware triage: the AI reads the WHOLE request and decides category,
        complexity, risk, urgency and the workflow pick, which drive priority and
        owner."""
        from app.intake import triage_agent

        db = self.db
        r.ai_triage = triage_agent.triage(db, r)
        _urgency = (r.ai_triage.get("understanding") or {}).get("urgency")
        if _urgency in ("Low", "Medium", "High"):
            r.priority = _urgency

    def _assign_and_notify(self, r: IntakeRequest, actor: User) -> None:
        """Auto-assign the owner from the triage read: expertise picks the team,
        least-loaded within."""
        self._assign_owner_from_triage(r)
        if r.assigned_to_user_id and r.assigned_to_user_id != actor.id:
            self._notify(r, r.assigned_to_user_id, "intake.assigned",
                    f"{r.ref} assigned to you", f"{r.type_label} — priority {r.priority}.")

    def _enrich_after_commit(self, r: IntakeRequest, actor: User) -> None:
        """Enrichment once the request is safely persisted: the deep litigation
        assessment and the confidence-gated workflow autostart.

        Screening is NOT here any more — it is a durable job queued in the
        request's own transaction and dispatched at creation, so it can no longer
        be lost to a crash on this path."""
        from app.intake.litigation_agent import is_litigation

        if is_litigation(r):
            self._attach_flow_suggestion(r)
        self._maybe_autostart_workflow(r, actor)

    def run_intake_triage(self, *, request_id: str, actor_id: str | None) -> None:
        """The background half of a web-form submission: AI triage, owner
        assignment, screening and workflow autostart. A no-op once triage has run."""
        db = self.db
        r = db.get(IntakeRequest, request_id)
        actor = db.get(User, actor_id) if actor_id else None
        if r is None or actor is None:
            raise RuntimeError("Intake request or requester not found for triage")
        if (r.ai_triage or {}).get("status") != "pending":
            return
        self._apply_ai_triage(r)
        self._assign_and_notify(r, actor)
        write_timeline_event(db, org_id=r.org_id, resource_type="intake_request", resource_id=r.id,
                             event_type="intake.triaged", title="AI triage complete", actor_user_id=actor.id)
        db.commit()
        db.refresh(r)
        self._enrich_after_commit(r, actor)

    def _resolve_party_records(self, org_id: str, field_values: dict | None) -> tuple[str | None, str | None]:
        """Check every picked entity / counterparty exists in this org and is active,
        and store its registered name next to the id (the browser's copy of the
        name is not trusted). Returns (counterparty_id, legal_entity_id)."""
        from app.parties import service as parties

        db = self.db
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

    def _recent_duplicate_request(self, *, actor: User, payload,
                                  field_values: dict | None = None) -> IntakeRequest | None:
        """The same person filing the same request again within a couple of minutes is a
        retry (a cut connection, a double click), not a second ticket.

        "The same" means every answer matches, not just the type and the note: two
        different agreements filed with the same quick-phrase note used to be merged
        into the first, silently losing the second."""
        db = self.db
        # `field_values` is a plain JSON column, so a request filed without answers
        # stores JSON `null`, not SQL NULL — COALESCE alone never turned it into {},
        # and identical answer-less filings (quick questions, Ask Aegis, a double
        # click) were never recognised as retries. NULLIF maps JSON null to SQL NULL.
        stored = func.nullif(cast(IntakeRequest.field_values, JSONB), sql_text("'null'::jsonb"))
        same_answers = func.coalesce(stored, cast({}, JSONB)) == cast(field_values or {}, JSONB)
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

    def set_parties(self, *, actor: User, request_id: str, parties: list) -> dict:
        """Replace a request's parties, then refresh the relationship note so it
        reflects the new party set."""
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
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
        # would keep coming back. [] means "the reviewer cleared the parties".
        r.parties = clean
        write_audit_log(db, action="intake.parties.updated", resource_type="intake_request",
                        resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                        after={"count": len(clean)})
        # A changed party list must actually re-run, so this job is deliberately
        # NOT idempotent against the original — folding into it would return the
        # note for the parties the reviewer just replaced.
        r.screening = {"status": "pending", "note": "Relationship check queued."}
        rescreen_job = self.queue_screening(r, actor.id, idempotent=False)
        db.commit()
        db.refresh(r)
        self.dispatch_screening(rescreen_job)
        self._attach_flow_suggestion(r)
        return self.serialize_request(r)

    def queue_screening(self, r: IntakeRequest, actor_id: str | None,
                        *, idempotent: bool = True):
        """Enqueue the relationship check as a durable job.

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

        db = self.db
        return create_job(
            db, org_id=r.org_id, job_type="intake_screening",
            resource_type="intake_request", resource_id=r.id,
            created_by_user_id=actor_id,
            idempotency_key=f"intake_screening:{r.id}" if idempotent else None,
            metadata={"intake_request_id": r.id},
        )

    def dispatch_screening(self, job) -> None:
        """Hand a committed screening job to a worker.

        Best-effort on purpose: the row is already durable, so a broker that is
        down costs a few minutes rather than the screen — `reclaim_stale_jobs`
        re-dispatches anything left QUEUED.
        """
        from app.jobs.service import dispatch_job

        db = self.db
        if job is None:
            return
        try:
            dispatch_job(db, job=job)
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("could not dispatch screening job %s", getattr(job, "id", "?"),
                           exc_info=True)

    def run_intake_screening(self, *, request_id: str, actor_id: str | None) -> None:
        """Job body. Exceptions propagate on purpose — a failed screen must fail
        its job, so it is retried and then visible, not swallowed into a status
        field nobody queries."""
        from app.intake.screening import run_screening

        db = self.db
        r = db.get(IntakeRequest, request_id)
        if r is None:
            return  # request deleted before the job ran; nothing to screen
        run_screening(db, r, actor_user_id=actor_id)

    def _notify(self, r: IntakeRequest, user_id: str | None, event_type: str,
                subject: str, body: str) -> None:
        """Best-effort in-app notification on intake events (never raises)."""
        db = self.db
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

    def list_requests(self, *, user: User, status_filter: str | None = None,
                      mine: bool = False) -> list[dict]:
        db = self.db
        q = select(IntakeRequest).where(_accessible(user))
        if mine:
            q = q.where(IntakeRequest.requester_user_id == user.id)
        if status_filter:
            q = q.where(IntakeRequest.status == status_filter)
        rows = db.scalars(q.order_by(IntakeRequest.submitted_at.desc())).all()
        self._prefetch_serialize_dependencies(rows)
        return [self.serialize_request(r) for r in rows]

    def _prefetch_serialize_dependencies(self, rows: list[IntakeRequest]) -> None:
        """Warm the session identity map so serialize_request's per-row db.get()
        calls (requester/assignee labels, contract title) hit the
        map instead of issuing one query per row — this was an N+1 on the main
        Legal Intake queue. Behavior-preserving: serialize_request is unchanged,
        db.get() just becomes a cache hit for anything fetched here."""
        from app.contracts.models import Contract

        db = self.db
        user_ids = {r.requester_user_id for r in rows if r.requester_user_id} | {
            r.assigned_to_user_id for r in rows if r.assigned_to_user_id
        }
        contract_ids = {r.contract_id for r in rows if r.contract_id}
        if user_ids:
            db.scalars(select(User).where(User.id.in_(user_ids))).all()
        if contract_ids:
            db.scalars(select(Contract).where(Contract.id.in_(contract_ids))).all()
        # Latest workflow run per request, attached to the row so the stage column
        # doesn't cost one query per request.
        from app.workflows.models import WorkflowRun

        latest: dict = {}
        for run in db.scalars(select(WorkflowRun).where(WorkflowRun.request_id.in_([r.id for r in rows]))
                              .order_by(WorkflowRun.created_at.asc())).all() if rows else []:
            latest[run.request_id] = run
        for r in rows:
            r._latest_run = latest.get(r.id)

    def get_request(self, *, user: User, request_id: str) -> IntakeRequest:
        db = self.db
        r = db.get(IntakeRequest, request_id)
        if r is None or r.org_id != user.org_id:
            raise HTTPException(404, "Request not found")
        from app.core.rbac import has_permission

        can_read = is_org_admin(user) or has_permission(user.permission_values, "intake:read")
        if not can_read and r.requester_user_id != user.id:
            raise HTTPException(404, "Request not found")
        return r

    def record_handoff(self, *, actor: User | None, request: IntakeRequest, to_holder: str,
                       to_user_id: str | None = None, reason: str | None = None,
                       actor_type: str = "user", sync_assignee: bool = True) -> IntakeHandoff:
        db = self.db
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

    def serialize_handoff(self, h: IntakeHandoff) -> dict:
        return {
            "id": h.id, "from_holder": h.from_holder, "to_holder": h.to_holder,
            "to_user_id": h.to_user_id, "to_label": self._user_label(h.to_user_id),
            "reason": h.reason, "actor_type": h.actor_type, "created_at": _iso(h.created_at),
        }

    def list_handoffs(self, *, request: IntakeRequest) -> list[dict]:
        db = self.db
        rows = db.scalars(
            select(IntakeHandoff).where(IntakeHandoff.request_id == request.id)
            .order_by(IntakeHandoff.created_at)
        ).all()
        return [self.serialize_handoff(h) for h in rows]

    def handoff(self, *, actor: User, request_id: str, payload) -> dict:
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
        self.record_handoff(actor=actor, request=r, to_holder=payload.to_holder,
                       to_user_id=payload.to_user_id, reason=payload.reason,
                       sync_assignee=payload.sync_assignee)
        db.commit()
        db.refresh(r)
        return self.serialize_request(r)

    def _approval_gate_blocked(self, *, actor: User, request: IntakeRequest,
                               attempted: str, http_request_id: str | None) -> None:
        """Part 0.5 — a 🔒 gate names the only user whose approve sticks; anyone else
        is refused and the refusal is logged on an isolated session (survives the
        403 rollback) plus an intake audit row."""
        db = self.db
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

    def record_triage_action(self, *, actor: User, request_id: str, payload,
                             http_request_id: str | None = None) -> dict:
        """Request-management actions on a ticket: reassign, escalate, snooze, close.
        (Triage verdicts removed — a request is resolved by its workflow / approval
        ladder, not by approving an AI recommendation.)"""
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
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
            self.record_handoff(actor=actor, request=r, to_holder="human",
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
            self._transition(request=r, actor=actor, to_status="closed", to_stage="complete",
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
            self._transition(request=r, actor=actor, to_status="escalated",
                        audit_action="intake.auto_escalated", after={"priority": "Critical"},
                        timeline_title="Escalated", request_id=http_request_id)

        db.commit()
        db.refresh(r)
        return self.serialize_request(r)

    def _rung_label(self, *, user_id: str | None, team_id: str | None, role: str | None) -> str:
        db = self.db
        if user_id:
            u = db.get(User, user_id)
            return (u.full_name or u.email) if u else user_id
        if team_id:
            t = db.get(IntakeTeam, team_id)
            return t.name if t else (role or "Approver")
        return role or "Approver"

    def preview_approvals(self, *, actor: User, payload) -> dict:
        """Who will approve and sign a request that hasn't been filed yet — the
        wizard's "Check Approvers". Builds the request in memory (never saved) and
        runs the same workflow selection and step conditions filing will, so the
        answer is the real one, not a guess.

        The owner is not predicted: it is chosen by the AI read of the request,
        which only runs once it is filed."""
        from app.intake.teams import member_users
        from app.parties.models import Counterparty, LegalEntity
        from app.workflows.service import planned_approvals, select_flow

        db = self.db
        fv = dict(payload.field_values or {})
        r = IntakeRequest(org_id=actor.org_id, type_label=payload.type_label.strip(),
                          description=payload.description or "", field_values=fv,
                          priority=payload.priority, department=payload.department)
        flow = select_flow(db, request=r)
        approvers = []
        for t in planned_approvals(db, org_id=actor.org_id, steps=flow.steps or [], request=r) if flow else []:
            team = t["approver_team_id"]
            approvers.append({
                "step_name": t["step_name"],
                "approver": self._rung_label(user_id=t["approver_user_id"], team_id=team,
                                        role=t["approver_role"]),
                "kind": "person" if t["approver_user_id"] else "team" if team else "role",
                "mode": t.get("mode", "any"),
                # An empty team can't decide anything; filing would stall there.
                "members": len(member_users(db, team_id=team, org_id=actor.org_id)) if team else None,
            })
        entity = db.get(LegalEntity, fv["entity_id"]) if fv.get("entity_id") else None
        party = db.get(Counterparty, fv["counterparty_id"]) if fv.get("counterparty_id") else None
        ours_ok = entity is not None and entity.org_id == actor.org_id
        theirs_ok = party is not None and party.org_id == actor.org_id
        return {
            "workflow": {"id": flow.id, "name": flow.name} if flow else None,
            "approvers": approvers,
            "our_signatory": entity.authorised_signatory if ours_ok else None,
            "our_entity": entity.name if ours_ok else None,
            "counterparty_contact": party.contact_email if theirs_ok else None,
        }

    def _serialize_chain(self, rows: list) -> list[dict]:
        """One rung per ApprovalRequest, in order — the intake ladder strip."""
        from app.approvals.service import _quorum_needed

        db = self.db
        return [{
            "approval_request_id": a.id,
            "step_order": a.step_order,
            "status": a.status,
            "mode": getattr(a, "mode", "any"),
            "approvals": (a.metadata_json or {}).get("approvals", 0),
            "needed": _quorum_needed(db, a),
            "approver_label": self._rung_label(user_id=a.approver_user_id,
                                          team_id=a.approver_team_id, role=a.approver_role),
            "due_at": a.due_at.isoformat() if a.due_at else None,
        } for a in rows]

    def get_approval_chain(self, *, actor: User, request_id: str) -> list[dict]:
        """The request's approval rungs. If a chain is live, its real rows (RAG). If
        not yet submitted, the PLANNED rungs (status 'planned'): one per Approval
        step of the request's workflow (its run, else the workflow it would get) —
        exactly who the workflow will ask."""
        from app.approvals.models import ApprovalRequest

        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
        rows = db.scalars(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.org_id == actor.org_id,
                ApprovalRequest.intake_request_id == r.id,
            )
            .order_by(ApprovalRequest.created_at, ApprovalRequest.step_order)
        ).all()
        if rows:
            return self._serialize_chain(rows)
        if r.status in ("closed", "approved"):
            return []
        from app.workflows.service import get_run_for_request, planned_approvals, select_flow

        run = get_run_for_request(db, request_id=r.id, org_id=actor.org_id)
        flow = None if run else select_flow(db, request=r)
        steps = (run.steps if run else (flow.steps if flow else None)) or []
        targets = planned_approvals(db, org_id=actor.org_id, steps=steps, request=r)
        # Only the workflow's Approval steps decide who approves, so the plan is
        # exactly their targets (condition-driven chains never take one over).
        rungs: list[dict] = [{
            "approval_request_id": None,
            "status": "planned",
            "approver_label": self._rung_label(user_id=t.get("approver_user_id"),
                                          team_id=t.get("approver_team_id"), role=t.get("approver_role")),
            "due_at": None,
        } for t in targets]
        for i, rung in enumerate(rungs):
            rung["step_order"] = i + 1
        return rungs

    def serialize_task(self, t: IntakeTask) -> dict:
        return {
            "id": t.id, "request_id": t.request_id, "title": t.title, "description": t.description,
            "assignee_user_id": t.assignee_user_id, "assignee_label": self._user_label(t.assignee_user_id),
            "status": t.status, "sort_order": t.sort_order, "effort_minutes": t.effort_minutes,
        }

    def list_tasks(self, *, actor: User, request_id: str) -> list[dict]:
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
        rows = db.scalars(
            select(IntakeTask).where(IntakeTask.request_id == r.id)
            .order_by(IntakeTask.sort_order, IntakeTask.created_at)
        ).all()
        return [self.serialize_task(t) for t in rows]

    def create_task(self, *, actor: User, request_id: str, payload) -> dict:
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
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
        return self.serialize_task(t)

    def _get_task(self, actor: User, task_id: str) -> IntakeTask:
        db = self.db
        t = db.get(IntakeTask, task_id)
        if t is None or t.org_id != actor.org_id:
            raise HTTPException(404, "Task not found")
        self.get_request(user=actor, request_id=t.request_id)  # access gate
        return t

    def update_task(self, *, actor: User, task_id: str, payload) -> dict:
        db = self.db
        t = self._get_task(actor, task_id)
        for attr in ("title", "description", "assignee_user_id", "status", "sort_order"):
            val = getattr(payload, attr, None)
            if val is not None:
                setattr(t, attr, val)
        t.updated_by_user_id = actor.id
        db.commit()
        db.refresh(t)
        return self.serialize_task(t)

    def delete_task(self, *, actor: User, task_id: str) -> None:
        db = self.db
        t = self._get_task(actor, task_id)
        db.delete(t)
        db.commit()

    def log_effort(self, *, actor: User, task_id: str, minutes: int) -> dict:
        db = self.db
        if minutes <= 0:
            raise HTTPException(422, "minutes must be positive")
        t = self._get_task(actor, task_id)
        t.effort_minutes = (t.effort_minutes or 0) + minutes
        t.updated_by_user_id = actor.id
        write_audit_log(db, action="intake.task.effort_logged", resource_type="intake_request",
                        resource_id=t.request_id, org_id=actor.org_id, actor_user_id=actor.id,
                        after={"task_id": t.id, "minutes": minutes, "total": t.effort_minutes})
        db.commit()
        db.refresh(t)
        return self.serialize_task(t)

    def _pool_scope_for_user(self, user: User) -> tuple[set[str], set[str]] | None:
        """None = no narrowing (show every unassigned request — today's
        staff-wide default, used when the viewer isn't on any active team).
        Otherwise ``(expertise, departments)`` — the union of matter categories
        and business units every active team the user belongs to serves — except
        a team with a BLANK departments list, which per the admin Teams UI's own
        documented semantics ("Serves (business units) ... Blank = serves all")
        means that team alone unlocks the unrestricted result."""
        db = self.db
        team_ids = db.scalars(
            select(IntakeTeamMember.team_id).where(
                IntakeTeamMember.user_id == user.id, IntakeTeamMember.active.is_(True),
            )
        ).all()
        if not team_ids:
            return None
        teams = db.scalars(
            select(IntakeTeam).where(IntakeTeam.id.in_(team_ids), IntakeTeam.active.is_(True))
        ).all()
        if not teams:
            return None
        expertise: set[str] = set()
        depts: set[str] = set()
        for t in teams:
            if not t.departments:
                return None  # "serves all"
            depts.update((d or "").strip().lower() for d in t.departments if d)
            expertise.update((e or "").strip().lower() for e in (t.expertise or []) if e)
        return expertise, depts

    def list_pool_requests(self, *, user: User) -> list[dict]:
        """The role/team-scoped "All requests" queue: every open request visible
        per role (`_accessible`) and, if the viewer belongs to a team with a
        configured department list, narrowed to those departments — assigned and
        unassigned alike (an assigned row just carries its `assigned_to_label`;
        the frontend shows "Assign to me" only for the unassigned ones). A
        request with no department set, or whose department doesn't match any
        team but whose AI-triaged matter category is in one of the viewer's
        teams' expertise, also stays visible — `department` is the requester's
        own business unit (a free intake-form pick like "Enterprise Systems"),
        a fundamentally different vocabulary from a team's "Serves" list, so a
        request can't be attributed to any one team by department alone. This
        mirrors the never-assign-to-nobody fallback in
        workflows/service.py::_assign_step, and keeps this in parity with
        `_pick_owner_team`, which already treats expertise as the primary signal
        and department as a soft, non-exclusive narrowing."""
        db = self.db
        q = select(IntakeRequest).where(
            _accessible(user),
            IntakeRequest.status.in_(OPEN_STATUSES),
        )
        rows = db.scalars(q.order_by(IntakeRequest.submitted_at.desc())).all()
        scope = self._pool_scope_for_user(user)
        rows = [r for r in rows if _in_pool_scope(r, scope)]
        self._prefetch_serialize_dependencies(rows)
        return [self.serialize_request(r) for r in rows]

    def claim_request(self, *, actor: User, request_id: str,
                      http_request_id: str | None = None) -> dict:
        """Self-assign an unclaimed pool request — the manual counterpart to the
        auto-balancer's pick_from_pool, gated the same way as reassigning to
        someone else (record_triage_action's "reassigned" branch), just always
        targeting the actor."""
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
        _require_staff(actor)
        if r.status == "closed":
            raise HTTPException(409, "Request is closed — file a follow-up")
        if r.assigned_to_user_id:
            raise HTTPException(409, "Already assigned — someone else claimed this first")
        scope = self._pool_scope_for_user(actor)
        if not _in_pool_scope(r, scope):
            raise HTTPException(403, "This request isn't in your team's pool")

        self.record_handoff(actor=actor, request=r, to_holder="human", to_user_id=actor.id,
                       reason="Self-assigned from pool")
        r.triaged_by_user_id = actor.id
        r.triaged_at = utcnow()
        r.triage_action = "self_assigned"
        write_audit_log(db, action="intake.assigned", resource_type="intake_request",
                        resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id,
                        request_id=http_request_id, after={"assignee": actor.id})
        db.commit()
        db.refresh(r)
        return self.serialize_request(r)

    def my_work(self, *, user: User) -> dict:
        db = self.db
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
            "my_tickets": [self.serialize_request(r) for r in tickets],
            "my_tasks": [self.serialize_task(t) for t in tasks],
        }

    def promote(self, *, actor: User, request_id: str, payload,
                http_request_id: str | None = None) -> dict:
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
        _require_staff(actor)
        from app.contracts.models import Contract
        c = db.get(Contract, payload.target_id)
        if c is None or c.org_id != actor.org_id:
            raise HTTPException(404, "Contract not found")
        r.contract_id = c.id
        r.updated_by_user_id = actor.id
        write_audit_log(db, action="intake.promoted", resource_type="intake_request", resource_id=r.id,
                        org_id=actor.org_id, actor_user_id=actor.id, request_id=http_request_id,
                        after={"target": payload.target, "id": payload.target_id})
        write_timeline_event(db, org_id=actor.org_id, resource_type="intake_request", resource_id=r.id,
                             event_type="intake.promoted", title=f"Promoted to {payload.target}",
                             actor_user_id=actor.id)
        db.commit()
        db.refresh(r)
        return self.serialize_request(r)

    def build_sla_legs(self, *, request: IntakeRequest, now=None) -> dict:
        db = self.db
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
                return self._user_label(uid) or "Reviewer"
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

    def set_pause(self, *, actor: User, request_id: str, paused: bool,
                  http_request_id: str | None = None) -> dict:
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
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
        return self.serialize_request(r)

    def sla_ops_summary(self, *, org_id: str) -> dict:
        db = self.db
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
            "workload": [{"user_id": uid, "name": self._user_label(uid), "open": n,
                          "overdue": workload_over.get(uid, 0)} for uid, n in sorted(workload.items(), key=lambda x: -x[1])],
        }

    def run_sla_sweep(self, *, org_id: str | None = None) -> dict:
        """Recompute posture both directions; escalate on the upward edge only
        (Part 0.11/0.16). Idempotent. Returns a counter dict."""
        db = self.db
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
                    self._notify(r, r.assigned_to_user_id, "intake.sla_breached",
                            f"{r.ref} breached its SLA",
                            f"{r.type_label} is overdue ({r.sla_hours}h window) and was escalated.")
                elif new != "overdue":
                    downgraded += 1
        db.commit()
        return {"escalated": escalated, "breached": breached, "downgraded": downgraded}

    def list_assignees(self, *, org_id: str) -> list[dict]:
        from app.core.enums import UserStatus

        db = self.db
        rows = db.scalars(
            select(User).where(User.org_id == org_id, User.status == UserStatus.ACTIVE)
            .order_by(User.full_name)
        ).all()
        return [{"id": u.id, "name": u.full_name or u.email, "email": u.email} for u in rows]

    def update_request(self, *, actor: User, request_id: str, payload,
                       http_request_id: str | None = None) -> dict:
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
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
            from app.intake.agreement_forms import form_fields

            form = (r.field_values or {}).get("request_form") or (payload.field_values or {}).get("request_form")
            r.field_values = _validate_field_values(form_fields(form), payload.field_values)
        if payload.stage is not None and payload.stage != r.stage:
            valid = stages_for()
            if payload.stage not in valid:
                raise HTTPException(422, f"Unknown stage '{payload.stage}'")
            # Only the head/tail stages carry a canonical status (new→open,
            # complete→closed). Mid-stage moves must NOT change status — a default of
            # "open" here silently de-escalated an `escalated` request on any advance.
            mapped = _STAGE_STATUS.get(payload.stage)  # None ⇒ _transition preserves status
            self._transition(
                request=r, actor=actor, to_status=mapped, to_stage=payload.stage,
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
        return self.serialize_request(r)

    def _attach(self, *, actor: User, r: IntakeRequest, att: _PreparedAttachment):
        from app.intake.models import IntakeDocument

        db = self.db
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

    def add_document(self, *, actor: User, request_id: str, filename: str,
                     mime_type: str, content: bytes) -> dict:
        """Attach a document to a request that is already filed."""
        db = self.db
        att = _prepare_attachment(filename, mime_type, content)
        r = self.get_request(user=actor, request_id=request_id)
        doc = self._attach(actor=actor, r=r, att=att)
        db.commit()
        db.refresh(doc)
        return serialize_document(doc)

    def list_documents(self, *, actor: User, request_id: str) -> list[dict]:
        from app.intake.models import IntakeDocument
        db = self.db
        r = self.get_request(user=actor, request_id=request_id)
        docs = db.scalars(select(IntakeDocument).where(IntakeDocument.request_id == r.id)
                          .order_by(IntakeDocument.created_at)).all()
        return [serialize_document(d) for d in docs]


# --- DI-MIGRATION: temporary wrappers ---------------------------------------
# Thin delegates to IntakeService, kept so external importers
# (app.workflows.service, app.approvals.*, app.jobs, app.ai.tool_runtime, tests)
# keep working while they move to Depends(get_intake_service). Tracked in
# backend/DI_MIGRATION.md.


def serialize_request(db: Session, r: IntakeRequest) -> dict:
    return IntakeService(db).serialize_request(r)


def _transition(
    db: Session, *, request: IntakeRequest, actor: User | None,
    to_status: str | None = None, to_stage: str | None = None,
    audit_action: str, before: dict | None = None, after: dict | None = None,
    timeline_title: str | None = None, actor_type: str = "user", request_id: str | None = None,
) -> None:
    return IntakeService(db)._transition(
        request=request, actor=actor, to_status=to_status, to_stage=to_stage, audit_action=audit_action, before=before, after=after, timeline_title=timeline_title, actor_type=actor_type, request_id=request_id,
    )


def resuggest_flow(db: Session, *, actor: User, request_id: str) -> dict:
    return IntakeService(db).resuggest_flow(actor=actor, request_id=request_id)


def _pick_owner_team_candidates(db: Session, *, org_id: str, category: str | None,
                                department: str | None) -> list:
    return IntakeService(db)._pick_owner_team_candidates(
        org_id=org_id, category=category, department=department,
    )


def _pick_owner_team(db: Session, *, org_id: str, category: str | None, department: str | None):
    return IntakeService(db)._pick_owner_team(
        org_id=org_id, category=category, department=department,
    )


def _maybe_autostart_workflow(db: Session, request: IntakeRequest, actor: User) -> None:
    return IntakeService(db)._maybe_autostart_workflow(request, actor)


def file_request_for_contract(db: Session, *, actor: User, contract) -> IntakeRequest:
    return IntakeService(db).file_request_for_contract(actor=actor, contract=contract)


def create_request(db: Session, *, actor: User, payload, request_id: str | None = None,
                   conversation: list | None = None, defer_triage: bool = False,
                   external_message_id: str | None = None) -> dict:
    return IntakeService(db).create_request(
        actor=actor, payload=payload, request_id=request_id, conversation=conversation, defer_triage=defer_triage, external_message_id=external_message_id,
    )


def run_intake_triage(db: Session, *, request_id: str, actor_id: str | None) -> None:
    return IntakeService(db).run_intake_triage(request_id=request_id, actor_id=actor_id)


def set_parties(db: Session, *, actor: User, request_id: str, parties: list) -> dict:
    return IntakeService(db).set_parties(actor=actor, request_id=request_id, parties=parties)


def queue_screening(db: Session, r: IntakeRequest, actor_id: str | None,
                    *, idempotent: bool = True):
    return IntakeService(db).queue_screening(r, actor_id, idempotent=idempotent)


def dispatch_screening(db: Session, job) -> None:
    return IntakeService(db).dispatch_screening(job)


def run_intake_screening(db: Session, *, request_id: str, actor_id: str | None) -> None:
    return IntakeService(db).run_intake_screening(request_id=request_id, actor_id=actor_id)


def list_requests(db: Session, *, user: User, status_filter: str | None = None,
                  mine: bool = False) -> list[dict]:
    return IntakeService(db).list_requests(user=user, status_filter=status_filter, mine=mine)


def get_request(db: Session, *, user: User, request_id: str) -> IntakeRequest:
    return IntakeService(db).get_request(user=user, request_id=request_id)


def record_handoff(db: Session, *, actor: User | None, request: IntakeRequest, to_holder: str,
                   to_user_id: str | None = None, reason: str | None = None,
                   actor_type: str = "user", sync_assignee: bool = True) -> IntakeHandoff:
    return IntakeService(db).record_handoff(
        actor=actor, request=request, to_holder=to_holder, to_user_id=to_user_id, reason=reason, actor_type=actor_type, sync_assignee=sync_assignee,
    )


def serialize_handoff(db: Session, h: IntakeHandoff) -> dict:
    return IntakeService(db).serialize_handoff(h)


def list_handoffs(db: Session, *, request: IntakeRequest) -> list[dict]:
    return IntakeService(db).list_handoffs(request=request)


def handoff(db: Session, *, actor: User, request_id: str, payload) -> dict:
    return IntakeService(db).handoff(actor=actor, request_id=request_id, payload=payload)


def record_triage_action(db: Session, *, actor: User, request_id: str, payload,
                         http_request_id: str | None = None) -> dict:
    return IntakeService(db).record_triage_action(
        actor=actor, request_id=request_id, payload=payload, http_request_id=http_request_id,
    )


def preview_approvals(db: Session, *, actor: User, payload) -> dict:
    return IntakeService(db).preview_approvals(actor=actor, payload=payload)


def get_approval_chain(db: Session, *, actor: User, request_id: str) -> list[dict]:
    return IntakeService(db).get_approval_chain(actor=actor, request_id=request_id)


def serialize_task(db: Session, t: IntakeTask) -> dict:
    return IntakeService(db).serialize_task(t)


def list_tasks(db: Session, *, actor: User, request_id: str) -> list[dict]:
    return IntakeService(db).list_tasks(actor=actor, request_id=request_id)


def create_task(db: Session, *, actor: User, request_id: str, payload) -> dict:
    return IntakeService(db).create_task(actor=actor, request_id=request_id, payload=payload)


def update_task(db: Session, *, actor: User, task_id: str, payload) -> dict:
    return IntakeService(db).update_task(actor=actor, task_id=task_id, payload=payload)


def delete_task(db: Session, *, actor: User, task_id: str) -> None:
    return IntakeService(db).delete_task(actor=actor, task_id=task_id)


def log_effort(db: Session, *, actor: User, task_id: str, minutes: int) -> dict:
    return IntakeService(db).log_effort(actor=actor, task_id=task_id, minutes=minutes)


def list_pool_requests(db: Session, *, user: User) -> list[dict]:
    return IntakeService(db).list_pool_requests(user=user)


def claim_request(db: Session, *, actor: User, request_id: str,
                  http_request_id: str | None = None) -> dict:
    return IntakeService(db).claim_request(
        actor=actor, request_id=request_id, http_request_id=http_request_id,
    )


def my_work(db: Session, *, user: User) -> dict:
    return IntakeService(db).my_work(user=user)


def promote(db: Session, *, actor: User, request_id: str, payload,
            http_request_id: str | None = None) -> dict:
    return IntakeService(db).promote(
        actor=actor, request_id=request_id, payload=payload, http_request_id=http_request_id,
    )


def build_sla_legs(db: Session, *, request: IntakeRequest, now=None) -> dict:
    return IntakeService(db).build_sla_legs(request=request, now=now)


def set_pause(db: Session, *, actor: User, request_id: str, paused: bool,
              http_request_id: str | None = None) -> dict:
    return IntakeService(db).set_pause(
        actor=actor, request_id=request_id, paused=paused, http_request_id=http_request_id,
    )


def sla_ops_summary(db: Session, *, org_id: str) -> dict:
    return IntakeService(db).sla_ops_summary(org_id=org_id)


def run_sla_sweep(db: Session, *, org_id: str | None = None) -> dict:
    return IntakeService(db).run_sla_sweep(org_id=org_id)


def list_assignees(db: Session, *, org_id: str) -> list[dict]:
    return IntakeService(db).list_assignees(org_id=org_id)


def update_request(db: Session, *, actor: User, request_id: str, payload,
                   http_request_id: str | None = None) -> dict:
    return IntakeService(db).update_request(
        actor=actor, request_id=request_id, payload=payload, http_request_id=http_request_id,
    )


def add_document(db: Session, *, actor: User, request_id: str, filename: str,
                 mime_type: str, content: bytes) -> dict:
    return IntakeService(db).add_document(
        actor=actor, request_id=request_id, filename=filename, mime_type=mime_type, content=content,
    )


def list_documents(db: Session, *, actor: User, request_id: str) -> list[dict]:
    return IntakeService(db).list_documents(actor=actor, request_id=request_id)
