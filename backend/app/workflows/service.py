"""The workflow engine: select a flow for a ticket, then walk its steps.

One executor (`advance_run`) is the single brain. Each step type is a thin
adapter to a subsystem that already exists (drafting, AI skills, approval ladder,
signatures). The subsystem chain auto-cascades (approval-complete -> SIGNATURE,
signature-complete -> ACTIVE -> obligations), so waiting steps resume by
detecting the contract's stage change (`refresh_run`).
"""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.schemas import confidence_score
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.core.rbac import has_permission
from app.intake.models import IntakeRequest, IntakeTeam
from app.workflows.models import STEP_TYPES, Workflow, WorkflowRun, WorkflowStepRun

logger = logging.getLogger(__name__)

# Contract lifecycle order — walk one edge at a time to respect the engine.
_STAGE_ORDER = ["intake", "drafting", "review", "approval", "signature", "active", "closed"]

def approval_target(db: Session, *, org_id: str, cfg: dict, request: IntakeRequest | None) -> dict:
    """Who an Approval step asks. The step itself decides — there are no routing
    rules or lookup tables. "Specific person" / "The requester" name one user;
    otherwise the step's team (``config.team_id``, from Admin → Teams), where
    "All members must approve" makes every member's approval required. No team
    and no person: nobody, and submitting says the step has no approver.

    The designer saves the person as ``assignee_user_id``; approvals used to read
    only ``approver_user_id``, so a named approver was silently ignored."""
    assign_by = (cfg.get("assign_by") or "").lower()
    person = cfg.get("assignee_user_id") or cfg.get("approver_user_id")
    if "requester" in assign_by and request is not None:
        person = request.requester_user_id
    if person and ("specific" in assign_by or "requester" in assign_by or cfg.get("approver_user_id")):
        return {"approver_user_id": person, "approver_team_id": None, "approver_role": None, "mode": "any"}
    team = cfg.get("team_id")
    if team:
        found = db.get(IntakeTeam, team)
        team = found.id if found is not None and found.org_id == org_id else None
    return {"approver_user_id": None, "approver_team_id": team, "approver_role": None,
            "mode": "all" if "all members" in assign_by else "any"}


def planned_approvals(db: Session, *, org_id: str, steps: list, request: IntakeRequest) -> list[dict]:
    """The Approval steps a request will actually run — its skip rules and
    "run only when" conditions applied, as the engine applies them — each with
    the approver it will ask. Used to preview approvals before they start."""
    return [
        {"step_name": st.get("name") or "Approval",
         **approval_target(db, org_id=org_id, cfg=st.get("config") or {}, request=request)}
        for st in steps
        if (st or {}).get("type") == "approval" and _skip_reason(st, request) is None
    ]


# Registered AI agents for ai_task steps: maps the ported library's agent keys
# (github.com/Letscode82/aegis) AND our own ids/short names onto an intake
# specialist agent id (app.intake.agents). Running the agent yields a real
# confidence that drives the escalate-below-confidence gate.
_AGENT_KEY_MAP = {
    "nda-agent": "nda_agent", "nda_agent": "nda_agent", "nda": "nda_agent",
    "contract-review-agent": "contract_review_agent", "contract_review_agent": "contract_review_agent",
    "contract review": "contract_review_agent", "msa": "contract_review_agent",
    "litigation-agent": "litigation_agent", "litigation_agent": "litigation_agent", "litigation": "litigation_agent",
    "notice-mgmt-agent": "litigation_agent",  # deadline/notice extraction — closest registered agent
    "vendor-intake-agent": "vendor_agent", "vendor_agent": "vendor_agent", "vendor": "vendor_agent",
    "privacy-assessment-agent": "privacy_agent", "privacy_agent": "privacy_agent", "dpa": "privacy_agent",
    "trademark-agent": "trademark_agent", "trademark_agent": "trademark_agent", "trademark": "trademark_agent",
    "faq-agent": "faq_agent", "faq_agent": "faq_agent", "policy_answer": "faq_agent", "policy/faq": "faq_agent",
}


# --------------------------------------------------------------------------- selection

def _type_token(request: IntakeRequest) -> str:
    """A lowercase token describing the request kind, for criteria matching."""
    from app.intake.drafting import resolve_doc_type

    dt = resolve_doc_type(request)  # nda|msa|dpa|vendor|None
    if dt:
        return dt.lower()
    return (request.type_label or "").lower()


def _matches(criteria: dict | None, request: IntakeRequest) -> bool:
    c = criteria or {}
    mt = (c.get("match_type") or "").strip().lower()
    if mt:
        hay = " ".join([_type_token(request), (request.type_label or ""), (request.description or "")]).lower()
        # Word-boundary, not plain substring: "nda" as a bare `in` check also
        # matches inside "ANDA" (Abbreviated New Drug Application), a real
        # patent-litigation term — that misrouted genuine ANDA/Para IV matters
        # into the NDA Fast-Track flow.
        if not re.search(rf"\b{re.escape(mt)}\b", hay):
            return False
    mp = (c.get("match_priority") or "").strip().lower()
    if mp and (request.priority or "").lower() != mp:
        return False
    md = (c.get("match_department") or "").strip().lower()
    if md and (request.department or "").lower() != md:
        return False
    mk = (c.get("match_keyword") or "").strip().lower()
    if mk and not re.search(rf"\b{re.escape(mk)}\b", f"{request.type_label or ''} {request.description or ''}".lower()):
        return False
    # Field condition {field, op, value} against the request's structured answers.
    # For flow selection, a condition that can't be evaluated doesn't match.
    return not c.get("field") or _field_condition(c, request) is True


_CONDITION_OPS = ("eq", "ne", "lt", "lte", "gt", "gte")
_TRUE_WORDS = {"true", "yes", "y"}
_FALSE_WORDS = {"false", "no", "n"}


def _typed(value):
    """Normalize a condition operand: booleans and yes/no words to bool, numbers
    (including "$25,000") to float, other text to lowercase."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        try:
            return float(text.replace(",", "").replace("$", ""))
        except ValueError:
            return text
    return value


def _field_condition(cond: dict, request) -> bool | None:
    """Evaluate {field, op, value} against the request's answers. None means it
    can't be evaluated (unknown operator, unanswered field, or operands of
    different kinds); each caller decides what that means."""
    field = (cond.get("field") or "").strip()
    op = (cond.get("op") or "eq").strip().lower()
    if not field or op not in _CONDITION_OPS or request is None:
        return None
    raw = (request.field_values or {}).get(field)
    if raw is None and field == "contract_value":
        from app.intake.approval_bridge import IntakeApprovalSubject

        raw = IntakeApprovalSubject(request, type_key=None).value_amount  # value / amount / deal_value …
    if raw is None or raw == "":
        return None
    have, want = _typed(raw), _typed(cond.get("value"))
    if type(have) is not type(want):
        return None
    if op == "eq":
        return have == want
    if op == "ne":
        return have != want
    if not isinstance(have, float):
        return None
    return {"lt": have < want, "lte": have <= want, "gt": have > want, "gte": have >= want}[op]


def _condition_holds(criteria: dict, request) -> bool | None:
    """Step rules use the flow-selection shape; the field part is tri-state."""
    if request is None:
        return None
    other = {k: v for k, v in criteria.items() if k not in ("field", "op", "value")}
    if other and not _matches(other, request):
        return False
    return _field_condition(criteria, request) if criteria.get("field") else True


def _skip_reason(step: dict, request) -> str | None:
    """Why this step should be skipped for this request, or None to run it. A rule
    that can't be evaluated never skips: running an extra review or approval is
    safe, silently skipping one is not."""
    cfg = step.get("config") or {}
    # skip_if is the key the ported library stored (and flows seeded before this fix still hold).
    skip = cfg.get("skip_when") or cfg.get("skip_if") or {}
    if skip and _condition_holds(skip, request) is True:
        return "Skipped by rule"
    cond = step.get("cond") or cfg.get("cond") or {}
    if cond and _condition_holds(cond, request) is False:
        return "Condition not met"
    return None


def _validate_condition(cond: dict, step_name: str) -> None:
    op = (cond.get("op") or "eq").strip().lower()
    if op not in _CONDITION_OPS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Step '{step_name}': unsupported condition operator '{op}'. Use one of: {', '.join(_CONDITION_OPS)}.",
        )
    if op in ("lt", "lte", "gt", "gte") and not isinstance(_typed(cond.get("value")), float):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Step '{step_name}': '{op}' needs a number to compare against.",
        )


def _used_for(criteria: dict | None) -> list[tuple[str, str]]:
    """The workflow's "Used for" entries as (form key, agreement_type) —
    agreement_type lowercased, "" meaning any agreement type on that form."""
    out = []
    for e in (criteria or {}).get("used_for") or []:
        if isinstance(e, dict) and e.get("form"):
            out.append((str(e["form"]), str(e.get("agreement_type") or "").strip().lower()))
    return out


def _used_for_rank(criteria: dict | None, request: IntakeRequest) -> int | None:
    """2 = this request's form AND its agreement-type answer, 1 = its form (any
    agreement type), None = not used for it."""
    fv = request.field_values or {}
    form_of_request = fv.get("request_form")
    if not form_of_request:
        return None
    answer = str(fv.get("agreement_type") or "").strip().lower()
    best = None
    for form, agreement in _used_for(criteria):
        if form != form_of_request:
            continue
        if not agreement:
            best = max(best or 0, 1)
        elif agreement == answer:
            best = 2
    return best


# "Chosen when" conditions on a workflow: {field, op, value[, value2][, currency]}.
CHOOSE_OPS = ("is", "is_not", "under", "at_least", "between", "within_days")
_AMOUNT_OPS = ("under", "at_least", "between")
# A date question: "Needed by is within 7 days" of the day the request is routed.
_DATE_OPS = ("within_days",)
# "value" means the request's money figure, whichever form asked it.
_VALUE_KEYS = ("value", "new_value", "renew_value")


def _answer(fv: dict, field: str):
    if field == "value":
        return next((fv[k] for k in _VALUE_KEYS if fv.get(k) not in (None, "")), None)
    return fv.get(field)


def conditions_hold(conditions: list | None, request: IntakeRequest) -> bool:
    """Every condition holds for this request. A question left blank never
    holds, so a request with no value can't slip into a low-value workflow; an
    amount in another currency than the condition's doesn't hold either."""
    fv = request.field_values or {}
    for c in conditions or []:
        have = _answer(fv, c.get("field", ""))
        if have in (None, "", []):
            return False
        op = c.get("op")
        if op in _DATE_OPS:
            from datetime import date

            try:
                days = (date.fromisoformat(str(have)[:10]) - utcnow().date()).days
                ok = days <= int(c["value"])  # a date already past is the most urgent of all
            except (TypeError, ValueError, KeyError):
                return False
        elif op in _AMOUNT_OPS:
            if (c.get("currency") or settings.default_currency) != (fv.get("currency") or settings.default_currency):
                return False
            try:
                amount, lo = float(str(have).replace(",", "")), float(c["value"])
            except (TypeError, ValueError, KeyError):
                return False
            ok = (amount < lo if op == "under" else amount >= lo if op == "at_least"
                  else lo <= amount < float(c.get("value2") or 0))
        else:
            picked = have if isinstance(have, list) else [have]
            ok = (c.get("value") in picked) == (op == "is")
        if not ok:
            return False
    return True


def used_for_clash(db: Session, *, org_id: str, criteria: dict | None, exclude_id: str | None = None) -> str | None:
    """Name of another enabled workflow with the same agreement type AND the
    same conditions — two such workflows could never be told apart. Different
    conditions are fine: that is how one type gets several workflows."""
    mine = (set(_used_for(criteria)), _conditions_key(criteria))
    if not mine[0]:
        return None
    for f in db.scalars(select(Workflow).where(Workflow.org_id == org_id, Workflow.enabled.is_(True))).all():
        if f.id != exclude_id and mine[0] & set(_used_for(f.criteria)) and _conditions_key(f.criteria) == mine[1]:
            return f.name
    return None


def _conditions_key(criteria: dict | None) -> str:
    import json

    return json.dumps(sorted((criteria or {}).get("conditions") or [], key=lambda c: json.dumps(c, sort_keys=True)),
                      sort_keys=True)


def _enabled_flows(db: Session, org_id: str) -> list[Workflow]:
    return db.scalars(
        select(Workflow).where(Workflow.org_id == org_id, Workflow.enabled.is_(True)).order_by(Workflow.eval_order.asc())
    ).all()


def _pick_used_for(flows: list[Workflow], request: IntakeRequest) -> Workflow | None:
    """Of the workflows for this request's type, the one whose conditions all
    hold. If several do: one for this kind of agreement beats one for the whole
    form, then the one with more conditions, then the lowest priority order."""
    ranked = []
    for f in flows:
        rank = _used_for_rank(f.criteria, request)
        conditions = (f.criteria or {}).get("conditions") or []
        if rank is not None and conditions_hold(conditions, request):
            ranked.append(((rank, len(conditions)), f))
    return max(ranked, key=lambda rf: rf[0])[1] if ranked else None  # max keeps the first: lowest eval_order


def flow_used_for(db: Session, *, request: IntakeRequest) -> Workflow | None:
    """The workflow set up for this request's type whose conditions it meets —
    an admin's explicit choice, which no guess (word match or AI) may override."""
    return _pick_used_for(_enabled_flows(db, request.org_id), request)


