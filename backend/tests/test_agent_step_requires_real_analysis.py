"""AGENT-02: an AI step whose only "analysis" was the keyword classifier escalates
to a human; the classifier's confidence must never clear a review step."""

import asyncio
from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.workflows import service


class FakeDB:
    def __init__(self, request):
        self.request = request

    def commit(self):  # the engine commits before an agent's slow work
        pass

    def get(self, _model, _key):
        return self.request


def test_vendor_ticket_with_no_screening_escalates_even_if_it_says_confidential():
    request = SimpleNamespace(
        id="req-1", type_label="Vendor onboarding",
        description="New vendor for confidential data processing; please onboard the supplier.",
        screening=None, ai_triage={}, field_values={}, priority="Medium",
    )
    step = {"type": "ai_task", "name": "AI Sanctions & Debarment Screening",
            "config": {"agent": "vendor-intake-agent", "escalate_role": "legal_ops",
                       "escalate_below_confidence": 0.85}}
    sr = SimpleNamespace(status="running", note=None, result=None, updated_at=None)
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id=None, flow_name="Vendor DD")
    outcome = asyncio.run(service._execute_step(FakeDB(request), run=run, step=step, sr=sr,
                                                actor=SimpleNamespace(id="u-1")))
    assert outcome == "wait"
    assert sr.status == "waiting_human"
    assert "No analysis ran" in sr.note
    assert sr.result["source"] == "regex"
