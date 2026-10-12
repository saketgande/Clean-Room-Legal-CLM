"""A request filed on an agreement form is decided by the form.

Two failures this guards: the form's value and dates never reached the contract
(the requester typed INR 45,00,000 and an end date; the contract had neither),
and the model re-triaged a fully stated form — calling an MSA "Vendor", holding
it for "missing" negotiable terms, and so never starting its workflow.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.intake import drafting, flow_agent, triage_agent


def _request(**fv):
    return SimpleNamespace(id="req-1", org_id="org-1", priority="High", department=None,
                           type_label="New agreement", counterparty_id="cp-1", legal_entity_id="le-1",
                           field_values={"request_form": "new_agreement", **fv})


def test_form_facts_are_typed_for_the_contract_row():
    """Value, currency and dates come off the form; alternate date keys count and junk is ignored."""
    facts = drafting.request_facts({"value": "4500000", "currency": "USD", "start_date": "2026-10-01",
                                    "services_end": "2027-09-30", "end_date": "not a date"})
    assert facts == {"value_amount": Decimal(4500000), "currency": "USD",
                     "effective_date": date(2026, 10, 1), "expiration_date": date(2027, 9, 30)}
    assert drafting.request_facts({"value": "10"})["currency"] == "INR"
    assert drafting.request_facts({"value": "banana"}) == {}


def test_form_facts_land_on_the_contract_but_never_over_a_person():
    """The form beats the AI, a person beats the form; the auto-review flag rides along."""
    contract = SimpleNamespace(counterparty_id=None, legal_entity_id=None, value_amount=Decimal(1),
                               effective_date=None, expiration_date=None, currency=None,
                               metadata_json={"field_sources": {"value_amount": "user"}})
    drafting.apply_request_facts(contract, _request(value="4500000", end_date="2027-09-30"))
    assert contract.value_amount == Decimal(1)
    assert contract.expiration_date == date(2027, 9, 30)
    assert contract.counterparty_id == "cp-1"
    meta = contract.metadata_json
    assert meta["field_sources"] == {"value_amount": "user", "currency": "form", "expiration_date": "form"}
    assert meta["auto_review_pending"] is True


def test_the_msa_template_states_the_form_s_term_and_value():
    text = drafting.render_document("msa", company="Us", counterparty="Them", effective="2026-10-01",
                                    fields={"value": "4500000", "end_date": "2027-09-30", "payment_terms": "60 days",
                                            "term": "Renews automatically", "renewal_term": "1 year", "notice_days": "90 days"})
    assert "continues until 30 September 2027, and then renews automatically" in text
    assert "90 days' written notice of non-renewal" in text
    assert "undisputed invoices within 60 days" in text
    assert "INR 4,500,000.00" in text
    assert "continues until terminated" in drafting.render_document(
        "msa", company="Us", counterparty="Them", effective="2026-10-01", fields={})


def test_a_form_request_is_triaged_by_the_form_not_the_model(monkeypatch):
    """No guessing: the form's category, the requester's priority, nothing held
    for info, and the workflow set up for its type ("Used for"), so it auto-starts."""
    monkeypatch.setattr(flow_agent, "used_for_suggestion", lambda db, r: {
        "flow_id": "wf-msa", "flow_name": "MSA", "confidence": 1.0, "needs_human": False, "source": "used_for"})
    monkeypatch.setattr(triage_agent, "aegis_read", lambda db, r, claude_client=None: {"summary": "note"})

    msa = triage_agent.triage(None, _request(agreement_type="Services (MSA)"))
    assert msa["category"] == "Contract Review" and msa["source"] == "form"
    assert msa["needs_info"] is False
    assert msa["understanding"]["urgency"] == "High"
    assert msa["flow_suggestion"]["confidence"] == 1.0
    assert msa["read"] == {"summary": "note"}
    nda = triage_agent.triage(None, _request(agreement_type="NDA"))
    assert nda["category"] == "NDA"


def test_a_form_request_with_no_workflow_waits_for_a_person(monkeypatch):
    """Nothing set up for its type: no word match or catch-all picks one by accident."""
    monkeypatch.setattr(flow_agent, "used_for_suggestion", lambda db, r: None)
    monkeypatch.setattr(triage_agent, "aegis_read", lambda db, r, claude_client=None: None)
    out = triage_agent.triage(None, _request(agreement_type="Selling to a customer"))
    assert out["flow_suggestion"]["flow_id"] is None and out["flow_suggestion"]["needs_human"] is True


def test_the_read_drops_mismatches_that_are_not_mismatches():
    """Live, the model filed "value: INR 45 lakh — these agree" as a mismatch."""
    items = [
        {"field": "value", "form_says": "INR 4,500,000", "text_says": "INR 45 lakh", "same": True},
        {"field": "term", "form_says": "2027-09-30", "text_says": "", "same": False},
        {"field": "type", "form_says": "MSA", "text_says": "NDA", "same": False},
        "not a dict",
    ]
    assert triage_agent.real_mismatches(items) == ["type: the form says MSA; the text says NDA"]


def test_a_finished_change_request_says_what_changes_on_the_contract():
    """An approved amendment only recorded the new value and date on the request;
    the contract kept the old ones. Only the answers that change is asked for count."""
    cols, _ = drafting._change_facts({"request_form": "amendment", "what_changes": ["Value"],
                                      "new_value": 5200000, "currency": "INR", "new_end_date": None})
    assert cols == {"value_amount": Decimal(5200000), "currency": "INR"}
    cols, notes = drafting._change_facts({"request_form": "termination", "termination_date": "2027-01-31",
                                          "grounds": "For convenience"})
    assert cols == {"expiration_date": date(2027, 1, 31)} and notes["terminated"]["on"] == "2027-01-31"
    cols, _ = drafting._change_facts({"request_form": "novation", "incoming_party": "Initech Software Ltd"})
    assert cols == {"counterparty_name": "Initech Software Ltd"}


def test_a_confident_model_pick_never_starts_a_workflow(monkeypatch):
    """The model's 0.92 workflow pick used to start the run by itself, recorded as the
    requester's own start; only an admin's "Used for" pick may start without a person."""
    from app.intake.service import IntakeService
    from app.workflows import service as workflows_service

    started: list[str] = []

    async def fake_start(db, *, actor, request, flow):
        started.append(flow.id)

    monkeypatch.setattr(workflows_service, "start_flow", fake_start)
    db = SimpleNamespace(get=lambda model, fid: SimpleNamespace(id=fid, org_id="org-1"),
                         commit=lambda: None, rollback=lambda: None)
    fs = {"flow_id": "wf-1", "confidence": 0.92, "needs_human": False, "source": "llm"}
    r = SimpleNamespace(id="req-1", org_id="org-1", ai_triage={"flow_suggestion": fs})

    IntakeService(db)._maybe_autostart_workflow(r, SimpleNamespace(id="u-1"))
    assert started == []
    fs["source"] = "used_for"
    IntakeService(db)._maybe_autostart_workflow(r, SimpleNamespace(id="u-1"))
    assert started == ["wf-1"]


def test_the_ai_urgency_is_a_suggestion_not_the_priority(monkeypatch):
    """Free-text triage overwrote the requester's priority (and an escalated notice's
    'Critical') with the model's urgency, silently; it must stay the requester's, with
    the AI's read kept beside it."""
    from app.intake.service import IntakeService

    monkeypatch.setattr(triage_agent, "triage", lambda db, r: {"understanding": {"urgency": "High"}})
    r = SimpleNamespace(priority="Low", ai_triage=None)
    IntakeService(None)._apply_ai_triage(r)
    assert r.priority == "Low"
    assert r.ai_triage["understanding"]["urgency"] == "High"