def select_flow(db: Session, *, request: IntakeRequest) -> Workflow | None:
    """The workflow for this request.

    A form request only ever gets a workflow set up for its agreement type
    whose conditions it meets; if none fits it gets none and waits for a person
    to pick (no word match, no catch-all — a request must never land on a
    workflow by accident). Requests without a form (email, chat) keep the older
    word criteria: first enabled untyped flow (lowest eval_order) whose words
    match, an empty criteria dict matching everything. A request for a document
    only picks a flow that drafts one, so a DPA to draft can't land in the
    privacy-incident flow because both say "DPA"."""
    from app.intake.drafting import resolve_doc_type

    flows = _enabled_flows(db, request.org_id)
    if (request.field_values or {}).get("request_form"):
        return _pick_used_for(flows, request)
    wants_document = resolve_doc_type(request) is not None
    for f in flows:
        if _used_for(f.criteria):
            continue  # typed workflows are only for their form's requests
        if wants_document and not any((st or {}).get("type") == "clm_draft" for st in (f.steps or [])):
            continue
        if _matches(f.criteria, request):
            return f
    return None


# --------------------------------------------------------------------------- run lifecycle

def cancel_runs_for_request(db: Session, *, request_id: str, org_id: str, actor_user_id: str | None) -> int:
    """A closed request's workflow stops: its active runs are cancelled, their open
    steps skipped, and the pending approvals they raised (on the request or its
    contract) cancelled, so nobody keeps being asked to decide. Returns the runs cancelled."""
    from app.approvals.models import ApprovalRequest
    from app.core.enums import ApprovalStatus

    runs = db.scalars(
        select(WorkflowRun).where(
            WorkflowRun.org_id == org_id,
            WorkflowRun.request_id == request_id,
            WorkflowRun.status.in_(("running", "waiting")),
        )
    ).all()
    if not runs:
        return 0
    for run in runs:
        run.status = "cancelled"
        for sr in _step_runs(db, run):
            if sr.status in ("pending", "running", "waiting_human", "waiting_job"):
                sr.status = "skipped"; sr.note = "Request closed — workflow cancelled."
    contract_ids = [run.contract_id for run in runs if run.contract_id]
    for appr in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.org_id == org_id,
            or_(ApprovalRequest.intake_request_id == request_id, ApprovalRequest.contract_id.in_(contract_ids)),
            ApprovalRequest.status.in_([ApprovalStatus.PENDING, ApprovalStatus.WAITING]),
        )
    ).all():
        appr.status = ApprovalStatus.CANCELLED
        appr.updated_by_user_id = actor_user_id
    write_timeline_event(
        db, org_id=org_id, resource_type="intake_request", resource_id=request_id,
        event_type="flow.cancelled", title="Workflow cancelled: the request was closed",
        actor_user_id=actor_user_id, details={"flow_run_ids": [run.id for run in runs]},
    )
    return len(runs)


def get_run_for_request(db: Session, *, request_id: str, org_id: str) -> WorkflowRun | None:
    return db.scalars(
        select(WorkflowRun).where(WorkflowRun.request_id == request_id, WorkflowRun.org_id == org_id)
        .order_by(WorkflowRun.created_at.desc())
    ).first()


def get_run_for_contract(db: Session, *, contract_id: str, org_id: str) -> WorkflowRun | None:
    """The governance ladder driving a drafted contract (a clm_draft step sets
    run.contract_id). Lets the contract page surface the same workflow the ticket
    shows, instead of only the contract's own lifecycle stage."""
    return db.scalars(
        select(WorkflowRun).where(WorkflowRun.contract_id == contract_id, WorkflowRun.org_id == org_id)
        .order_by(WorkflowRun.created_at.desc())
    ).first()


def _step_runs(db: Session, run: WorkflowRun) -> list[WorkflowStepRun]:
    return db.scalars(
        select(WorkflowStepRun).where(WorkflowStepRun.flow_run_id == run.id).order_by(WorkflowStepRun.idx.asc())
    ).all()


def _sr_at(db: Session, run: WorkflowRun, idx: int) -> WorkflowStepRun | None:
    return next((s for s in _step_runs(db, run) if s.idx == idx), None)


# --- parallel groups -------------------------------------------------------
# A step flagged ``parallel: true`` runs concurrently with the step(s) before
# it — consecutive parallel-flagged steps form one group ("all must respond").
# ``current_index`` always points at the FIRST step of the current group; the
# run advances past the whole group only when every step in it is terminal. A
# step with no parallel flag is a group of one, so sequential flows (which is
# every existing flow) behave exactly as before and need no migration.
def _group_end(steps: list, start: int) -> int:
    """Exclusive end index of the parallel group beginning at ``start``."""
    end = start + 1
    while end < len(steps) and (steps[end] or {}).get("parallel"):
        end += 1
    return end


def _group_done(db: Session, run: WorkflowRun, start: int, end: int) -> bool:
    srs = [s for s in _step_runs(db, run) if start <= s.idx < end]
    return len(srs) == (end - start) and all(
        s.status in ("done", "skipped", "failed") for s in srs
    )


async def start_flow(
    db: Session, *, actor, request: IntakeRequest, flow: Workflow | None = None, request_id: str | None = None
) -> WorkflowRun:
    """Pick (or use the given) flow, create the run + one step-run per step, then
    drive it to the first waiting step. Idempotent-ish: returns the existing run
    if one is already open for the request."""
    # Lock the request row first, so two starts at once can't both find no open run
    # and both create one. The lock is released when this transaction ends.
    db.execute(select(IntakeRequest.id).where(IntakeRequest.id == request.id).with_for_update())
    existing = get_run_for_request(db, request_id=request.id, org_id=request.org_id)
    if existing and existing.status in ("running", "waiting"):
        return existing

    flow = flow or select_flow(db, request=request)
    if flow is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No workflow matches this request.")
    if not flow.enabled:
        # A flow picked by id (manual start, Ask Aegis, auto-start) honours the same
        # switch that automatic selection does.
        raise HTTPException(status.HTTP_409_CONFLICT, "This workflow is disabled. Enable it or choose another one.")

    steps = list(flow.steps or [])
    run = WorkflowRun(
        id=str(uuid.uuid4()), org_id=request.org_id, created_by_user_id=actor.id,
        request_id=request.id, flow_id=flow.id, flow_name=flow.name, flow_version=flow.version,
        steps=steps, status="running", current_index=0, contract_id=request.contract_id, context={},
    )
    db.add(run)
    for i, step in enumerate(steps):
        db.add(WorkflowStepRun(
            id=str(uuid.uuid4()), org_id=request.org_id, flow_run_id=run.id, idx=i,
            step_type=step.get("type", "notify"), step_name=step.get("name") or step.get("type", "step"),
            status="pending",
        ))
    db.flush()
    write_timeline_event(
        db, org_id=request.org_id, resource_type="intake_request", resource_id=request.id,
        event_type="flow.started", title=f"Workflow started: {flow.name}", actor_user_id=actor.id,
        details={"flow_id": flow.id, "flow_run_id": run.id, "steps": len(steps)},
    )
    write_audit_log(
        db, action="workflow.started", resource_type="workflow_run", resource_id=run.id,
        org_id=request.org_id, actor_user_id=actor.id, request_id=request_id,
        after={"flow_id": flow.id, "flow_name": flow.name, "flow_version": flow.version,
               "intake_request_id": request.id},
    )
    return await advance_run(db, run=run, actor=actor)


