"""The lifecycle shows the whole story, not only the workflow's steps.

Intake is the request being filed, read and routed; Signature is who signs;
Active is obligations and renewal; Closed is the contract ending. Before this the
Intake stage of every request read "no steps", and an uploaded contract (no
request, no workflow) had no lifecycle at all.
"""

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

import app.models  # noqa: F401
from app.workflows import lifecycle_rows as rows
from app.workflows import service

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _request(**kw):
    base = dict(source="form", type_label="New agreement Request", requester_name="Priya", requester_user_id="u1",
                ai_triage={"category": "MSA", "complexity": "standard", "risk_flag": "low"},
                assigned_to_user_id=None, submitted_at=NOW, triaged_at=None)
    return SimpleNamespace(**{**base, **kw})


def test_intake_lists_the_request_events_and_what_is_still_waiting(monkeypatch):
    monkeypatch.setattr(rows, "_user_label", lambda db, uid: None)
    out = rows._intake(None, _request(ai_triage={"status": "pending"}), run=None)
    assert [r["name"] for r in out] == ["Request filed", "AI triage", "Owner assigned", "Workflow chosen"]
    assert [r["status"] for r in out] == ["done", "waiting", "waiting", "waiting"]


def test_a_started_workflow_is_the_chosen_one(monkeypatch):
    monkeypatch.setattr(rows, "_user_label", lambda db, uid: "Erin")
    run = SimpleNamespace(flow_id="f1", flow_name="Master Services Agreement", steps=[{}] * 8, created_at=NOW)
    chosen = rows._intake(None, _request(assigned_to_user_id="u2"), run=run)[-1]
    assert chosen["status"] == "done" and "8 steps" in chosen["detail"]


def test_closed_says_when_the_contract_ends():
    live = rows._closed(SimpleNamespace(lifecycle_stage="active", expiration_date=date(2029, 9, 30)))
    assert live[0]["status"] == "planned" and "30 Sep 2029" in live[0]["detail"]
    assert rows._closed(SimpleNamespace(lifecycle_stage="closed", expiration_date=None))[0]["status"] == "done"


def test_an_uploaded_contract_skips_the_draft_step(monkeypatch):
    """Its document already exists: re-drafting would replace what was uploaded."""
    monkeypatch.setattr(service, "_enter_step_stage", lambda *a, **k: None)
    sr = SimpleNamespace(status="pending", note=None)
    run = SimpleNamespace(contract_id="c1", request_id="r1")
    out = asyncio.run(service._execute_step(None, run=run, step={"type": "clm_draft", "name": "Draft"}, sr=sr, actor=None))
    assert out == "advance" and sr.status == "skipped"
