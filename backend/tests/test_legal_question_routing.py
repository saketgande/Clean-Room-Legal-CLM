"""General legal questions: routing pin, litigation exception, never drafted.

Decided 2026-10-05: "Legal Question — General" requests always get the General
Legal Question workflow (a "Used for" pin by request type), except ones triage
marks as litigation, which keep the litigation route. A question that mentions a
vendor or an MSA must never turn into an "Approve & draft" job.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (registers every mapper)
from app.intake import triage_agent
from app.intake.drafting import resolve_doc_type
from app.intake.models import IntakeRequest
from app.workflows import service as wf
from app.workflows.builtin import BUILTIN_FLOWS

LQ = "Legal Question — General"
PIN = {"used_for": [{"type_label": LQ}]}


def _req(type_label=LQ, *, category=None, form=None, description=""):
    fv = {"request_form": form} if form else None
    return SimpleNamespace(type_label=type_label, description=description, field_values=fv,
                           ai_triage={"category": category} if category else None, priority="Medium",
                           department=None)


def test_type_pin_claims_a_legal_question():
    assert wf._used_for_rank(PIN, _req()) == 1


def test_type_pin_ignores_other_types_and_form_requests():
    assert wf._used_for_rank(PIN, _req("Contract Question")) is None
    assert wf._used_for_rank(PIN, _req(form="new_agreement")) is None


def test_type_pin_yields_to_litigation():
    assert wf._used_for_rank(PIN, _req(category="Litigation")) is None


def test_form_pins_are_unchanged():
    nda = {"used_for": [{"form": "new_agreement", "agreement_type": "NDA"}]}
    r = _req("New agreement", form="new_agreement")
    r.field_values["agreement_type"] = "NDA"
    assert wf._used_for_rank(nda, r) == 2


def test_builtin_general_legal_question_is_pinned():
    spec = next(s for s in BUILTIN_FLOWS if s["name"] == "General Legal Question")
    assert spec["used_for"] == [{"type_label": LQ}]


def test_saving_a_workflow_keeps_a_known_type_pin_and_rejects_an_unknown_one():
    kept = wf._clean_criteria(None, "org", {"used_for": [{"type_label": "legal question — general"}]})
    assert kept["used_for"] == [{"type_label": LQ}]
    with pytest.raises(HTTPException) as exc:
        wf._clean_criteria(None, "org", {"used_for": [{"type_label": "Made-up type"}]})
    assert exc.value.status_code == 422


def test_triage_drops_the_pin_for_a_litigation_question(monkeypatch):
    pinned = {"flow_id": "f-general", "flow_name": "General Legal Question", "source": "used_for"}
    monkeypatch.setattr(triage_agent, "form_key", lambda r: None)
    monkeypatch.setattr("app.intake.flow_agent.used_for_suggestion", lambda db, r: dict(pinned))

    monkeypatch.setattr(triage_agent, "_triage", lambda db, r, claude_client=None: {
        "category": "Litigation", "flow_suggestion": {"flow_id": "f-lit", "source": "llm"}})
    assert triage_agent.triage(None, _req())["flow_suggestion"]["flow_id"] == "f-lit"

    monkeypatch.setattr(triage_agent, "_triage", lambda db, r, claude_client=None: {
        "category": "Contract Review", "flow_suggestion": {"flow_id": None, "source": "llm"}})
    assert triage_agent.triage(None, _req())["flow_suggestion"]["flow_id"] == "f-general"


@pytest.mark.parametrize("text", [
    "Our vendor missed three deliveries — can we terminate?",
    "Globex wants to end our MSA early; do we owe a refund?",
])
def test_a_legal_question_is_never_draftable(text):
    q = IntakeRequest(type_label=LQ, description=text, field_values=None)
    assert resolve_doc_type(q) is None


def test_a_real_vendor_request_is_still_draftable():
    r = IntakeRequest(type_label="Vendor Due Diligence", description="Onboard a new supplier", field_values=None)
    assert resolve_doc_type(r) == "vendor"