async def start_flow_for_contract(
    db: Session, *, actor, contract, flow: Workflow | None = None, request_id: str | None = None
) -> WorkflowRun:
    """Every contract gets a workflow — including one uploaded straight into the CLM,
    which has no request. File one for it, then start the workflow on it with the
    contract attached (its draft step is skipped: the document exists)."""
    from app.intake.service import file_request_for_contract

    run = get_run_for_contract(db, contract_id=contract.id, org_id=contract.org_id)
    if run is not None and run.status in ("running", "waiting"):
        return run
    request = file_request_for_contract(db, actor=actor, contract=contract)
    return await start_flow(db, actor=actor, request=request, flow=flow, request_id=request_id)


def _record_step_finding(run: WorkflowRun, step: dict, sr: WorkflowStepRun | None) -> None:
    """Aggregate each completed step's result into a run-level BRIEF — the shared
    "what the agents have established" trace that flows across the whole workflow.
    This is the hand-off substrate: the review step's finding, the drafting
    step's contract, each approval — all on one board later steps and the UI read,
    instead of each step's output being buried on its own row."""
    if sr is None or sr.status != "done":
        return
    res = sr.result if isinstance(sr.result, dict) else {}
    stype = step.get("type") or sr.step_type
    name = step.get("name") or sr.step_name or stype or "step"
    if res.get("risk_band"):
        summary = f"Risk {res['risk_band']}" + (
            f" ({res.get('risk_score')})" if res.get("risk_score") is not None else ""
        ) + (f" — {res['summary']}" if res.get("summary") else "")
    elif res.get("matter_type"):
        summary = str(res["matter_type"]) + (" · legal hold required" if res.get("legal_hold_required") else "")
    elif stype == "clm_draft" and res.get("contract_id"):
        summary = "Drafted the contract."
    elif res.get("summary"):
        summary = str(res["summary"])
    elif res.get("category"):
        summary = f"Classified as {res['category']}."
    else:
        return  # notify / plain human step — nothing substantive to record
    ctx = dict(run.context or {})
    items = list(ctx.get("brief") or [])
    items.append(
        {
            "step": name,
            "type": stype,
            "agent": res.get("agent") or stype,
            "summary": summary[:400],
            "confidence": res.get("confidence"),
        }
    )
    ctx["brief"] = items[-40:]
    run.context = ctx  # reassign so the JSON column change is detected


def _apply_change_request(db: Session, *, run: WorkflowRun, actor) -> None:
    """A finished amendment / renewal / termination / novation changes its contract."""
    from app.intake.drafting import apply_change_request

    request = db.get(IntakeRequest, run.request_id) if run.request_id else None
    if request is not None:
        apply_change_request(db, request=request, actor_id=actor.id if actor else None)


async def advance_run(db: Session, *, run: WorkflowRun, actor) -> WorkflowRun:
    """Walk steps until one must wait, the flow completes, or a step fails."""
    guard = 0
    while run.status == "running":
        guard += 1
        if guard > 100:
            run.status = "failed"; run.error = "step loop guard tripped"; break
        steps = list(run.steps or [])
        if run.current_index >= len(steps):
            run.status = "complete"
            write_timeline_event(
                db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
                event_type="flow.completed", title=f"Workflow complete: {run.flow_name}", actor_user_id=actor.id,
                details={"flow_run_id": run.id},
            )
            _finalize_intake_if_approved(db, run=run, actor=actor)
            _apply_change_request(db, run=run, actor=actor)
            break
        # Execute every not-yet-terminal step in the current parallel group (a
        # size-1 group is a plain sequential step). The group holds the run until
        # all its steps are done; a single waiting step parks the whole run.
        start = run.current_index
        end = _group_end(steps, start)
        any_wait = False
        current = start
        try:
            for gi in range(start, end):
                current = gi
                sr = _sr_at(db, run, gi)
                if sr and sr.status in ("done", "skipped", "failed"):
                    continue
                if sr and sr.status in ("waiting_human", "waiting_job"):
                    any_wait = True  # already executed, waiting on a person or subsystem: never re-run it
                    continue
                outcome = await _execute_step(db, run=run, step=steps[gi], sr=sr, actor=actor)
                if outcome == "advance":
                    if sr and sr.status not in ("done", "skipped"):
                        sr.status = "done"
                    _record_step_finding(run, steps[gi], sr)
                elif outcome == "wait":
                    any_wait = True
                    if sr and sr.status == "waiting_human" and steps[gi].get("type") == "ai_task" and not sr.assignee_user_id:
                        _assign_escalation(db, run=run, sr=sr, cfg=steps[gi].get("config") or {})
                # "yield": step is mid-beat (an ai_task animating its running
                # state); leave it running — a refresh tick resumes it.
        except HTTPException:
            raise
        except Exception as exc:  # a broken step must not wedge the whole run
            if isinstance(exc, SQLAlchemyError):
                # A failed statement leaves the transaction unusable: roll it back
                # and record the failure in a fresh one.
                db.rollback()
            sr = _sr_at(db, run, current)  # the step that raised, not the group's first
            if sr:
                sr.status = "failed"; sr.note = str(exc)[:500]
            run.status = "failed"; run.error = str(exc)[:500]
            break
        if _group_done(db, run, start, end):
            run.current_index = end
            continue
        if any_wait:
            run.status = "waiting"
        # else only "yield" steps remain running; stop and let the next refresh
        # tick pump them (the open ticket, or the scheduled resume_workflow_runs task).
        break
    db.commit()
    db.refresh(run)
    return run


def _finalize_intake_if_approved(db: Session, *, run: WorkflowRun, actor) -> None:
    """A no-contract intake flow that cleared at least one approval gate is
    finally approved when it completes — the flow owns this terminal transition
    (the ladder's on_complete only records each gate; see AL5). Contract flows
    finalize through the contract lifecycle instead, so skip those. Idempotent."""
    if run.contract_id:
        return
    # M2: only finalize when an approval step actually RAN and cleared. Checking
    # the flow *definition* for an approval step (the old behavior) let a
    # `skip_when` rule that skips the approval step auto-approve the request — but
    # a skipped step asked nobody, so that would approve with zero sign-off.
    # Require a real, cleared (or no-rungs-needed) approval step-run.
    approval_srs = [s for s in _step_runs(db, run) if s.step_type == "approval"]
    if not approval_srs:
        return
    if not any(s.status in ("done", "complete") for s in approval_srs):
        logger.warning(
            "intake flow completed with its approval step skipped/unresolved — NOT "
            "auto-approving (a skip_when rule likely skipped the approval)",
            extra={"flow_run_id": run.id, "request_id": run.request_id},
        )
        return
    from app.intake.approval_bridge import build_intake_subject

    subject = build_intake_subject(db, run.request_id, org_id=run.org_id)
    subject.finalize_approved(db, actor_user_id=actor.id if actor else None)


def _ai_agent_failed(sr: WorkflowStepRun | None, exc: Exception, label: str) -> str:
    """The single place every ai_task branch's failure handling converges. A
    genuine agent-call error must stop for a human, not disappear into a plain
    'Done' — the step previously kept `conf = None` and fell through to
    `return "advance"`, which `advance_run` then marks done since it "wasn't
    already done/skipped." Escalating to waiting_human reuses the exact same
    unblock path as a low-confidence escalation (complete_human_step doesn't
    care which step type it's completing), so this needs no new UI."""
    if sr:
        sr.status = "waiting_human"
        sr.note = f"{label} failed: {str(exc)[:180]} — needs manual review"
    return "wait"


def _team_name(db: Session, team_id: str | None) -> str | None:
    team = db.get(IntakeTeam, team_id) if team_id else None
    return team.name if team else None


def _team_head(db: Session, team_id: str) -> str | None:
    """No explicit lead field on a team, so "the head" is its earliest active
    member — a stable, deterministic stand-in for a designated lead."""
    from app.intake.models import IntakeTeamMember

    m = db.scalars(
        select(IntakeTeamMember)
        .where(IntakeTeamMember.team_id == team_id, IntakeTeamMember.active.is_(True))
        .order_by(IntakeTeamMember.created_at.asc(), IntakeTeamMember.id.asc())
    ).first()
    return m.user_id if m else None


def _assign_step(db: Session, *, run: WorkflowRun, sr: WorkflowStepRun, cfg: dict) -> None:
    """Route a human step per its "pick the person by" strategy (config.assign_by):
    the requester, the team head, a specific named person, or — the default —
    the step's team (``config.team_id``) shares it by its own rule (fewest open
    items / taking turns). Always falls back to the request's owner so a step is
    never assigned to nobody."""
    from app.intake.teams import pick_from_pool

    assign_by = (cfg.get("assign_by") or "").lower()
    user_id = cfg.get("assignee_user_id")
    team_id = cfg.get("team_id")
    if team_id:
        team = db.get(IntakeTeam, team_id)
        team_id = team.id if team is not None and team.org_id == run.org_id else None
    req = db.get(IntakeRequest, run.request_id)

    # Honor the "pick the person by" strategy before the default balancer.
    if not user_id:
        if "requester" in assign_by:
            user_id = req.requester_user_id if req else None
        elif "head" in assign_by and team_id:
            user_id = _team_head(db, team_id)
        # "specific" relies on config.assignee_user_id (handled above); if it
        # wasn't set, fall through to the balancer rather than nobody.

    # Default / "least-loaded" — the team balancer.
    if not user_id and team_id:
        try:
            pick = pick_from_pool(db, team_id=team_id)
            if pick:
                user_id = pick.user_id
        except Exception:  # a balancer hiccup must not wedge the step
            logger.warning("flow %s: team pick failed for team %s", run.flow_name, team_id, exc_info=True)
    if not user_id:
        user_id = req.assigned_to_user_id if req else None
    sr.assignee_user_id = user_id
    sr.team_id = team_id
    if assign_by:
        sr.result = {**(sr.result or {}), "assign_by": cfg.get("assign_by")}

    # Notify every active member of the resolved team, once. _execute_step can
    # be re-entered for a step already sitting in waiting_human (poll-driven
    # resume), so guard on a marker in sr.result rather than dispatching on
    # every call.
    #
    # Dispatch note (same race as app.contracts.stage_triggers): the caller
    # owns this transaction and hasn't committed yet, so a plain .delay() lets
    # the worker's own SessionLocal() read this step_run row before team_id
    # is actually visible — it sees no team and silently sends nothing. Use
    # the same short countdown fix as stage_triggers.fire_stage_entry_triggers
    # to let the caller's commit land before the worker reads the row.
    if team_id and not (sr.result or {}).get("notified_at"):
        from app.jobs.tasks import notify_workflow_step_team

        notify_workflow_step_team.apply_async(args=[sr.id], countdown=4)
        sr.result = {**(sr.result or {}), "notified_at": utcnow().isoformat()}


