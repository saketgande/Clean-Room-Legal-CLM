"""HTTP for the ideal engine — a working demo you can click through.

This is the one place ``ideal`` meets the running application, and it is
deliberately thin: it wires the pure engine (app/ideal/workflow.py) to an
in-process event store and exposes it under ``/ideal/*``. No database table, no
alembic migration — the store lives in memory, which is exactly right for a demo
that must not touch the live schema. Restarting the backend resets it.

The AI is stubbed with the same injected-function seam the tests use: a rules
extractor and a rules recommender stand in for a model, so the demo is
deterministic and costs nothing to run. Swapping in a real model is a one-line
change and the pipeline never learns the difference.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.ideal import intake as I
from app.ideal.library import LIBRARY
from app.ideal.roster import Member, Roster, auto_assigner
from app.ideal.store import InMemoryEventStore, inbox
from app.ideal.workflow import (
    advance,
    matches,
    record_change,
    start,
    step_report,
    waiting_on,
)

router = APIRouter(prefix="/ideal", tags=["ideal"])

# --- the in-process world (resets on restart — it is a demo) ---------------

_STORE = InMemoryEventStore()
_REQUESTS: dict[str, dict] = {}   # ref -> {request, recommendation}
_SEQ = {"n": 4122}

_ROSTER = Roster(members=(
    Member("Ops Bot", "Legal Ops"),
    Member("Aisha Sharma", "Legal & IP"),
    Member("Priya Nair", "Legal & IP"),
    Member("Meera Rao", "Finance & Tax"),
    Member("Rahul Iyer", "Quality & Compliance"),
    Member("Sanjay Das", "Risk & Compliance"),
    Member("Karan Menon", "IT / Digital"),
    Member("Hema Gupta", "Procurement"),
))
_ASSIGN = auto_assigner(_ROSTER)


# --- the stubbed "AI", via the injected seam -------------------------------

def _understand(text: str, attachments) -> dict:
    low = text.lower()
    f: dict[str, str] = {}
    for name in ("Acme Corp", "Zenith Labs", "Nova Pharma"):
        if name.lower() in low:
            f["counterparty"] = name
    if "nda" in low or "non-disclosure" in low or "confidential" in low:
        f["type"] = "NDA"
    elif "master services" in low or "msa" in low or "services agreement" in low:
        f["type"] = "MSA"
    if "gxp" in low or "manufactur" in low or "clinical" in low:
        f["gxp"] = "true"
    if "personal data" in low or "patient" in low:
        f["personal_data"] = "true"
    return f


def _recommend(text: str, fields: dict, attachments) -> dict:
    kind = (fields.get("type") or "NDA")
    key = "msa" if kind == "MSA" else "nda"
    cp = fields.get("counterparty", "the counterparty")
    reasons = [f"the request describes a {kind}"]
    if attachments:
        reasons.append(f"read the attached {attachments[0].filename}")
    if fields.get("gxp"):
        reasons.append("mentions GxP / manufacturing — Quality review will be pulled in")
    return {
        "workflow_key": key,
        "confidence": 0.86 if key == "nda" else 0.78,
        "summary": f"{kind} with {cp}. "
                   + ("Straightforward mutual NDA." if key == "nda"
                      else "Services agreement — reviewers depend on scope and value."),
        "reasons": reasons,
        "alternatives": (
            [{"workflow_key": "msa", "why": "use this if it becomes an ongoing services deal"}]
            if key == "nda" else
            [{"workflow_key": "nda", "why": "use this if only confidentiality is needed first"}]
        ),
    }


# --- request/response bodies -----------------------------------------------

class Intake(BaseModel):
    channel: str = "email"
    sender: str = "requester@drreddys.com"
    subject: str = ""
    body: str = ""
    attachment_name: str | None = None
    attachment_text: str | None = None


class Reply(BaseModel):
    fields: dict[str, str]


class ChooseBody(BaseModel):
    workflow_key: str
    context: dict = {}


class Decision(BaseModel):
    step_id: str
    outcome: str
    actor: str
    comment: str | None = None
    confidence: float | None = None


class ChangeBody(BaseModel):
    step_id: str
    what: str
    by: str


def _wf(run_id: str):
    """The workflow a run belongs to — read straight from its `started` event.

    replay() needs the right workflow to fold events (a parallel step in one
    workflow is not a step in another), so the key must come from the stored
    events, not from guessing.
    """
    rows = _STORE.events(run_id)
    if not rows:
        raise HTTPException(404, "run not found")
    started = next((r for r in rows if r.get("kind") == "started"), None)
    key = (started or {}).get("payload", {}).get("workflow_key")
    wf = LIBRARY.get(key)
    if wf is None:
        raise HTTPException(404, f"unknown workflow for run {run_id!r}")
    return wf


def _run_view(run_id: str) -> dict:
    wf = _wf(run_id)
    state = _STORE.load(run_id, wf)
    # waiting_on() reports by step NAME; the decide endpoint needs the id, so
    # attach it here rather than making the frontend guess from the name.
    name_to_id = {s.name: s.id for s in wf.steps.values()}
    waiting = [{**w, "step_id": name_to_id.get(w["step"])} for w in waiting_on(state, wf)]
    return {
        "run_id": run_id,
        "workflow": wf.name,
        "status": state.status,
        "context": state.context,
        "waiting_on": waiting,
        "steps": step_report(state, wf),
        "not_applicable": [
            {"step": wf.step(e.step_id).name, "reason": e.reason}
            for e in state.events if e.kind == "not_applicable"
        ],
        "events": [
            {"seq": e.seq, "kind": e.kind, "step": wf.steps.get(e.step_id).name
             if e.step_id in wf.steps else e.step_id, "actor": e.actor,
             "outcome": e.outcome, "comment": e.comment, "reason": e.reason,
             "confidence": e.confidence, "round": e.round}
            for e in state.events
        ],
    }


# --- endpoints --------------------------------------------------------------

@router.get("/workflows")
def list_workflows(_=Depends(get_current_user)):
    """The available workflows, so the picker can show them."""
    return [
        {"key": wf.key, "name": wf.name, "steps": len(wf.steps),
         "start": wf.step(wf.start).name}
        for wf in LIBRARY.values()
    ]


@router.post("/intake")
def do_intake(body: Intake, _=Depends(get_current_user)):
    """Capture from any channel → understand → number → check completeness →
    recommend a workflow, with reasons."""
    _SEQ["n"] += 1
    ref = f"REQ-{_SEQ['n']}"
    atts = ()
    if body.attachment_name:
        atts = (I.Attachment(body.attachment_name, body.attachment_text or ""),)
    inbound = I.Inbound(channel=body.channel, sender=body.sender, subject=body.subject,
                        body=body.body, attachments=atts)
    req = I.capture(inbound, ref=ref, understand=_understand,
                    required=("counterparty", "type"))
    rec = I.recommend(req, recommender=_recommend)
    _REQUESTS[ref] = {"request": req, "recommendation": rec}
    return {
        "request": {"ref": req.ref, "name": req.name, "channel": req.channel,
                    "fields": req.fields, "missing": list(req.missing),
                    "status": req.status, "history": list(req.history)},
        "next_chase": I.next_chase(req),
        "notification": I.notification(req, rec),
    }


@router.post("/intake/{ref}/reply")
def reply(ref: str, body: Reply, _=Depends(get_current_user)):
    """The sender answers the chase for missing details."""
    entry = _REQUESTS.get(ref)
    if not entry:
        raise HTTPException(404, "request not found")
    req = I.record_chase(entry["request"])
    req = I.apply_reply(req, body.fields)
    entry["request"] = req
    return {"ref": req.ref, "missing": list(req.missing), "status": req.status,
            "fields": req.fields, "history": list(req.history)}


@router.post("/intake/{ref}/choose")
def choose(ref: str, body: ChooseBody, current=Depends(get_current_user)):
    """A human confirms the workflow. THIS is what starts the run."""
    entry = _REQUESTS.get(ref)
    if not entry:
        raise HTTPException(404, "request not found")
    if body.workflow_key not in LIBRARY:
        raise HTTPException(422, f"unknown workflow {body.workflow_key!r}")
    who = getattr(current, "full_name", None) or getattr(current, "email", "a user")
    I.choose(entry["request"], entry["recommendation"], workflow_key=body.workflow_key, by=who)
    wf = LIBRARY[body.workflow_key]
    # carry the extracted facts into the run so conditional steps work
    ctx = {**{k: (v == "true" if v in ("true", "false") else v)
              for k, v in entry["request"].fields.items()}, **body.context}
    state = start(wf, assigner=_ASSIGN, context=ctx)
    _STORE.append(ref, state)
    return _run_view(ref)


@router.get("/runs")
def list_runs(_=Depends(get_current_user)):
    out = []
    for run_id in _STORE.runs():
        wf = _wf(run_id)
        st = _STORE.load(run_id, wf)
        out.append({"run_id": run_id, "workflow": wf.name, "status": st.status,
                    "open": [wf.step(s).name for s in st.active]})
    return out


@router.get("/runs/{run_id}")
def get_run(run_id: str, _=Depends(get_current_user)):
    return _run_view(run_id)


@router.post("/runs/{run_id}/decide")
def decide(run_id: str, body: Decision, _=Depends(get_current_user)):
    wf = _wf(run_id)
    state = _STORE.load(run_id, wf)
    try:
        state = advance(state, wf, step_id=body.step_id, outcome=body.outcome,
                        actor=body.actor, comment=body.comment,
                        confidence=body.confidence, assigner=_ASSIGN)
    except Exception as e:
        raise HTTPException(422, str(e))
    _STORE.append(run_id, state)
    return _run_view(run_id)


@router.post("/runs/{run_id}/change")
def change(run_id: str, body: ChangeBody, _=Depends(get_current_user)):
    wf = _wf(run_id)
    state = _STORE.load(run_id, wf)
    try:
        state = record_change(state, wf, step_id=body.step_id, what=body.what, by=body.by)
    except Exception as e:
        raise HTTPException(422, str(e))
    _STORE.append(run_id, state)
    return _run_view(run_id)


@router.get("/runs/{run_id}/at/{seq}")
def at_seq(run_id: str, seq: int, _=Depends(get_current_user)):
    """Time travel — the run as it stood at an earlier event."""
    wf = _wf(run_id)
    state = _STORE.load(run_id, wf, upto=seq)
    return {"run_id": run_id, "upto": seq, "status": state.status,
            "open": [wf.step(s).name for s in state.active]}
