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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_timeline_event
from app.core.database import utcnow
from app.workflows.models import STEP_TYPES, Workflow, WorkflowRun, WorkflowStepRun
from app.intake.models import IntakeRequest, IntakeTeam

logger = logging.getLogger(__name__)

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
    if mk and not re.search(rf"\b{re.escape(mk)}\b", (request.description or "").lower()):
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


def select_flow(db: Session, *, request: IntakeRequest) -> Workflow | None:
    """First enabled flow (lowest eval_order) whose criteria match. An empty
    criteria dict matches everything, so a high-eval_order catch-all is the
    default workflow."""
    flows = db.scalars(
        select(Workflow).where(Workflow.org_id == request.org_id, Workflow.enabled.is_(True)).order_by(Workflow.eval_order.asc())
    ).all()
    for f in flows:
        if _matches(f.criteria, request):
            return f
    return None


# --------------------------------------------------------------------------- run lifecycle

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


async def start_flow(db: Session, *, actor, request: IntakeRequest, flow: Workflow | None = None) -> WorkflowRun:
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
    return await advance_run(db, run=run, actor=actor)


def _record_step_finding(run: WorkflowRun, step: dict, sr: "WorkflowStepRun | None") -> None:
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
            break
        # Execute every not-yet-terminal step in the current parallel group (a
        # size-1 group is a plain sequential step). The group holds the run until
        # all its steps are done; a single waiting step parks the whole run.
        start = run.current_index
        end = _group_end(steps, start)
        any_wait = False
        try:
            for gi in range(start, end):
                sr = _sr_at(db, run, gi)
                if sr and sr.status in ("done", "skipped", "failed"):
                    continue
                outcome = await _execute_step(db, run=run, step=steps[gi], sr=sr, actor=actor)
                if outcome == "advance":
                    if sr and sr.status not in ("done", "skipped"):
                        sr.status = "done"
                    _record_step_finding(run, steps[gi], sr)
                elif outcome == "wait":
                    any_wait = True
                # "yield": step is mid-beat (an ai_task animating its running
                # state); leave it running — a refresh tick resumes it.
        except HTTPException:
            raise
        except Exception as exc:  # a broken step must not wedge the whole run
            sr = _sr_at(db, run, start)
            if sr:
                sr.status = "failed"; sr.note = str(exc)[:500]
            run.status = "failed"; run.error = str(exc)[:500]
            break
        if _group_done(db, run, start, end):
            run.current_index = end
            continue
        if any_wait:
            run.status = "waiting"
        # else only "yield" steps remain running; stop and let a refresh tick
        # pump them (the UI pumps it when the ticket is viewed).
        # ponytail: add a periodic sweep resume if headless progress matters.
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
    # a skipped step never created its Tier-0 gate rungs (litigation, sensitive-
    # data, …), so that would approve with zero sign-off, bypassing a mandatory
    # gate. Require a real, cleared (or no-rungs-needed) approval step-run.
    approval_srs = [s for s in _step_runs(db, run) if s.step_type == "approval"]
    if not approval_srs:
        return
    if not any(s.status in ("done", "complete") for s in approval_srs):
        logger.warning(
            "intake flow completed with its approval step skipped/unresolved — NOT "
            "auto-approving (a skip_when rule likely bypassed a Tier-0 gate)",
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


def _resolve_role_team(db: Session, *, org_id: str, role: str) -> IntakeTeam | None:
    """Best-effort map a flow step's approver_role token onto an intake team pool
    (by key, then by name substring), so a step that names a group actually routes
    to it."""
    r = (role or "").strip().lower()
    if not r:
        return None
    team = db.scalar(select(IntakeTeam).where(IntakeTeam.org_id == org_id, IntakeTeam.active.is_(True), func.lower(IntakeTeam.key) == r))
    if team:
        return team
    return db.scalar(select(IntakeTeam).where(IntakeTeam.org_id == org_id, IntakeTeam.active.is_(True), func.lower(IntakeTeam.name).like(f"%{r}%")))


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
    the least-loaded member of the team the approver_role resolves to. Always
    falls back to the request's owner so a step is never assigned to nobody."""
    from app.intake.teams import pick_from_pool

    role = cfg.get("approver_role") or cfg.get("assignee_role")
    assign_by = (cfg.get("assign_by") or "").lower()
    user_id = cfg.get("assignee_user_id")
    team_id = cfg.get("team_id")
    if not team_id and role:
        team = _resolve_role_team(db, org_id=run.org_id, role=role)
        team_id = team.id if team else None
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
            logger.warning("flow %s: pool pick failed for team %s", run.flow_name, team_id, exc_info=True)
    if not user_id:
        user_id = req.assigned_to_user_id if req else None
    sr.assignee_user_id = user_id
    sr.team_id = team_id
    if role or assign_by:
        sr.result = {**(sr.result or {}), "role": role, "assign_by": cfg.get("assign_by")}


async def _execute_step(db: Session, *, run: WorkflowRun, step: dict, sr: WorkflowStepRun | None, actor) -> str:
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

    # Per-step run-only-when condition (the designer's "only when X"): the
    # inverse of skip_when — run this step only when the condition matches,
    # otherwise skip it. Same {field, op, value} criteria shape.
    cond = step.get("cond") or cfg.get("cond") or {}
    if cond and not _matches(cond, db.get(IntakeRequest, run.request_id)):
        if sr:
            sr.status = "skipped"; sr.note = "Condition not met"
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
        # Run the configured agent best-effort. If it's a registered intake
        # agent (incl. the ported library's agent keys), run the deterministic
        # classifier for a real confidence; otherwise try it as an AI skill.
        # When confidence is below the step threshold, escalate to a human.
        agent_val = (cfg.get("agent") or cfg.get("skill") or "").strip()
        mapped = _AGENT_KEY_MAP.get(agent_val.lower())
        conf = None
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
                elif mapped == "vendor_agent":
                    # Sanctions/conflict screening (intake/screening.py) is a real
                    # watchlist + relationship check, not an LLM judgment call — an
                    # LLM guessing at sanctions hits would be the wrong tool here.
                    # It already ran synchronously at intake time and is sitting on
                    # the request; reuse it rather than replace it with a regex
                    # category guess that never looked at any of this.
                    screening = request.screening if isinstance(request.screening, dict) else None
                    if screening and screening.get("status") == "done":
                        sanctions = screening.get("sanctions") or {}
                        conflicts = screening.get("conflicts") or []
                        s_status = sanctions.get("status")
                        high_conflict = any(c.get("severity") == "high" for c in conflicts)
                        if s_status == "hit" or high_conflict:
                            vconf = 0.1  # a real hit or high-severity conflict always escalates
                        elif s_status == "unavailable":
                            vconf = 0.2  # unscreened is never "clear" — escalate, don't guess
                        else:
                            vconf = 0.95
                        real = (vconf, {
                            "agent": mapped, "source": "screening", "confidence": vconf,
                            "counterparty": screening.get("counterparty"),
                            "sanctions_status": s_status,
                            "sanctions_matches": sanctions.get("matches"),
                            "conflicts": conflicts[:5],
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
        thr = cfg.get("escalate_below_confidence")
        if thr is not None and conf is not None and conf < thr:
            if sr:
                sr.status = "waiting_human"
                base = f"AI confidence {conf} < {thr} — escalated to {cfg.get('escalate_role') or 'a human'}"
                # A branch may have already left a more specific explanation in
                # sr.result (e.g. "risk score not ready in time" for the contract
                # review fallback) — surface it instead of only the generic number.
                detail = (sr.result or {}).get("note") if isinstance(sr.result, dict) else None
                sr.note = f"{base} ({detail})" if detail else base
            return "wait"
        return "advance"

    if t == "clm_draft":
        # The requester's chosen path (from the intake form) decides HOW the
        # document is produced: fast-lane uses the standard template; "custom"
        # routes to an attorney to draft fresh (no template); "attach" waits for
        # the requester to upload their own NDA. Only fast-lane auto-drafts.
        req = db.get(IntakeRequest, run.request_id)
        path = str(((req.field_values or {}).get("draft_path")) or cfg.get("mode") or "fast_lane").lower()
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

def complete_human_step(db: Session, *, run: WorkflowRun, actor, note: str | None = None, step_idx: int | None = None):
    # In a parallel group the human may resolve any of the concurrent steps, so
    # accept an explicit idx; default to the current step for sequential flows.
    idx = step_idx if step_idx is not None else run.current_index
    sr = _sr_at(db, run, idx)
    if sr and sr.status == "waiting_human":
        sr.status = "done"; sr.note = note or sr.note
        steps = list(run.steps or [])
        start = run.current_index
        end = _group_end(steps, start)
        # Advance past the whole group only once every concurrent step is done;
        # otherwise it stays in flight for its remaining steps.
        if _group_done(db, run, start, end):
            run.current_index = end
        run.status = "running"
        db.flush()
    return run


def _configured_return_to(steps: list, current: int) -> int:
    """Where a send-back from the current step lands, per the designer's
    `config.return_to`: a specific earlier step index, else the previous step."""
    cfg = (steps[current].get("config") if 0 <= current < len(steps) else None) or {}
    rt = cfg.get("return_to")
    target = rt if isinstance(rt, int) else current - 1
    return max(0, min(target, current - 1))


async def return_run(db: Session, *, run: WorkflowRun, actor, to_idx: int | None = None, note: str | None = None) -> WorkflowRun:
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
    for sr in _step_runs(db, run):
        if sr.idx >= to_idx:
            sr.status = "pending"
    add_run_comment(db, run=run, actor=actor, idx=to_idx, text=note or "Returned for rework", kind="return")
    run.current_index = to_idx
    run.status = "running"
    run.error = None
    write_timeline_event(
        db, org_id=run.org_id, resource_type="intake_request", resource_id=run.request_id,
        event_type="flow.returned", title=f"Returned to “{steps[to_idx].get('name', 'step')}”",
        actor_user_id=actor.id if actor else None, details={"flow_run_id": run.id, "to_idx": to_idx, "note": note},
    )
    return await advance_run(db, run=run, actor=actor)


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
                    # M4: persist the failed run. Every other branch commits via
                    # advance_run; get_db never commits on success, so without this
                    # the rejection rolls back and the run stays 'waiting' forever.
                    db.commit()
        return run

    contract = _get_contract(db, run.contract_id)
    stage = (contract.lifecycle_stage or "").lower()
    # Group-aware: resolve every approval/signature step in the current group
    # the contract's stage has cleared, then advance once the whole group is done.
    start = run.current_index
    end = _group_end(steps, start)
    resolved = False
    for gi in range(start, end):
        st = (steps[gi] or {}).get("type")
        cleared = (st == "approval" and stage in ("signature", "active", "closed")) or (
            st == "signature" and stage in ("active", "closed")
        )
        if cleared:
            sr = _sr_at(db, run, gi)
            if sr and sr.status not in ("done", "skipped", "failed"):
                sr.status = "done"; resolved = True
    if resolved and _group_done(db, run, start, end):
        run.status = "running"
        run.current_index = end
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
        select(WorkflowRun).where(WorkflowRun.contract_id == contract.id, WorkflowRun.status == "waiting")
        .order_by(WorkflowRun.created_at.desc())
    ).first()
    if run is None:
        return
    steps = list(run.steps or [])
    if run.current_index >= len(steps):
        return
    stage = (contract.lifecycle_stage or "").lower()
    # Group-aware: clear every approval/signature step in the current group the
    # contract's stage has passed; only advance once the whole group is done.
    start = run.current_index
    end = _group_end(steps, start)
    resolved = False
    for gi in range(start, end):
        st = (steps[gi] or {}).get("type")
        cleared = (st == "approval" and stage in ("signature", "active", "closed")) or (
            st == "signature" and stage in ("active", "closed")
        )
        if cleared:
            sr = _sr_at(db, run, gi)
            if sr and sr.status not in ("done", "skipped", "failed"):
                sr.status = "done"; resolved = True
    if not (resolved and _group_done(db, run, start, end)):
        return
    run.current_index = end
    run.status = "running"
    _run_sync_steps(db, run=run, contract=contract, actor_user_id=actor_user_id)
    # M11: no commit here. This runs inside a stage-entry trigger whose enclosing
    # transaction is owned by the caller (transition_contract_stage) — the same
    # commit that lands the queued AI jobs also lands these flow mutations. An
    # internal commit here would break the transition's atomicity.


def _run_sync_steps(db: Session, *, run: WorkflowRun, contract, actor_user_id: str | None) -> None:
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
        # A multi-step parallel group can't run through this sequential cascade
        # walker — leave it running so the next refresh/advance tick executes the
        # whole group at once (its steps are still pending).
        if _group_end(steps, run.current_index) > run.current_index + 1:
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
                    logger.warning(
                        "workflow %s: transition to signature failed for contract %s",
                        run.flow_name, contract.id, exc_info=True,
                    )
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
        criteria=payload.get("criteria") or {}, steps=_clean_steps(payload.get("steps") or []),
    )
    db.add(f); db.commit(); db.refresh(f)
    return f


def update_flow(db: Session, *, actor, flow: Workflow, payload: dict) -> Workflow:
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
        # A run-only-when condition (the designer's "only when X"): keep only a
        # well-formed {field, op, value} so the executor can evaluate it.
        cond = s.get("cond")
        if isinstance(cond, dict) and (cond.get("field") or "").strip():
            step["cond"] = {
                "field": cond["field"].strip(),
                "op": (cond.get("op") or "eq").strip().lower(),
                "value": cond.get("value"),
            }
        out.append(step)
    return out


def serialize_flow(f: Workflow) -> dict:
    return {
        "id": f.id, "name": f.name, "description": f.description, "enabled": f.enabled,
        "is_builtin": f.is_builtin, "eval_order": f.eval_order, "version": f.version,
        "criteria": f.criteria or {}, "steps": f.steps or [],
    }


def serialize_run(db: Session, run: WorkflowRun) -> dict:
    from app.auth.models import User

    srs = _step_runs(db, run)
    # Resolve the labels the UI needs (who / which team) in two batched lookups.
    uids = {s.assignee_user_id for s in srs if s.assignee_user_id}
    tids = {s.team_id for s in srs if s.team_id}
    users = {u.id: (u.full_name or u.email) for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    teams = {t.id: t.name for t in db.scalars(select(IntakeTeam).where(IntakeTeam.id.in_(tids))).all()} if tids else {}
    cfgs = list(run.steps or [])
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
                "role": (cfgs[s.idx].get("config") or {}).get("approver_role") if s.idx < len(cfgs) else None,
                "note": s.note, "result": s.result,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "parallel": bool(cfgs[s.idx].get("parallel")) if s.idx < len(cfgs) else False,
                "cond": cfgs[s.idx].get("cond") if s.idx < len(cfgs) else None,
            }
            for s in srs
        ],
    }