def _assign_escalation(db: Session, *, run: WorkflowRun, sr: WorkflowStepRun, cfg: dict) -> None:
    """An AI step that stopped for a human lands in a real person's queue: someone on
    the step's team (else the request's owner), who is told about it."""
    from app.notifications.models import Notification

    _assign_step(db, run=run, sr=sr, cfg={"team_id": cfg.get("team_id")})
    if sr.assignee_user_id:
        db.add(Notification(
            org_id=run.org_id, user_id=sr.assignee_user_id, channel="in_app", event_type="workflow.step_escalated",
            subject=f"Needs your review: {sr.step_name}",
            body=sr.note or f'"{sr.step_name}" in {run.flow_name} was escalated to you.', status="sent",
        ))


def _commit_before_slow_work(db: Session) -> None:
    """Commit what this request has written so far before an AI call or a draft.

    write_audit_log holds an app-wide advisory lock until the transaction ends,
    and starting or advancing a run has already written audit rows. Holding that
    across a model call froze every writer in the app for as long as the call
    took (2026-09-29: repeated "Start workflow" clicks on requests whose first
    step is an AI review queued behind one another). The run's progress up to
    here is complete and safe to persist."""
    db.commit()


async def _execute_step(db: Session, *, run: WorkflowRun, step: dict, sr: WorkflowStepRun | None, actor) -> str:
    """Returns 'advance' | 'wait'. Each branch is a thin call into an existing
    subsystem."""
    t = step.get("type", "notify")
    cfg = step.get("config") or {}

    # Per-step skip rule and run-only-when condition (the designer's "only when X").
    if cfg.get("skip_when") or cfg.get("skip_if") or step.get("cond") or cfg.get("cond"):
        reason = _skip_reason(step, db.get(IntakeRequest, run.request_id))
        if reason:
            if sr:
                sr.status = "skipped"; sr.note = reason
            return "advance"

    _enter_step_stage(db, run=run, step=step, actor=actor)

    if t == "notify":
        write_timeline_event(
            db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
            event_type="flow.notify", title=cfg.get("message") or step.get("name") or "Notification",
            actor_user_id=actor.id, details={"flow_run_id": run.id},
        )
        return "advance"

    if t == "human_task":
        if sr:
            sr.status = "waiting_human"
            _assign_step(db, run=run, sr=sr, cfg=cfg)
        return "wait"

    if t == "ai_task":
        # Two-phase so the agent's work is *observable*: on first encounter mark
        # the step running and yield, so the UI shows a live "Agent is working…"
        # beat rather than the step flashing straight to done. The agent actually
        # runs on the next tick (refresh/advance), when the step is already
        # "running". The classifier is near-instant, so the beat only reads as
        # real because the work is genuinely still pending during it.
        if sr is not None and sr.status != "running":
            sr.status = "running"
            return "yield"
        _commit_before_slow_work(db)
        # Run the configured agent best-effort. If it's a registered intake
        # agent (incl. the ported library's agent keys), run the deterministic
        # classifier for a real confidence; otherwise try it as an AI skill.
        # When confidence is below the step threshold, escalate to a human.
        agent_val = (cfg.get("agent") or cfg.get("skill") or "").strip()
        mapped = _AGENT_KEY_MAP.get(agent_val.lower())
        conf = None
        ran_analysis = True  # False when only the keyword classifier ran
        if mapped == "contract_review_agent" and run.contract_id:
            # The weighted risk score (contracts/risk.py) is genuinely real — but
            # it depends on clause extraction having already run, and both that
            # and the risk assessment itself happen in a background job after
            # drafting, so it isn't ready the instant this step starts. Keep
            # yielding (bounded) rather than settle for a regex guess the moment
            # we're asked; only fall back once the job clearly isn't landing in
            # a reasonable time — that's still better than staying stuck forever.
            from app.core.database import utcnow

            try:
                contract = _get_contract(db, run.contract_id)
                if contract.risk_score is not None:
                    band = contract.risk_band or "medium"
                    # Confidence here means "safe to auto-advance without a human
                    # pausing on this step" — inversely related to risk, not a
                    # restatement of it.
                    conf = {"low": 0.95, "medium": 0.65, "high": 0.3}.get(band, 0.5)
                    if sr:
                        sr.result = {
                            "agent": mapped, "source": "ai", "confidence": conf,
                            "risk_score": contract.risk_score, "risk_band": band,
                            "summary": (contract.risk_summary or {}).get("summary"),
                            "top_drivers": (contract.risk_summary or {}).get("drivers", [])[:3],
                        }
                elif sr and sr.updated_at and (utcnow() - sr.updated_at).total_seconds() < 60:
                    return "yield"  # still waiting on the background clause/risk job
                else:
                    from app.intake import agents as intake_agents
                    request = db.get(IntakeRequest, run.request_id)
                    cls = intake_agents.classify(request.type_label or "", request.description or "")
                    conf = cls.get("confidence")
                    ran_analysis = False  # a keyword match on the ticket text is not a review
                    if sr:
                        sr.result = {
                            "agent": mapped, "category": cls.get("category"), "confidence": conf,
                            "source": cls.get("source", "regex"),
                            "note": "risk score not ready in time — used the fallback classifier",
                        }
            except Exception as exc:
                return _ai_agent_failed(sr, exc, "Contract review agent")
        elif mapped:
            try:
                from app.intake import agents as intake_agents
                request = db.get(IntakeRequest, run.request_id)
                # (confidence, result) once a domain has a real, already-computed
                # result to reuse — never re-derive a worse one from scratch.
                real: tuple[float | None, dict] | None = None
                if mapped == "litigation_agent":
                    # A real Claude-backed litigation assessment (matter type,
                    # statutory deadlines, hold/counsel signals) already ran at
                    # intake time (intake/service.py:_compute_intake_analysis)
                    # and is sitting on the request — use it instead of paying
                    # for (and settling for) another regex pass here.
                    assessment = (request.ai_triage or {}).get("litigation_assessment")
                    if assessment:
                        fs = (request.ai_triage or {}).get("flow_suggestion") or {}
                        # assessment_confidence measures trust in the extracted FACTS
                        # (matter type, deadlines, hold/counsel calls) given how much
                        # detail the request actually provided — a distinct judgment
                        # from flow_suggestion.confidence, which only measures "which
                        # workflow bucket fits." A live scorecard proved these can
                        # diverge: a genuine model call was fully confident about the
                        # bucket on a deliberately vague ticket while the facts behind
                        # it were thin — only fact confidence tells us whether a human
                        # needs to double-check this specific assessment. Still only
                        # trust the raw number when a real model call produced it;
                        # the heuristic fallback (source != "llm") gets capped low
                        # regardless, since litigation_agent._heuristic's facts are
                        # keyword-guessed and defaulted, not genuine extraction.
                        ac = assessment.get("assessment_confidence")
                        conf = ac if (isinstance(ac, (int, float)) and fs.get("source") == "llm") else min(ac or 0.0, 0.3)
                        real = (conf, {
                            "agent": mapped, "source": fs.get("source", "unknown"),
                            "confidence": conf,
                            "matter_type": assessment.get("matter_type"),
                            "statutory_deadlines": assessment.get("statutory_deadlines"),
                            "legal_hold_required": assessment.get("legal_hold_required"),
                            "outside_counsel_likely": assessment.get("outside_counsel_likely"),
                            "summary": assessment.get("summary"),
                        })
                elif mapped == "privacy_agent":
                    # Unlike litigation/vendor, there's no pre-computed signal to
                    # reuse here — this domain gets a genuine, purpose-built skill
                    # call. No inner try/except: a failure must propagate to the
                    # shared except below and escalate to a human, not quietly
                    # degrade to a regex category guess with no privacy judgment
                    # behind it at all.
                    from app.ai.controller import ai_controller
                    out = await ai_controller.run_structured_skill(
                        db, skill_name="privacy_incident_assessment", org_id=run.org_id,
                        created_by_user_id=actor.id,
                        input_payload={
                            "type_label": request.type_label, "priority": request.priority,
                            "description": request.description, "field_values": request.field_values,
                        },
                        resource_type="intake_request", resource_id=request.id,
                    )
                    real = (out.confidence, {
                        "agent": mapped, "source": "ai", "confidence": out.confidence,
                        "severity": out.severity, "notification_required": out.notification_required,
                        "notification_deadline_hours": out.notification_deadline_hours,
                        "affected_data_categories": out.affected_data_categories,
                        "estimated_affected_count": out.estimated_affected_count,
                        "recommended_immediate_actions": out.recommended_immediate_actions,
                        "rationale": out.rationale,
                    })
                if real:
                    conf, result = real
                    if sr:
                        sr.result = result
                else:
                    # No real data on this request (e.g. it reached this flow
                    # without the matching intake-time enrichment ever running) —
                    # fall back to the regex classifier rather than stall.
                    cls = intake_agents.classify(request.type_label or "", request.description or "")
                    conf = cls.get("confidence")
                    ran_analysis = False  # a keyword match on the ticket text is not a review
                    if sr:
                        sr.result = {"agent": mapped, "category": cls.get("category"),
                                     "confidence": conf, "source": cls.get("source", "regex")}
            except Exception as exc:
                return _ai_agent_failed(sr, exc, "Agent")
        elif agent_val:
            try:
                from app.ai.controller import ai_controller
                out = await ai_controller.run_structured_skill(
                    db, skill_name=agent_val, org_id=run.org_id, created_by_user_id=actor.id,
                    input_payload={"request_id": run.request_id, "contract_id": run.contract_id},
                )
                data = out.model_dump() if hasattr(out, "model_dump") else {}
                conf = data.get("confidence") if isinstance(data, dict) else None
                if sr:
                    sr.result = data or {"ran": True}
            except Exception as exc:
                return _ai_agent_failed(sr, exc, "AI skill")
        if not ran_analysis:
            # The classifier's confidence answers "what kind of ticket is this?",
            # not "did the review pass?" -- never let it clear the step.
            if sr:
                sr.status = "waiting_human"
                who = _team_name(db, cfg.get("team_id")) or "a human"
                base = f"No analysis ran for this step (only a keyword match) -- escalated to {who}"
                detail = (sr.result or {}).get("note") if isinstance(sr.result, dict) else None
                sr.note = f"{base} ({detail})" if detail else base
            return "wait"
        thr = confidence_score(cfg.get("escalate_below_confidence"))
        conf = confidence_score(conf)
        if thr is not None and conf is not None and conf < thr:
            if sr:
                sr.status = "waiting_human"
                base = f"AI confidence {conf} < {thr} — escalated to {_team_name(db, cfg.get('team_id')) or 'a human'}"
                # A branch may have already left a more specific explanation in
                # sr.result (e.g. "risk score not ready in time" for the contract
                # review fallback) — surface it instead of only the generic number.
                detail = (sr.result or {}).get("note") if isinstance(sr.result, dict) else None
                sr.note = f"{base} ({detail})" if detail else base
            return "wait"
        return "advance"

    if t == "clm_draft" and run.contract_id:
        # The document already exists (uploaded, or drafted before a send-back):
        # nothing to draft.
        if sr:
            sr.status = "skipped"; sr.note = "The contract already exists — nothing to draft."
        return "advance"

    if t == "clm_draft":
        _commit_before_slow_work(db)
        # The requester's chosen path (from the intake form) decides HOW the
        # document is produced: fast-lane uses the standard template; "custom"
        # routes to an attorney to draft fresh (no template); "attach" waits for
        # the requester to upload their own NDA. Only fast-lane auto-drafts.
        req = db.get(IntakeRequest, run.request_id)
        path = str(((req.field_values or {}).get("draft_path")) or cfg.get("mode") or "fast_lane").lower()
        if path not in ("custom", "attach"):
            from app.intake.drafting import resolve_doc_type

            if resolve_doc_type(req) is None:
                # No standard template for this kind (a distribution or licence agreement,
                # say): draft it fresh instead of failing the flow on its first step.
                path = "custom"
        if path == "custom":
            # Create a real, editable draft SHELL now (captured details + an
            # attorney-fill canvas) and link it, so the contract is visible from
            # step one. The attorney writes the bespoke terms in the CLM editor,
            # then marks this step done.
            contract = await _draft_contract(db, run=run, mode="custom", actor=actor)
            run.contract_id = contract.id
            if sr:
                sr.status = "waiting_human"
                sr.note = "Draft the custom document in the editor, then mark this step done."
                sr.result = {"contract_id": contract.id}
                _assign_step(db, run=run, sr=sr, cfg=cfg)
            return "wait"
        if path == "attach":
            # The contract is created when the requester uploads their document
            # on the ticket (which links run.contract_id); until then, wait.
            if sr:
                sr.status = "waiting_human"
                sr.note = "Upload the document on the ticket — it becomes the contract."
            return "wait"
        contract = await _draft_contract(db, run=run, mode="template", actor=actor)
        run.contract_id = contract.id
        if sr:
            sr.result = {"contract_id": contract.id}
        return "advance"

    if t == "approval":
        req = db.get(IntakeRequest, run.request_id)
        target = approval_target(db, org_id=run.org_id, cfg=cfg, request=req)
        if not run.contract_id:
            # No contract yet (litigation / notice / regulatory / employment /
            # board matters): gate the intake REQUEST through the shared ladder
            # rather than silently skipping.
            from app.intake.approval_bridge import submit_request_for_approval

            reqs = await submit_request_for_approval(db, actor=actor, request=req, **target)
            # A step that names no approver may be handed to a condition-driven
            # chain (FR-22), which creates no ApprovalRequest rows — check for a
            # live chain instance before treating "no reqs" as "nothing to approve"
            # (the M2 auto-approve bug).
            from app.approval_chains import dispatch as chain_dispatch

            instance = chain_dispatch.live_instance_for(
                db, org_id=run.org_id, module="intake_request", module_record_id=req.id
            )
            if not reqs and instance is None:
                if sr:
                    sr.note = "No approval rungs required — skipped."
                return "advance"
            if sr:
                sr.status = "waiting_job"
                sr.result = (
                    {"chain_instance_id": instance.id} if instance is not None
                    else {"approval_ids": [r.id for r in reqs]}
                )
            return "wait"
        contract = _get_contract(db, run.contract_id)
        _advance_contract_to(db, contract=contract, target="approval", actor=actor, reason=f"workflow: {run.flow_name}")
        from app.approvals.service import submit_contract_for_approval
        await submit_contract_for_approval(
            db, user=actor, contract=contract, contract_version_id=None, **target,
        )
        if sr:
            sr.status = "waiting_job"
        return "wait"

    if t == "signature":
        if not run.contract_id:
            if sr:
                sr.note = "No contract to sign — skipped."
            return "advance"
        contract = _get_contract(db, run.contract_id)
        _advance_contract_to(db, contract=contract, target="signature", actor=actor, reason=f"workflow: {run.flow_name}")
        if sr:
            sr.status = "waiting_job"
            if (contract.lifecycle_stage or "").lower() == "approval":
                sr.note = "Signing starts once this version is fully approved (resubmit it if it changed after approval)."
        return "wait"

    if t == "counterparty":
        if sr:
            sr.status = "waiting_human"
            sr.note = "Send the contract to the counterparty."
            # Someone owns the negotiation; _assign_step also emails their team.
            _assign_step(db, run=run, sr=sr, cfg=cfg)
        return "wait"

    # unknown step type — record and skip rather than wedge
    if sr:
        sr.status = "skipped"; sr.note = f"Unknown step type: {t}"
    return "advance"


