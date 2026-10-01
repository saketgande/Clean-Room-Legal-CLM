"""A workflow's "Used for" picks it for a request type (and agreement-type answer).

Before this, workflows were picked by guessing from the request's words: a New
agreement for an MSA ("New agreement Request") matched no word and fell to the
catch-all ladder, which never drafts an MSA. "Used for" is the admin's decision
and must beat the word match and the AI's pick.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401
from app.intake import drafting
from app.workflows import service


def _req(agreement_type=None, label="New agreement Request", form="new_agreement"):
    fv = {"request_form": form}
    if agreement_type:
        fv["agreement_type"] = agreement_type
    return SimpleNamespace(org_id="org-1", type_label=label, description="",
                           priority="Medium", department=None, field_values=fv)


def _flow(name, criteria, order=50):
    return SimpleNamespace(id=name, name=name, criteria=criteria, steps=[], eval_order=order)


LADDER = _flow("Ladder", {}, 100)
MSA = _flow("MSA", {"used_for": [{"form": "new_agreement", "agreement_type": "Master Services Agreement"}]}, 60)
ANY_NEW = _flow("Any new agreement", {"used_for": [{"form": "new_agreement", "agreement_type": None}]}, 10)


def test_the_agreement_type_answer_beats_a_workflow_for_the_whole_type():
    """Specific wins even when the general one has the better eval_order."""
    assert service._pick_used_for([ANY_NEW, MSA, LADDER], _req("Master Services Agreement")).name == "MSA"
    assert service._pick_used_for([ANY_NEW, MSA, LADDER], _req("SaaS or software licence")).name == "Any new agreement"


def test_used_for_ignores_other_forms():
    assert service._pick_used_for([MSA], _req("Master Services Agreement", form="sow")) is None
    assert service._pick_used_for([MSA], _req("Master Services Agreement", form=None)) is None


def test_a_used_for_only_workflow_is_never_the_catch_all(monkeypatch):
    """Its criteria have no words, which the old matcher read as "match everything"."""
    monkeypatch.setattr(service, "_enabled_flows", lambda db, org_id: [MSA, LADDER])
    other = _req(None, label="Trademark", form=None)
    assert service.select_flow(None, request=other).name == "Ladder"
    assert service.select_flow(None, request=_req("Master Services Agreement")).name == "MSA"


def test_the_new_agreement_answer_decides_what_gets_drafted():
    assert drafting.resolve_doc_type(_req("Services (MSA)")) == "msa"
    assert drafting.resolve_doc_type(_req("NDA")) == "nda"
    assert drafting.resolve_doc_type(_req("Selling to a customer")) is None  # Legal drafts it
    assert drafting.resolve_doc_type(_req(None, form="sow")) == "msa"


def test_cancelling_a_request_is_not_a_document_to_draft():
    """Cancellation asks the same "Agreement type" question; it must not draft an MSA."""
    cancel = _req("Master Services Agreement", label="Cancel a request in the CLM Request",
                  form="cancellation")
    assert drafting.resolve_doc_type(cancel) is None


def test_two_workflows_cannot_claim_the_same_request(monkeypatch):
    class DB:
        def scalars(self, _):
            return SimpleNamespace(all=lambda: [SimpleNamespace(id="other", name="MSA", criteria=MSA.criteria)])

    mine = SimpleNamespace(id="mine", org_id="org-1", criteria=MSA.criteria)
    with pytest.raises(HTTPException) as exc:
        service._refuse_clash(DB(), mine)
    assert exc.value.status_code == 409 and "MSA" in exc.value.detail


def test_the_model_s_workflow_pick_cannot_override_used_for(monkeypatch):
    """The live triage (_to_triage) wrote the model's flow pick last, so on the
    real LLM path "Used for" was silently ignored and autostart followed the model."""
    from app.intake import flow_agent, triage_agent

    monkeypatch.setattr(triage_agent, "_triage", lambda db, r, claude_client=None: {"category": "Commercial", "flow_suggestion": {"flow_id": "model-pick", "confidence": 0.9}})
    monkeypatch.setattr(flow_agent, "used_for_suggestion", lambda db, r: {"flow_id": "msa", "source": "used_for", "confidence": 1.0})
    # Free text (no form) — a form request never reaches the model's pick at all.
    out = triage_agent.triage(None, _req("Master Services Agreement", form=None))
    assert out["flow_suggestion"]["flow_id"] == "msa" and out["category"] == "Commercial"


# --- several workflows per agreement type, told apart by their conditions -----

def _typed(name, conditions, order=50, kind="Services (MSA)"):
    return _flow(name, {"used_for": [{"form": "new_agreement", "agreement_type": kind}], "conditions": conditions}, order)


LOW = _typed("MSA low", [{"field": "value", "op": "under", "value": 1000000, "currency": "INR"}], 10)
MID = _typed("MSA standard", [{"field": "value", "op": "between", "value": 1000000, "value2": 5000000, "currency": "INR"}], 20)
HIGH = _typed("MSA high", [{"field": "value", "op": "at_least", "value": 5000000, "currency": "INR"}], 30)
DATA = _typed("MSA with personal data", [{"field": "value", "op": "under", "value": 1000000, "currency": "INR"},
                                         {"field": "personal_data", "op": "is", "value": "Yes"}], 40)


def _msa(**fv):
    r = _req("Services (MSA)")
    r.field_values.update(fv)
    return r


def test_the_value_range_picks_the_workflow():
    flows = [LOW, MID, HIGH]
    assert service._pick_used_for(flows, _msa(value=500000)).name == "MSA low"
    assert service._pick_used_for(flows, _msa(value=1000000)).name == "MSA standard"  # a range includes its start
    assert service._pick_used_for(flows, _msa(value=4500000)).name == "MSA standard"
    assert service._pick_used_for(flows, _msa(value=5000000)).name == "MSA high"


def test_a_blank_value_or_another_currency_meets_no_value_condition():
    """A request with no value must not slip into the low-value path, and 500,000
    USD is not 500,000 INR."""
    flows = [LOW, MID, HIGH]
    assert service._pick_used_for(flows, _msa()) is None
    assert service._pick_used_for(flows, _msa(value=500000, currency="USD")) is None


def test_more_conditions_win_when_two_fit():
    assert service._pick_used_for([LOW, DATA], _msa(value=500000, personal_data="Yes")).name == "MSA with personal data"
    assert service._pick_used_for([LOW, DATA], _msa(value=500000, personal_data="No")).name == "MSA low"


def test_a_form_request_nothing_fits_gets_no_workflow(monkeypatch):
    """No catch-all for form requests: the untyped Ladder must not take it."""
    monkeypatch.setattr(service, "_enabled_flows", lambda db, org_id: [LOW, LADDER])
    assert service.select_flow(None, request=_msa(value=9000000)) is None
    assert service.select_flow(None, request=_req(None, label="Trademark", form=None)).name == "Ladder"


def test_saving_refuses_conditions_that_could_never_be_met():
    """An NDA is never asked a value; a range must go low to high."""
    import pytest
    from fastapi import HTTPException

    nda = [{"form": "new_agreement", "agreement_type": "NDA"}]
    with pytest.raises(HTTPException):
        service._clean_conditions([{"field": "nonexistent", "op": "is", "value": "x"}], nda)
    with pytest.raises(HTTPException):
        service._clean_conditions([{"field": "value", "op": "between", "value": 50, "value2": 10}], nda)
    ok = service._clean_conditions([{"field": "value", "op": "under", "value": "10,00,000"}], nda)
    assert ok == [{"field": "value", "op": "under", "value": 1000000.0, "currency": "INR"}]


def test_needed_within_days_condition():
    """Urgency routing ("Needed by is within 7 days"): a request with no date must
    not slip into the fast track, and the condition only fits a date question."""
    from datetime import timedelta

    from app.core.database import utcnow

    within = [{"field": "needed_by", "op": "within_days", "value": 7}]
    on = lambda days: SimpleNamespace(field_values={"needed_by": (utcnow().date() + timedelta(days=days)).isoformat()})
    assert service.conditions_hold(within, on(7))
    assert service.conditions_hold(within, on(-1))  # already late is the most urgent
    assert not service.conditions_hold(within, on(8))
    assert not service.conditions_hold(within, SimpleNamespace(field_values={}))

    nda = [{"form": "new_agreement", "agreement_type": "NDA"}]
    assert service._clean_conditions([{**within[0], "value": "7"}], nda) == within
    for bad in ({"field": "paper", "op": "within_days", "value": 7},
                {"field": "needed_by", "op": "is", "value": "2026-10-01"},
                {"field": "needed_by", "op": "within_days", "value": -1}):
        with pytest.raises(HTTPException):
            service._clean_conditions([bad], nda)
