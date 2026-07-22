"""The workflow engine: select a flow for a ticket, then walk its steps.

One executor (`advance_run`) is the single brain. Each step type is a thin
adapter to a subsystem that already exists (drafting, AI skills, approval ladder,
signatures). The subsystem chain auto-cascades (approval-complete -> SIGNATURE,
signature-complete -> ACTIVE -> obligations), so waiting steps resume by
detecting the contract's stage change (`refresh_run`).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.audit import write_timeline_event
from app.core.database import Session
from app.flows.models import STEP_TYPES, Flow, FlowRun, FlowStepRun
from app.intake.models import IntakeRequest

# Contract lifecycle order — walk one edge at a time to respect the engine.
_STAGE_ORDER = ["intake", "drafting", "review", "approval", "signature", "active", "closed"]

# A no-contract approval step (litigation / notice / regulatory / board matters)
# gates the intake request itself. Its flow role token maps to a functional
# approver group so the fallback rung is decidable + emailed when no routing
# rule or Tier-0 gate already supplies one.
_FLOW_ROLE_GROUP = {
    "gc": "Executive",
    "board": "Executive",
    "attorney": "Legal Counsel",
    "legal_ops": "Compliance",
}

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
        if mt not in hay:
            return False
    mp = (c.get("match_priority") or "").strip().lower()
    if mp and (request.priority or "").lower() != mp:
        return False
    md = (c.get("match_department") or "").strip().lower()
    if md and (request.department or "").lower() != md:
        return False
    mk = (c.get("match_keyword") or "").strip().lower()
    if mk and mk not in (request.description or "").lower():
        return False
    # Field condition (used by step skip_when): {field, op, value} against the
    # request's structured answers. An absent field is treated as not-equal.
    fld = c.get("field")
    if fld:
        op = (c.get("op") or "eq").lower()
        have = (request.field_values or {}).get(fld)
        want = c.get("value")
        if op == "eq" and have != want:
            return False
        if op == "ne" and have == want:
            return False
    return True


def select_flow(db: Session, *, request: IntakeRequest) -> Flow | None:
    """First enabled flow (lowest eval_order) whose criteria match. An empty
    criteria dict matches everything, so a high-eval_order catch-all is the
    default workflow."""
    flows = db.scalars(
        select(Flow).where(Flow.org_id == request.org_id, Flow.enabled.is_(True)).order_by(Flow.eval_order.asc())
    ).all()
    for f in flows:
        if _matches(f.criteria, request):
            return f
    return None


# --------------------------------------------------------------------------- run lifecycle

def get_run_for_request(db: Session, *, request_id: str, org_id: str) -> FlowRun | None:
    return db.scalars(
        select(FlowRun).where(FlowRun.request_id == request_id, FlowRun.org_id == org_id)
        .order_by(FlowRun.created_at.desc())
    ).first()


def _step_runs(db: Session, run: FlowRun) -> list[FlowStepRun]:
    return db.scalars(
        select(FlowStepRun).where(FlowStepRun.flow_run_id == run.id).order_by(FlowStepRun.idx.asc())
    ).all()


async def start_flow(db: Session, *, actor, request: IntakeRequest, flow: Flow | None = None) -> FlowRun:
    """Pick (or use the given) flow, create the run + one step-run per step, then
    drive it to the first waiting step. Idempotent-ish: returns the existing run
    if one is already open for the request."""
    existing = get_run_for_request(db, request_id=request.id, org_id=request.org_id)
    if existing and existing.status in ("running", "waiting"):
        return existing

    flow = flow or select_flow(db, request=request)
    if flow is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No workflow matches this request.")

    steps = list(flow.steps or [])
    run = FlowRun(
        id=str(uuid.uuid4()), org_id=request.org_id, created_by_user_id=actor.id,
        request_id=request.id, flow_id=flow.id, flow_name=flow.name, flow_version=flow.version,
        steps=steps, status="running", current_index=0, contract_id=request.contract_id, context={},
    )
    db.add(run)
    for i, step in enumerate(steps):
        db.add(FlowStepRun(
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
    return await advance_run(db, run=run, actor=actor)


async def advance_run(db: Session, *, run: FlowRun, actor) -> FlowRun:
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
            break
        step = steps[run.current_index]
        sr = next((s for s in _step_runs(db, run) if s.idx == run.current_index), None)
        try:
            outcome = await _execute_step(db, run=run, step=step, sr=sr, actor=actor)
        except HTTPException:
            raise
        except Exception as exc:  # a broken step must not wedge the whole run
            if sr:
                sr.status = "failed"; sr.note = str(exc)[:500]
            run.status = "failed"; run.error = str(exc)[:500]
            break
        if outcome == "advance":
            if sr and sr.status not in ("done", "skipped"):
                sr.status = "done"
            run.current_index += 1
        elif outcome == "wait":
            run.status = "waiting"
            break
        elif outcome == "yield":
            # The step is mid-beat (e.g. an ai_task showing a "running" state so
            # the UI can animate it instead of flashing straight to done). Leave
            # run.status == "running" and stop; a refresh tick resumes and runs it.
            # ponytail: the flow pauses here until the ticket is next viewed (the
            # UI pumps it); add a periodic sweep resume if headless progress matters.
            break
    db.commit()
    db.refresh(run)
    return run


def _finalize_intake_if_approved(db: Session, *, run: FlowRun, actor) -> None:
    """A no-contract intake flow that cleared at least one approval gate is
    finally approved when it completes — the flow owns this terminal transition
    (the ladder's on_complete only records each gate; see AL5). Contract flows
    finalize through the contract lifecycle instead, so skip those. Idempotent."""
    if run.contract_id:
        return
    if not any((s or {}).get("type") == "approval" for s in (run.steps or [])):
        return
    from app.intake.approval_bridge import build_intake_subject

    subject = build_intake_subject(db, run.request_id, org_id=run.org_id)
    subject.finalize_approved(db, actor_user_id=actor.id if actor else None)


async def _execute_step(db: Session, *, run: FlowRun, step: dict, sr: FlowStepRun | None, actor) -> str:
    """Returns 'advance' | 'wait'. Each branch is a thin call into an existing
    subsystem."""
    t = step.get("type", "notify")
    cfg = step.get("config") or {}

    # Per-step skip rule (same criteria shape as flow selection).
    sw = cfg.get("skip_when") or {}
    if sw and _matches(sw, db.get(IntakeRequest, run.request_id)):
        if sr:
            sr.status = "skipped"; sr.note = "Skipped by rule"
        return "advance"

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
            sr.assignee_user_id = cfg.get("assignee_user_id")
            sr.team_id = cfg.get("team_id")
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
        # Run the configured agent best-effort. If it's a registered intake
        # agent (incl. the ported library's agent keys), run the deterministic
        # classifier for a real confidence; otherwise try it as an AI skill.
        # When confidence is below the step threshold, escalate to a human.
        agent_val = (cfg.get("agent") or cfg.get("skill") or "").strip()
        mapped = _AGENT_KEY_MAP.get(agent_val.lower())
        conf = None
        if mapped:
            try:
                from app.intake import agents as intake_agents
                request = db.get(IntakeRequest, run.request_id)
                cls = intake_agents.classify(request.type_label or "", request.description or "")
                conf = cls.get("confidence")
                if sr:
                    sr.result = {"agent": mapped, "category": cls.get("category"), "confidence": conf}
            except Exception as exc:
                if sr:
                    sr.note = f"agent skipped: {str(exc)[:180]}"
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
                if sr:
                    sr.note = f"AI step skipped: {str(exc)[:200]}"
        thr = cfg.get("escalate_below_confidence")
        if thr is not None and conf is not None and conf < thr:
            if sr:
                sr.status = "waiting_human"
                sr.note = f"AI confidence {conf} < {thr} — escalated to {cfg.get('escalate_role') or 'a human'}"
            return "wait"
        return "advance"

    if t == "clm_draft":
        # The requester's chosen path (from the intake form) decides HOW the
        # document is produced: fast-lane uses the standard template; "custom"
        # routes to an attorney to draft fresh (no template); "attach" waits for
        # the requester to upload their own NDA. Only fast-lane auto-drafts.
        req = db.get(IntakeRequest, run.request_id)
        path = str(((req.field_values or {}).get("draft_path")) or cfg.get("mode") or "fast_lane").lower()
        if path in ("attach", "custom"):
            if sr:
                sr.status = "waiting_human"
                sr.note = (
                    "Awaiting the uploaded document — upload it on the ticket and it becomes the contract."
                    if path == "attach"
                    else "Custom draft requested — an attorney drafts it from the captured details (no template)."
                )
            return "wait"
        contract = await _draft_contract(db, run=run, mode="template", actor=actor)
        run.contract_id = contract.id
        if sr:
            sr.result = {"contract_id": contract.id}
        return "advance"

    if t == "approval":
        if not run.contract_id:
            # No contract yet (litigation / notice / regulatory / employment /
            # board matters): gate the intake REQUEST through the shared ladder
            # rather than silently skipping. Its routing rules + Tier-0 gates
            # decide the rungs; the flow role token maps to a functional group
            # as the decidable fallback.
            from app.approvals.models import ApproverGroup
            from app.intake.approval_bridge import submit_request_for_approval

            req = db.get(IntakeRequest, run.request_id)
            role = str(cfg.get("approver_role") or "").lower()
            gname = _FLOW_ROLE_GROUP.get(role)
            group = (
                db.scalar(
                    select(ApproverGroup).where(
                        ApproverGroup.org_id == run.org_id,
                        ApproverGroup.name == gname,
                    )
                )
                if gname
                else None
            )
            reqs = await submit_request_for_approval(
                db, actor=actor, request=req,
                approver_group_id=group.id if group else None,
                approver_role=None if group else (cfg.get("approver_role") or None),
                routing_rule_id=cfg.get("routing_rule_id") or None,
            )
            if not reqs:
                if sr:
                    sr.note = "No approval rungs required — skipped."
                return "advance"
            if sr:
                sr.status = "waiting_job"
                sr.result = {"approval_ids": [r.id for r in reqs]}
            return "wait"
        contract = _get_contract(db, run.contract_id)
        _advance_contract_to(db, contract=contract, target="approval", actor=actor, reason=f"workflow: {run.flow_name}")
        from app.approvals.service import submit_contract_for_approval
        await submit_contract_for_approval(
            db, user=actor, contract=contract, contract_version_id=None,
            approver_user_id=cfg.get("approver_user_id"), approver_role=cfg.get("approver_role", "legal_counsel"),
            routing_rule_id=cfg.get("routing_rule_id") or None,
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
        return "wait"

    if t == "counterparty":
        if sr:
            sr.status = "waiting_human"; sr.note = "Awaiting counterparty response."
        return "wait"

    # unknown step type — record and skip rather than wedge
    if sr:
        sr.status = "skipped"; sr.note = f"Unknown step type: {t}"
    return "advance"


# --------------------------------------------------------------------------- resume + human actions

def complete_human_step(db: Session, *, run: FlowRun, actor, note: str | None = None):
    sr = next((s for s in _step_runs(db, run) if s.idx == run.current_index), None)
    if sr and sr.status == "waiting_human":
        sr.status = "done"; sr.note = note or sr.note
        run.current_index += 1  # move past the completed human step
        run.status = "running"
        db.flush()
    return run


async def refresh_run(db: Session, *, run: FlowRun, actor) -> FlowRun:
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
    step = steps[run.current_index]
    t = step.get("type")

    # No-contract intake approval: advance when the tracked chain terminates.
    # Multi-gate flows are safe — the bridge's on_complete only records each
    # gate while a flow is active; the flow finalizes the request on completion
    # (_finalize_intake_if_approved).
    if not run.contract_id:
        if t == "approval":
            sr = next((s for s in _step_runs(db, run) if s.idx == run.current_index), None)
            ids = (sr.result or {}).get("approval_ids") if sr and sr.result else None
            if ids:
                from app.approvals.models import ApprovalRequest
                from app.core.enums import ApprovalStatus

                chain = db.scalars(
                    select(ApprovalRequest).where(ApprovalRequest.id.in_(ids))
                ).all()
                if chain and all(a.status == ApprovalStatus.APPROVED for a in chain):
                    if sr:
                        sr.status = "done"
                    run.status = "running"
                    run.current_index += 1
                    return await advance_run(db, run=run, actor=actor)
                if any(a.status == ApprovalStatus.REJECTED for a in chain):
                    if sr:
                        sr.status = "failed"
                        sr.note = "Approval rejected — returned to queue."
                    run.status = "failed"
                    run.error = "Approval rejected"
        return run

    contract = _get_contract(db, run.contract_id)
    stage = (contract.lifecycle_stage or "").lower()
    done = (
        (t == "approval" and stage in ("signature", "active", "closed"))
        or (t == "signature" and stage in ("active", "closed"))
    )
    if done:
        sr = next((s for s in _step_runs(db, run) if s.idx == run.current_index), None)
        if sr:
            sr.status = "done"
        run.status = "running"
        run.current_index += 1
        return await advance_run(db, run=run, actor=actor)
    return run


# --------------------------------------------------------------------------- auto-resume (sync)

def advance_flow_for_contract(db: Session, *, contract, actor_user_id: str | None) -> None:
    """Sync auto-resume, called from the contract stage-entry triggers. When a
    contract's stage advances past a waiting approval/signature step (the
    subsystems auto-cascade), advance the run and run any subsequent *synchronous*
    steps (notify/end/signature/human). Async steps (draft/AI/approval-submit) are
    left waiting for the UI/job path. Best-effort — never raises."""
    run = db.scalars(
        select(FlowRun).where(FlowRun.contract_id == contract.id, FlowRun.status == "waiting")
        .order_by(FlowRun.created_at.desc())
    ).first()
    if run is None:
        return
    steps = list(run.steps or [])
    if run.current_index >= len(steps):
        return
    t = (steps[run.current_index] or {}).get("type")
    stage = (contract.lifecycle_stage or "").lower()
    completed = (
        (t == "approval" and stage in ("signature", "active", "closed"))
        or (t == "signature" and stage in ("active", "closed"))
    )
    if not completed:
        return
    sr = next((s for s in _step_runs(db, run) if s.idx == run.current_index), None)
    if sr:
        sr.status = "done"
    run.current_index += 1
    run.status = "running"
    _run_sync_steps(db, run=run, contract=contract, actor_user_id=actor_user_id)
    db.commit()


def _run_sync_steps(db: Session, *, run: FlowRun, contract, actor_user_id: str | None) -> None:
    """Walk steps that can run without awaiting a subsystem; stop at the first
    async or waiting step."""
    steps = list(run.steps or [])
    guard = 0
    while run.status == "running":
        guard += 1
        if guard > 50:
            run.status = "failed"; run.error = "sync-resume guard"; break
        if run.current_index >= len(steps):
            run.status = "complete"
            write_timeline_event(
                db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
                event_type="flow.completed", title=f"Workflow complete: {run.flow_name}",
                actor_user_id=actor_user_id, details={"flow_run_id": run.id},
            )
            break
        step = steps[run.current_index]
        t = step.get("type"); cfg = step.get("config") or {}
        sr = next((s for s in _step_runs(db, run) if s.idx == run.current_index), None)
        if t == "notify":
            write_timeline_event(
                db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
                event_type="flow.notify", title=cfg.get("message") or step.get("name") or "Notification",
                actor_user_id=actor_user_id, details={"flow_run_id": run.id},
            )
            if sr:
                sr.status = "done"
            run.current_index += 1
        elif t == "signature":
            if (contract.lifecycle_stage or "").lower() in ("review", "approval"):
                try:
                    from app.contracts.lifecycle import transition_contract_stage
                    transition_contract_stage(db, contract=contract, to_stage="signature",
                        actor_user_id=actor_user_id or run.created_by_user_id, reason=f"workflow: {run.flow_name}")
                except Exception:
                    pass
            if sr:
                sr.status = "waiting_job"
            run.status = "waiting"; break
        elif t in ("human_task", "counterparty"):
            if sr:
                sr.status = "waiting_human"
            run.status = "waiting"; break
        else:  # approval / clm_draft / ai_task — need the async path; leave for UI/job
            run.status = "waiting"; break


# --------------------------------------------------------------------------- contract helpers

def _get_contract(db: Session, contract_id: str):
    from app.contracts.models import Contract
    c = db.get(Contract, contract_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    return c


def _advance_contract_to(db: Session, *, contract, target: str, actor, reason: str):
    """Walk the contract forward one lifecycle edge at a time up to `target`
    (never past SIGNATURE — reaching ACTIVE is the signature subsystem's job)."""
    from app.contracts.lifecycle import transition_contract_stage
    cur = _STAGE_ORDER.index((contract.lifecycle_stage or "intake").lower())
    tgt = _STAGE_ORDER.index(target)
    while cur < tgt:
        nxt = _STAGE_ORDER[cur + 1]
        transition_contract_stage(db, contract=contract, to_stage=nxt, actor_user_id=actor.id, reason=reason)
        cur += 1


async def _draft_contract(db: Session, *, run: FlowRun, mode: str, actor):
    from app.intake.drafting import draft_contract_for_request, ingest_attachment_as_contract
    request = db.get(IntakeRequest, run.request_id)
    if mode == "attachment":
        return await ingest_attachment_as_contract(db, actor=actor, request=request)
    return await draft_contract_for_request(db, actor=actor, request=request)


# --------------------------------------------------------------------------- CRUD + serialize

def list_flows(db: Session, *, org_id: str) -> list[Flow]:
    return db.scalars(
        select(Flow).where(Flow.org_id == org_id).order_by(Flow.eval_order.asc(), Flow.name.asc())
    ).all()


def create_flow(db: Session, *, actor, payload: dict) -> Flow:
    f = Flow(
        id=str(uuid.uuid4()), org_id=actor.org_id, created_by_user_id=actor.id,
        name=payload["name"], description=payload.get("description"),
        enabled=payload.get("enabled", True), is_builtin=False,
        eval_order=payload.get("eval_order", 100), version=1,
        criteria=payload.get("criteria") or {}, steps=_clean_steps(payload.get("steps") or []),
    )
    db.add(f); db.commit(); db.refresh(f)
    return f


def update_flow(db: Session, *, actor, flow: Flow, payload: dict) -> Flow:
    for k in ("name", "description", "enabled", "eval_order"):
        if k in payload and payload[k] is not None:
            setattr(flow, k, payload[k])
    if "criteria" in payload:
        flow.criteria = payload["criteria"] or {}
    if "steps" in payload:
        flow.steps = _clean_steps(payload["steps"] or [])
    flow.version = (flow.version or 1) + 1
    flow.updated_by_user_id = actor.id
    db.commit(); db.refresh(flow)
    return flow


def _clean_steps(steps: list) -> list:
    out = []
    for s in steps:
        t = s.get("type")
        if t not in STEP_TYPES:
            continue
        out.append({
            "id": s.get("id") or str(uuid.uuid4()),
            "type": t,
            "name": s.get("name") or t.replace("_", " ").title(),
            "config": s.get("config") or {},
        })
    return out


def serialize_flow(f: Flow) -> dict:
    return {
        "id": f.id, "name": f.name, "description": f.description, "enabled": f.enabled,
        "is_builtin": f.is_builtin, "eval_order": f.eval_order, "version": f.version,
        "criteria": f.criteria or {}, "steps": f.steps or [],
    }


def serialize_run(db: Session, run: FlowRun) -> dict:
    srs = _step_runs(db, run)
    return {
        "id": run.id, "request_id": run.request_id, "flow_id": run.flow_id, "flow_name": run.flow_name,
        "status": run.status, "current_index": run.current_index, "contract_id": run.contract_id,
        "error": run.error,
        "steps": [
            {
                "idx": s.idx, "type": s.step_type, "name": s.step_name, "status": s.status,
                "assignee_user_id": s.assignee_user_id, "note": s.note, "result": s.result,
            }
            for s in srs
        ],
    }