# --------------------------------------------------------------------------- resume + human actions

def _require_step_actor(db: Session, *, sr: WorkflowStepRun | None, actor) -> None:
    """Only the step's assignee, an active member of its team, or intake staff
    (intake:update) may complete or send back a step. The engine models who should
    act; this enforces it instead of trusting the UI to hide the button.
    Note: while RBAC is disabled (core/rbac.has_permission returns True for
    everyone) the intake:update override admits every user; the audit rows still
    record who acted."""
    if actor is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A signed-in user is required to act on this step")
    if sr is not None and sr.assignee_user_id and sr.assignee_user_id == actor.id:
        return
    if sr is not None and sr.team_id:
        from app.intake.models import IntakeTeamMember

        member = db.scalar(
            select(IntakeTeamMember.id).where(
                IntakeTeamMember.team_id == sr.team_id,
                IntakeTeamMember.user_id == actor.id,
                IntakeTeamMember.active.is_(True),
            )
        )
        if member:
            return
    if has_permission(actor.permission_values, "intake:update"):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Only this step's assignee or its team can act on it")


def complete_human_step(
    db: Session, *, run: WorkflowRun, actor, note: str | None = None, step_idx: int | None = None,
    request_id: str | None = None,
):
    # In a parallel group the human may resolve any of the concurrent steps, so
    # accept an explicit idx; default to the current step for sequential flows.
    idx = step_idx if step_idx is not None else run.current_index
    sr = _sr_at(db, run, idx)
    if sr and sr.status == "waiting_human":
        _require_step_actor(db, sr=sr, actor=actor)
        sr.status = "done"; sr.note = note or sr.note
        steps = list(run.steps or [])
        start = run.current_index
        end = _group_end(steps, start)
        # Advance past the whole group only once every concurrent step is done;
        # otherwise it stays in flight for its remaining steps.
        if _group_done(db, run, start, end):
            run.current_index = end
            run.status = "running"
        # Otherwise the group's other steps are still in flight: the run stays as it is,
        # so the executor doesn't re-enter them (and, say, reassign a parallel reviewer).
        write_audit_log(
            db, action="workflow.step_completed", resource_type="workflow_run", resource_id=run.id,
            org_id=run.org_id, actor_user_id=actor.id, request_id=request_id,
            after={"step_idx": idx, "step_name": sr.step_name, "note": note},
        )
        write_timeline_event(
            db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
            event_type="flow.step_completed", title=f"Step completed: {sr.step_name}",
            actor_user_id=actor.id, request_id=request_id, details={"flow_run_id": run.id, "idx": idx},
        )
        db.flush()
    return run


def _configured_return_to(steps: list, current: int) -> int:
    """Where a send-back from the current step lands, per the designer's
    `config.return_to`: a specific earlier step index, else the previous step."""
    cfg = (steps[current].get("config") if 0 <= current < len(steps) else None) or {}
    rt = cfg.get("return_to")
    target = rt if isinstance(rt, int) else current - 1
    return max(0, min(target, current - 1))


async def return_run(
    db: Session, *, run: WorkflowRun, actor, to_idx: int | None = None, note: str | None = None,
    request_id: str | None = None,
) -> WorkflowRun:
    """Send the workflow BACK to an earlier step for rework — the missing dynamic
    edge. Reopens every step from `to_idx` onward (their old results stay on the
    step rows as history), points the run there, and re-drives it. A returned
    ai/draft step re-runs; a returned human step waits again for its owner.

    `to_idx=None` uses the step's configured `config.return_to` target (set in the
    designer's Outcomes tab); an explicit `to_idx` overrides it."""
    if run.status in ("complete", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This workflow is finished — start a new one.")
    steps = list(run.steps or [])
    if to_idx is None:
        to_idx = _configured_return_to(steps, run.current_index)
    if to_idx < 0 or to_idx >= len(steps) or to_idx >= run.current_index:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Can only return to an earlier step.")
    _require_step_actor(db, sr=_sr_at(db, run, run.current_index), actor=actor)
    from_idx = run.current_index
    for sr in _step_runs(db, run):
        if sr.idx >= to_idx:
            sr.status = "pending"
    add_run_comment(db, run=run, actor=actor, idx=to_idx, text=note or "Returned for rework", kind="return")
    run.current_index = to_idx
    run.status = "running"
    run.error = None
    rework = _rewind_contract_for_rework(
        db, run=run, steps=steps, from_idx=from_idx, to_idx=to_idx, actor=actor, note=note
    )
    write_timeline_event(
        db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
        event_type="flow.returned", title=f"Returned to “{steps[to_idx].get('name', 'step')}”",
        actor_user_id=actor.id if actor else None, details={"flow_run_id": run.id, "to_idx": to_idx, "note": note},
    )
    write_audit_log(
        db, action="workflow.step_returned", resource_type="workflow_run", resource_id=run.id,
        org_id=run.org_id, actor_user_id=actor.id, request_id=request_id,
        after={"from_idx": from_idx, "to_idx": to_idx, "note": note, **rework},
    )
    run = await advance_run(db, run=run, actor=actor)
    await _void_envelopes(rework["voided_envelopes"])  # external action, after the commit
    return run


def _rewind_contract_for_rework(
    db: Session, *, run: WorkflowRun, steps: list, from_idx: int, to_idx: int, actor, note: str | None
) -> dict:
    """Rework that re-opens an approval or signature step must take the contract
    back too, or the contract stays in Approval/Signature and every later step
    409s. Cancels pending approvals, marks in-flight envelopes voided, and moves
    the contract back to Review with the reason on its stage history."""
    rework = {"cancelled_approvals": 0, "voided_envelopes": []}
    reopened = {(steps[i] or {}).get("type") for i in range(to_idx, min(from_idx, len(steps) - 1) + 1)}
    if not run.contract_id or not reopened & {"approval", "signature"}:
        return rework
    contract = _get_contract(db, run.contract_id)
    stage = (contract.lifecycle_stage or "").lower()
    if stage in ("active", "closed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This contract is already signed, so the workflow can't be sent back past signature. "
            "Start an amendment instead.",
        )
    if stage not in ("approval", "signature"):
        return rework
    from app.approvals.models import ApprovalRequest
    from app.contracts.lifecycle import transition_contract_stage
    from app.core.enums import ApprovalStatus, SignatureStatus
    from app.signatures.models import SignatureRequest

    for appr in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.contract_id == contract.id,
            ApprovalRequest.status.in_([ApprovalStatus.PENDING, ApprovalStatus.WAITING]),
        )
    ).all():
        appr.status = ApprovalStatus.CANCELLED
        appr.updated_by_user_id = actor.id
        rework["cancelled_approvals"] += 1
    for sig in db.scalars(
        select(SignatureRequest).where(
            SignatureRequest.contract_id == contract.id,
            SignatureRequest.status.in_([SignatureStatus.DRAFT, SignatureStatus.SENT, SignatureStatus.DELIVERED]),
        )
    ).all():
        sig.status = SignatureStatus.VOIDED
        sig.updated_by_user_id = actor.id
        if sig.provider_envelope_id:
            rework["voided_envelopes"].append(sig.provider_envelope_id)
    transition_contract_stage(
        db, contract=contract, to_stage="review", actor_user_id=actor.id,
        reason=f"Workflow sent back for rework: {note or 'no reason given'}",
        override=True, override_authorized=True,
    )
    return rework


async def _void_envelopes(envelope_ids: list[str]) -> None:
    """Void envelopes that were out for signature when the workflow was sent back.
    Best-effort: the local record already says voided."""
    if not envelope_ids:
        return
    from app.integrations.docusign import docusign_client

    for envelope_id in envelope_ids:
        try:
            await docusign_client.void_envelope(envelope_id=envelope_id, reason="workflow_sent_back_for_rework")
        except Exception:
            logger.warning("could not void envelope %s after rework", envelope_id, exc_info=True)


def add_run_comment(db: Session, *, run: WorkflowRun, actor, text: str, idx: int | None = None, kind: str = "comment") -> WorkflowRun:
    """Append a comment / question to the run's thread (stored on run.context — no
    new table). idx ties it to a specific step; None is a run-level note."""
    text = (text or "").strip()
    if not text:
        return run
    ctx = dict(run.context or {})
    thread = list(ctx.get("comments") or [])
    thread.append({
        "idx": idx, "kind": kind, "text": text[:2000],
        "actor_id": actor.id if actor else None,
        "actor_name": (getattr(actor, "full_name", None) or getattr(actor, "email", None)) if actor else "system",
        "at": utcnow().isoformat(),
    })
    ctx["comments"] = thread
    run.context = ctx  # reassign so SQLAlchemy tracks the JSON change
    db.flush()
    return run


async def refresh_run(db: Session, *, run: WorkflowRun, actor) -> WorkflowRun:
    """Re-check a waiting approval/signature step against the contract's current
    stage (the subsystems auto-cascade), and advance if the phase completed.
    Also pumps a mid-beat 'running' step (an ai_task showing its working state)
    by resuming the executor — this is how the UI's animation gives way to the
    real agent run."""
    if run.status == "running":
        return await advance_run(db, run=run, actor=actor)
    if run.status != "waiting":
        return run
    steps = list(run.steps or [])
    if run.current_index >= len(steps):
        return run
    # An approval step handed to a condition-driven chain (FR-22) waits on one
    # ApprovalChainInstance, not ApprovalRequest rows: settle it here first.
    # _resolve_waiting_group then treats the step like any other finished one.
    from app.approval_chains.models import ApprovalChainInstance

    outcome = None
    end = _group_end(steps, run.current_index)
    for sr in _step_runs(db, run):
        cid = (sr.result or {}).get("chain_instance_id") if isinstance(sr.result, dict) else None
        if not cid or not run.current_index <= sr.idx < end or sr.status in ("done", "skipped", "failed"):
            continue
        inst = db.get(ApprovalChainInstance, cid)
        if inst is not None and inst.status == "approved":
            sr.status = "done"
        elif inst is not None and inst.status == "rejected":
            sr.status = "failed"
            sr.note = "Approval rejected."
            outcome = "failed"
    outcome = outcome or _resolve_waiting_group(db, run=run, steps=steps)
    if outcome == "failed":
        if _send_back_after_rejection(db, run=run, steps=steps, actor_user_id=actor.id if actor else None):
            return await advance_run(db, run=run, actor=actor)
        _fail_run_after_rejection(db, run=run, actor_user_id=actor.id if actor else None)
        db.commit()
        return run
    if outcome == "advance":
        run.status = "running"
        run.current_index = _group_end(steps, run.current_index)
        return await advance_run(db, run=run, actor=actor)
    if outcome == "rerun":
        run.status = "running"
        return await advance_run(db, run=run, actor=actor)
    return run


def _rework_target(db: Session, *, run: WorkflowRun, steps: list) -> int | None:
    """Where a rejected approval (or a signature pulled back) sends the workflow:
    the step's own "send back to" setting, else the last Review step before it
    that actually ran, else any earlier Review step, else Drafting. None when the
    workflow has no earlier Review/Drafting step to fix things in."""
    start = run.current_index
    failed = next((sr.idx for sr in _step_runs(db, run)
                   if start <= sr.idx < _group_end(steps, start) and sr.status == "failed"), start)
    rt = ((steps[failed] or {}).get("config") or {}).get("return_to")
    if isinstance(rt, int) and 0 <= rt < start:
        return rt
    ran = {sr.idx for sr in _step_runs(db, run) if sr.status in ("done", "complete")}
    earlier = range(start - 1, -1, -1)
    for want in (lambda j: _stage_of(steps, j) == "review" and j in ran,
                 lambda j: _stage_of(steps, j) == "review",
                 lambda j: _stage_of(steps, j) == "drafting"):
        j = next((j for j in earlier if want(j)), None)
        if j is not None:
            return j
    return None


def _stage_of(steps: list, j: int) -> str:
    return (steps[j] or {}).get("stage") or "review"


def _rejection_note(db: Session, *, run: WorkflowRun) -> str:
    """The approver's reason, from the latest rejection on this contract or request."""
    from app.approvals.models import ApprovalDecision, ApprovalRequest

    on = (ApprovalRequest.contract_id == run.contract_id if run.contract_id
          else ApprovalRequest.intake_request_id == run.request_id)
    d = db.scalars(
        select(ApprovalDecision).join(ApprovalRequest, ApprovalDecision.approval_request_id == ApprovalRequest.id)
        .where(on, ApprovalDecision.decision == "reject").order_by(ApprovalDecision.decided_at.desc())
    ).first()
    return d.comment.strip() if d is not None and d.comment and d.comment.strip() else "no reason given"


def _send_back_after_rejection(db: Session, *, run: WorkflowRun, steps: list, actor_user_id: str | None) -> bool:
    """A rejected approval sends the workflow back to Review to fix what the approver
    raised; the steps from there on run again, approval included. The contract is
    already back in Review (the approval / signature subsystem moved it). False
    when there is nowhere to send it — the caller then stops the run as before."""
    from app.auth.models import User

    to_idx = _rework_target(db, run=run, steps=steps)
    if to_idx is None:
        return False
    from_idx = run.current_index
    for sr in _step_runs(db, run):
        if sr.idx >= to_idx:
            sr.status = "pending"
    actor = db.get(User, actor_user_id) if actor_user_id else None
    note = f"Sent back to Review — {(steps[from_idx] or {}).get('name', 'approval')} was rejected: {_rejection_note(db, run=run)}"
    add_run_comment(db, run=run, actor=actor, idx=to_idx, text=note, kind="return")
    run.current_index = to_idx
    run.status = "running"
    run.error = None
    write_timeline_event(
        db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
        event_type="flow.returned", title=f"Rejected — back to “{steps[to_idx].get('name', 'step')}”",
        actor_user_id=actor_user_id, details={"flow_run_id": run.id, "to_idx": to_idx, "note": note},
    )
    write_audit_log(
        db, action="workflow.step_returned", resource_type="workflow_run", resource_id=run.id,
        org_id=run.org_id, actor_user_id=actor_user_id,
        after={"from_idx": from_idx, "to_idx": to_idx, "note": note, "cause": "rejected"},
    )
    return True


def _fail_run_after_rejection(db: Session, *, run: WorkflowRun, actor_user_id: str | None) -> None:
    run.status = "failed"
    run.error = (
        "An approval was rejected or the contract was pulled back. "
        "Send the workflow back for rework, or start a new one."
    )
    write_timeline_event(
        db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
        event_type="flow.failed", title=f"Workflow stopped: {run.flow_name}", actor_user_id=actor_user_id,
        details={"flow_run_id": run.id, "reason": "approval_rejected_or_pulled_back"},
    )


def _resolve_waiting_group(db: Session, *, run: WorkflowRun, steps: list) -> str:
    """Settle the current group's waiting approval/signature steps from the state
    of what they wait on. The one resolver both resume paths share. Returns
    "failed" (an approval was rejected or the contract pulled back), "advance"
    (the whole group is done), "rerun" (an AI step in the group is mid-run), or
    "wait"."""
    from app.approvals.models import ApprovalRequest
    from app.contracts.lifecycle import _current_version_approved
    from app.core.enums import ApprovalStatus

    start = run.current_index
    end = _group_end(steps, start)
    contract = _get_contract(db, run.contract_id) if run.contract_id else None
    stage = (contract.lifecycle_stage or "").lower() if contract is not None else None
    for gi in range(start, end):
        st = (steps[gi] or {}).get("type")
        sr = _sr_at(db, run, gi)
        if sr is None or sr.status in ("done", "skipped", "failed"):
            continue
        if st == "approval":
            ids = (sr.result or {}).get("approval_ids") if isinstance(sr.result, dict) else None
            if ids:  # an intake-request (no-contract) approval chain
                chain = db.scalars(select(ApprovalRequest).where(ApprovalRequest.id.in_(ids))).all()
                if chain and all(a.status == ApprovalStatus.APPROVED for a in chain):
                    sr.status = "done"
                elif any(a.status == ApprovalStatus.REJECTED for a in chain):
                    sr.status = "failed"; sr.note = "Approval rejected."
                    return "failed"
            elif stage in ("signature", "active", "closed") or (stage == "approval" and _current_version_approved(db, contract)):
                # Approved but not signing yet: the workflow's later steps (e.g. counterparty
                # negotiation, then its own signature step) move the contract on.
                sr.status = "done"
            elif stage in ("intake", "drafting", "review") and sr.status == "waiting_job":
                sr.status = "failed"; sr.note = "Approval rejected: the contract went back to Review."
                return "failed"
        elif st == "signature":
            if stage in ("active", "closed"):
                sr.status = "done"
            elif stage == "approval" and sr.status == "waiting_job" and _current_version_approved(db, contract):
                sr.status = "pending"  # re-approved after a change: run the step again so signing starts
            elif stage in ("intake", "drafting", "review") and sr.status == "waiting_job":
                sr.status = "failed"; sr.note = "The contract was pulled back from signature."
                return "failed"
    if _group_done(db, run, start, end):
        return "advance"
    if any(s.status in ("running", "pending") for s in _step_runs(db, run) if start <= s.idx < end):
        return "rerun"
    return "wait"


# --------------------------------------------------------------------------- auto-resume (sync)

def advance_flow_for_contract(db: Session, *, contract, actor_user_id: str | None) -> None:
    """Called from the contract stage-entry triggers and when an approval chain
    completes. Settles the waiting approval/signature group from the contract's
    state; when the flow can move on, marks the run running and hands it to the one
    step executor (advance_run, via the resume task), so every later step has its
    condition checked and its assignee picked. Best-effort — never raises.

    No commit here: this runs inside the caller's transaction (a stage transition
    or an approval decision), and committing would break that transaction's atomicity."""
    run = db.scalars(
        select(WorkflowRun).where(WorkflowRun.contract_id == contract.id, WorkflowRun.status == "waiting")
        .order_by(WorkflowRun.created_at.desc())
    ).first()
    if run is None:
        return
    steps = list(run.steps or [])
    if run.current_index >= len(steps):
        return
    outcome = _resolve_waiting_group(db, run=run, steps=steps)
    if outcome == "failed":
        if _send_back_after_rejection(db, run=run, steps=steps, actor_user_id=actor_user_id):
            _schedule_resume()
            return
        _fail_run_after_rejection(db, run=run, actor_user_id=actor_user_id)
        return
    if outcome == "wait":
        return
    if outcome == "advance":
        run.current_index = _group_end(steps, run.current_index)
    run.status = "running"
    _schedule_resume()


def _schedule_resume() -> None:
    """Run the step executor shortly, after the caller's commit. The scheduled
    resume_workflow_runs beat is the fallback if this can't be queued."""
    try:
        from app.jobs.tasks import resume_workflow_runs

        resume_workflow_runs.apply_async(countdown=5)
    except Exception:
        logger.warning("could not queue a workflow resume", exc_info=True)


def workflow_holds_signature(db: Session, *, contract_id: str) -> bool:
    """True when an active workflow drives this contract and still has its own
    signature step ahead. That step, not the approval engine, then moves the
    contract to signing, so the steps in between (counterparty negotiation, say)
    happen first, and anything they change must be approved again."""
    run = db.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.contract_id == contract_id, WorkflowRun.status.in_(("running", "waiting")))
        .order_by(WorkflowRun.created_at.desc())
    ).first()
    if run is None:
        return False
    return any((st or {}).get("type") == "signature" for st in list(run.steps or [])[run.current_index + 1:])


# --------------------------------------------------------------------------- contract helpers

def _get_contract(db: Session, contract_id: str):
    from app.contracts.models import Contract
    c = db.get(Contract, contract_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    return c


def _enter_step_stage(db: Session, *, run: WorkflowRun, step: dict, actor) -> None:
    """A Drafting or Review step starting brings its contract to that stage, so the
    contract's lifecycle follows the workflow. Approval and Signature steps move it
    in their own branches; Active is reached only by signing. Forward only — going
    back is a send-back's job."""
    stage = step.get("stage")
    if not run.contract_id or stage not in ("drafting", "review"):
        return
    contract = _get_contract(db, run.contract_id)
    now = (contract.lifecycle_stage or "intake").lower()
    if now in _STAGE_ORDER and _STAGE_ORDER.index(now) < _STAGE_ORDER.index(stage):
        _advance_contract_to(db, contract=contract, target=stage, actor=actor,
                             reason=f"workflow: {run.flow_name} · {step.get('name') or stage}")


def _advance_contract_to(db: Session, *, contract, target: str, actor, reason: str):
    """Walk the contract forward one allowed lifecycle edge at a time up to
    `target` (never past SIGNATURE — reaching ACTIVE is the signature subsystem's
    job). Never walks THROUGH Approval: a contract leaves Approval only once its
    current version's chain is fully approved (the lifecycle refuses otherwise). A
    flow with no approval step takes the direct review → signature edge instead,
    so the stage history shows approval was skipped rather than implying it happened."""
    from app.contracts.lifecycle import _current_version_approved, transition_contract_stage
    cur = _STAGE_ORDER.index((contract.lifecycle_stage or "intake").lower())
    tgt = _STAGE_ORDER.index(target)
    while cur < tgt:
        if _STAGE_ORDER[cur] == "approval" and not _current_version_approved(db, contract):
            return  # mid-approval: the approval step keeps waiting for the chain
        nxt = _STAGE_ORDER[cur + 1]
        if nxt == "approval" and target != "approval":
            nxt = target
        transition_contract_stage(db, contract=contract, to_stage=nxt, actor_user_id=actor.id, reason=reason)
        cur = _STAGE_ORDER.index(nxt)


async def _draft_contract(db: Session, *, run: WorkflowRun, mode: str, actor):
    from app.intake.drafting import draft_contract_for_request, ingest_attachment_as_contract
    request = db.get(IntakeRequest, run.request_id)
    if mode == "attachment":
        return await ingest_attachment_as_contract(db, actor=actor, request=request)
    if mode == "custom":
        return await draft_contract_for_request(db, actor=actor, request=request, custom=True)
    return await draft_contract_for_request(db, actor=actor, request=request)


# --------------------------------------------------------------------------- CRUD + serialize

def list_flows(db: Session, *, org_id: str) -> list[Workflow]:
    return db.scalars(
        select(Workflow).where(Workflow.org_id == org_id).order_by(Workflow.eval_order.asc(), Workflow.name.asc())
    ).all()


def create_flow(db: Session, *, actor, payload: dict) -> Workflow:
    f = Workflow(
        id=str(uuid.uuid4()), org_id=actor.org_id, created_by_user_id=actor.id,
        name=payload["name"], description=payload.get("description"),
        enabled=payload.get("enabled", True), is_builtin=False,
        eval_order=payload.get("eval_order", 100), version=1,
        criteria=_clean_criteria(db, actor.org_id, payload.get("criteria")),
        steps=_clean_steps(payload.get("steps") or []),
    )
    if f.enabled:
        _refuse_clash(db, f)
    db.add(f); db.commit(); db.refresh(f)
    return f


def _flow_snapshot(flow: Workflow) -> dict:
    return {"name": flow.name, "enabled": flow.enabled, "criteria": flow.criteria,
            "steps": flow.steps, "version": flow.version}


def update_flow(
    db: Session, *, actor, flow: Workflow, payload: dict, request_id: str | None = None
) -> Workflow:
    before = _flow_snapshot(flow)
    for k in ("name", "description", "enabled", "eval_order"):
        if k in payload and payload[k] is not None:
            setattr(flow, k, payload[k])
    if "criteria" in payload:
        flow.criteria = _clean_criteria(db, flow.org_id, payload["criteria"])
    if "steps" in payload:
        flow.steps = _clean_steps(payload["steps"] or [])
    if flow.enabled:
        _refuse_clash(db, flow)
    flow.version = (flow.version or 1) + 1
    flow.updated_by_user_id = actor.id
    write_audit_log(
        db, action="workflow.updated", resource_type="workflow", resource_id=flow.id,
        org_id=flow.org_id, actor_user_id=actor.id, request_id=request_id,
        before=before, after=_flow_snapshot(flow),
    )
    db.commit(); db.refresh(flow)
    return flow


def _clean_criteria(db: Session, org_id: str, criteria: dict | None) -> dict:
    """Keep only real "Used for" entries: one of the agreement forms, and at most
    one entry per (form, agreement type)."""
    from app.intake.agreement_forms import form_defs

    c = dict(criteria or {})
    if "used_for" not in c:
        return c
    if {e[0] for e in _used_for(c)} - {f["key"] for f in form_defs()}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Used for names a form that doesn't exist.")
    seen, entries = set(), []
    for e in c.get("used_for") or []:
        if not isinstance(e, dict) or not e.get("form"):
            continue
        agreement = str(e.get("agreement_type") or "").strip() or None
        key = (e["form"], (agreement or "").lower())
        if key not in seen:
            seen.add(key)
            entries.append({"form": e["form"], "agreement_type": agreement})
    c["used_for"] = entries
    c["conditions"] = _clean_conditions(c.get("conditions"), entries)
    return c


def _clean_conditions(conditions, used_for: list[dict]) -> list[dict]:
    """"Chosen when" conditions, checked: a real operator, numbers where an
    amount is compared, and only on a question the workflow's form asks — a
    condition on a question the form never asks could never be met."""
    from app.intake.agreement_forms import form_def

    if not conditions:
        return []
    if not used_for:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick an agreement type before adding conditions.")
    kinds = {f["key"]: f.get("kind") for f in (form_def(used_for[0]["form"]) or {}).get("fields", [])}
    asked = set(kinds)
    out = []
    for c in conditions:
        field, op = str(c.get("field") or ""), str(c.get("op") or "")
        if op not in CHOOSE_OPS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown condition '{op}'.")
        if field != "value" and field not in asked:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"This form doesn't ask '{field}'.")
        if field == "value" and not asked & set(_VALUE_KEYS):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This form doesn't ask a value.")
        cond = {"field": field, "op": op, "value": c.get("value")}
        if (op in _DATE_OPS) != (kinds.get(field) == "date"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "'Within days' is for date questions, and a date question needs it.")
        if op in _DATE_OPS:
            try:
                cond["value"] = int(c.get("value"))
                if cond["value"] < 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    "Days must be a whole number, 0 or more.") from exc
        elif op in _AMOUNT_OPS:
            try:
                cond["value"] = float(str(c.get("value")).replace(",", ""))
                if op == "between":
                    cond["value2"] = float(str(c.get("value2")).replace(",", ""))
                    if cond["value2"] <= cond["value"]:
                        raise ValueError
            except (TypeError, ValueError) as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    "Amounts must be numbers, and a range must go from low to high.") from exc
            cond["currency"] = c.get("currency") or settings.default_currency
        elif not str(c.get("value") or "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick an answer for each condition.")
        out.append(cond)
    return out


def _refuse_clash(db: Session, flow: Workflow) -> None:
    other = used_for_clash(db, org_id=flow.org_id, criteria=flow.criteria, exclude_id=flow.id)
    if other:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"“{other}” is already used for that request — each can pick only one workflow.")


def _clean_steps(steps: list) -> list:
    out = []
    for i, s in enumerate(steps):
        t = s.get("type")
        if t not in STEP_TYPES:
            continue
        step = {
            "id": s.get("id") or str(uuid.uuid4()),
            "type": t,
            "name": s.get("name") or t.replace("_", " ").title(),
            "config": s.get("config") or {},
        }
        # A parallel flag runs the step concurrently with the one(s) above it;
        # the first step can't be parallel (nothing to pair with).
        if s.get("parallel") and i > 0:
            step["parallel"] = True
        if s.get("stage"):
            step["stage"] = s["stage"]
        # A run-only-when condition (the designer's "only when X"): keep only a
        # well-formed {field, op, value} so the executor can evaluate it.
        cond = s.get("cond")
        if isinstance(cond, dict) and (cond.get("field") or "").strip():
            step["cond"] = {
                "field": cond["field"].strip(),
                "op": (cond.get("op") or "eq").strip().lower(),
                "value": cond.get("value"),
            }
            _validate_condition(step["cond"], step["name"])
        for key in ("skip_when", "skip_if"):
            rule = step["config"].get(key)
            if isinstance(rule, dict) and rule.get("field"):
                _validate_condition(rule, step["name"])
        out.append(step)
    # Every step sits under a lifecycle stage (workflows/stages.py): fill in any
    # the designer left out, then refuse an order the lifecycle can't follow.
    from app.workflows import stages

    stages.infer(out)
    bad = stages.problems(out)
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, " ".join(bad[:3]))
    return out


def serialize_flow(f: Workflow) -> dict:
    return {
        "id": f.id, "name": f.name, "description": f.description, "enabled": f.enabled,
        "is_builtin": f.is_builtin, "eval_order": f.eval_order, "version": f.version,
        "criteria": f.criteria or {}, "steps": f.steps or [],
    }


def _authority_notes(db: Session, run: WorkflowRun, cfgs: list) -> dict[int, str]:
    """Per approval step: the authority it needs and how many of its approvers
    hold it (see authority_limits / Admin → Authority). Empty while no authority
    policy exists — then nobody is limited."""
    from app.authority.service import approval_authority_note
    from app.intake.teams import member_users

    idxs = [i for i, c in enumerate(cfgs) if (c or {}).get("type") == "approval"]
    if not idxs:
        return {}
    request = db.get(IntakeRequest, run.request_id)
    contract = _get_contract(db, run.contract_id) if run.contract_id else None
    out = {}
    for i in idxs:
        t = approval_target(db, org_id=run.org_id, cfg=(cfgs[i] or {}).get("config") or {}, request=request)
        ids = [t["approver_user_id"]] if t["approver_user_id"] else [
            u.id for u in member_users(db, team_id=t["approver_team_id"], org_id=run.org_id)]
        note = approval_authority_note(db, org_id=run.org_id, user_ids=ids, contract=contract)
        if note:
            out[i] = note
    return out


def serialize_run(db: Session, run: WorkflowRun) -> dict:
    from app.auth.models import User

    srs = _step_runs(db, run)
    # Resolve the labels the UI needs (who / which team) in two batched lookups.
    uids = {s.assignee_user_id for s in srs if s.assignee_user_id}
    cfgs = list(run.steps or [])
    # A step that hasn't started has no team yet; show the one its workflow names.
    planned = {i: ((c.get("config") or {}).get("team_id")) for i, c in enumerate(cfgs)}
    tids = {s.team_id for s in srs if s.team_id} | {t for t in planned.values() if t}
    users = {u.id: (u.full_name or u.email) for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    teams = {t.id: t.name for t in db.scalars(select(IntakeTeam).where(IntakeTeam.id.in_(tids))).all()} if tids else {}
    authority = _authority_notes(db, run, cfgs)
    return {
        "id": run.id, "request_id": run.request_id, "flow_id": run.flow_id, "flow_name": run.flow_name,
        "status": run.status, "current_index": run.current_index, "contract_id": run.contract_id,
        "error": run.error,
        "comments": list((run.context or {}).get("comments") or []),
        # The multi-agent hand-off trace: what each agent/step established, in order.
        "brief": list((run.context or {}).get("brief") or []),
        "steps": [
            {
                "idx": s.idx, "type": s.step_type, "name": s.step_name, "status": s.status,
                "assignee_user_id": s.assignee_user_id,
                "assignee_label": users.get(s.assignee_user_id) if s.assignee_user_id else None,
                "team_id": s.team_id, "team_label": teams.get(s.team_id) if s.team_id else None,
                "planned_team_label": teams.get(planned.get(s.idx)) if planned.get(s.idx) else None,
                "authority_note": authority.get(s.idx),
                "sla_hours": ((cfgs[s.idx].get("config") or {}).get("sla_hours") if s.idx < len(cfgs) else None),
                "note": s.note, "result": s.result,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "parallel": bool(cfgs[s.idx].get("parallel")) if s.idx < len(cfgs) else False,
                "stage": cfgs[s.idx].get("stage") if s.idx < len(cfgs) else None,
                "cond": cfgs[s.idx].get("cond") if s.idx < len(cfgs) else None,
            }
            for s in srs
        ],
    }


# --- DI service -------------------------------------------------------------
class WorkflowService:
    """Governance workflow selection, execution, and CRUD, for Depends()
    injection (see backend/DI_MIGRATION.md).

    Unlike most migrated modules the bodies stay in the module-level functions
    above and these methods delegate to them: the engine is one call graph
    (`advance_run` → `_execute_step` → `_resolve_waiting_group` → `_step_runs` …)
    and its tests patch those module-level names (`_step_runs`, `_sr_at`,
    `_execute_step`, `get_run_for_request`, …) and read `_execute_step`'s source
    with `inspect.getsource`. Moving the bodies into methods would let a patched
    name be silently bypassed.
    """

    def __init__(self, db: Session):
        self.db = db

    def select_flow(self, *, request: IntakeRequest) -> Workflow | None:
        return select_flow(self.db, request=request)

    def get_run_for_request(self, *, request_id: str, org_id: str) -> WorkflowRun | None:
        return get_run_for_request(self.db, request_id=request_id, org_id=org_id)

    def get_run_for_contract(self, *, contract_id: str, org_id: str) -> WorkflowRun | None:
        return get_run_for_contract(self.db, contract_id=contract_id, org_id=org_id)

    def _step_runs(self, run: WorkflowRun) -> list[WorkflowStepRun]:
        return _step_runs(self.db, run)

    def _sr_at(self, run: WorkflowRun, idx: int) -> WorkflowStepRun | None:
        return _sr_at(self.db, run, idx)

    def _group_done(self, run: WorkflowRun, start: int, end: int) -> bool:
        return _group_done(self.db, run, start, end)

    async def start_flow(
        self, *, actor, request: IntakeRequest, flow: Workflow | None = None, request_id: str | None = None
    ) -> WorkflowRun:
        return await start_flow(
            self.db, actor=actor, request=request, flow=flow, request_id=request_id,
        )

    async def start_flow_for_contract(
        self, *, actor, contract, flow: Workflow | None = None, request_id: str | None = None
    ) -> WorkflowRun:
        return await start_flow_for_contract(
            self.db, actor=actor, contract=contract, flow=flow, request_id=request_id,
        )

    async def advance_run(self, *, run: WorkflowRun, actor) -> WorkflowRun:
        return await advance_run(self.db, run=run, actor=actor)

    def _finalize_intake_if_approved(self, *, run: WorkflowRun, actor) -> None:
        return _finalize_intake_if_approved(self.db, run=run, actor=actor)

    def complete_human_step(
        self, *, run: WorkflowRun, actor, note: str | None = None, step_idx: int | None = None, request_id: str | None = None
    ):
        return complete_human_step(
            self.db, run=run, actor=actor, note=note, step_idx=step_idx, request_id=request_id,
        )

    async def return_run(
        self, *, run: WorkflowRun, actor, to_idx: int | None = None, note: str | None = None, request_id: str | None = None
    ) -> WorkflowRun:
        return await return_run(
            self.db, run=run, actor=actor, to_idx=to_idx, note=note, request_id=request_id,
        )

    def add_run_comment(
        self, *, run: WorkflowRun, actor, text: str, idx: int | None = None, kind: str = 'comment'
    ) -> WorkflowRun:
        return add_run_comment(self.db, run=run, actor=actor, text=text, idx=idx, kind=kind)

    async def refresh_run(self, *, run: WorkflowRun, actor) -> WorkflowRun:
        return await refresh_run(self.db, run=run, actor=actor)

    def advance_flow_for_contract(self, *, contract, actor_user_id: str | None) -> None:
        return advance_flow_for_contract(self.db, contract=contract, actor_user_id=actor_user_id)

    def list_flows(self, *, org_id: str) -> list[Workflow]:
        return list_flows(self.db, org_id=org_id)

    def create_flow(self, *, actor, payload: dict) -> Workflow:
        return create_flow(self.db, actor=actor, payload=payload)

    def update_flow(
        self, *, actor, flow: Workflow, payload: dict, request_id: str | None = None
    ) -> Workflow:
        return update_flow(self.db, actor=actor, flow=flow, payload=payload, request_id=request_id)

    def serialize_run(self, run: WorkflowRun) -> dict:
        return serialize_run(self.db, run)
